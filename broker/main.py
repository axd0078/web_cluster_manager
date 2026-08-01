#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import platform
import signal
import ssl
import struct
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.terminal_crypto import (  # noqa: E402
    TerminalCipher,
    TerminalProtocolError,
    new_ephemeral_key,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [broker] %(levelname)s: %(message)s",
)
logger = logging.getLogger("broker")

INPUT_LIMIT = 4 * 1024
OUTPUT_LIMIT = 32 * 1024


def _read_secret(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip() if path else ""


@dataclass
class BrokerConfig:
    server_url: str
    node_id: str
    token: str
    ca_cert: str = ""

    @classmethod
    def from_args(cls, args) -> "BrokerConfig":
        token = os.getenv("WCM_BROKER_TOKEN", "")
        token_file = os.getenv("WCM_BROKER_TOKEN_FILE", "")
        if token_file:
            token = _read_secret(token_file)
        config = cls(
            server_url=args.server or os.getenv(
                "WCM_BROKER_SERVER_URL",
                "wss://localhost/ws/broker",
            ),
            node_id=args.node_id or os.getenv("WCM_BROKER_NODE_ID", ""),
            token=token,
            ca_cert=args.ca_cert or os.getenv("WCM_CA_CERT", ""),
        )
        if not config.node_id or not config.token:
            raise ValueError("WCM_BROKER_NODE_ID and a Broker credential are required")
        try:
            uuid.UUID(config.node_id)
        except ValueError as exc:
            raise ValueError("WCM_BROKER_NODE_ID must be a registered node UUID") from exc
        parsed = urlsplit(config.server_url)
        if (
            parsed.scheme not in {"ws", "wss"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/ws/broker"
            or (
                parsed.scheme == "ws"
                and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            )
        ):
            raise ValueError("Broker requires WSS except on loopback")
        return config


class LinuxPty:
    def __init__(self, process, master_fd: int):
        self.process = process
        self.master_fd = master_fd

    @classmethod
    async def spawn(cls, rows: int, cols: int) -> "LinuxPty":
        import fcntl
        import pty
        import subprocess
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        env = {
            "HOME": "/root",
            "LANG": os.getenv("LANG", "C.UTF-8"),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "TERM": "xterm-256color",
        }
        process = subprocess.Popen(
            ["/bin/bash", "--noprofile", "--norc"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            start_new_session=True,
            env=env,
        )
        os.close(slave)
        return cls(process, master)

    async def read(self) -> bytes:
        return await asyncio.to_thread(os.read, self.master_fd, OUTPUT_LIMIT)

    async def write(self, data: bytes) -> None:
        await asyncio.to_thread(os.write, self.master_fd, data)

    async def resize(self, rows: int, cols: int) -> None:
        import fcntl
        import termios

        await asyncio.to_thread(
            fcntl.ioctl,
            self.master_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, cols, 0, 0),
        )

    async def wait(self) -> int:
        return await asyncio.to_thread(self.process.wait)

    async def terminate(self) -> None:
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                await asyncio.wait_for(asyncio.to_thread(self.process.wait), timeout=3)
            except (ProcessLookupError, asyncio.TimeoutError):
                if self.process.poll() is None:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    await asyncio.to_thread(self.process.wait)
        try:
            os.close(self.master_fd)
        except OSError:
            pass


class WindowsPty:
    def __init__(self, process):
        self.process = process

    @classmethod
    async def spawn(cls, rows: int, cols: int) -> "WindowsPty":
        from winpty import PtyProcess

        process = await asyncio.to_thread(
            PtyProcess.spawn,
            (
                "powershell.exe -NoLogo -NoProfile -NoExit -Command "
                "\"chcp.com 65001 | Out-Null; "
                "[Console]::InputEncoding=[Console]::OutputEncoding="
                "[System.Text.UTF8Encoding]::new($false)\""
            ),
            dimensions=(rows, cols),
        )
        return cls(process)

    async def read(self) -> bytes:
        text = await asyncio.to_thread(self.process.read, OUTPUT_LIMIT)
        return text.encode("utf-8", errors="replace")

    async def write(self, data: bytes) -> None:
        await asyncio.to_thread(self.process.write, data.decode("utf-8", errors="replace"))

    async def resize(self, rows: int, cols: int) -> None:
        await asyncio.to_thread(self.process.setwinsize, rows, cols)

    async def wait(self) -> int:
        while await asyncio.to_thread(self.process.isalive):
            await asyncio.sleep(0.1)
        return int(getattr(self.process, "exitstatus", -1) or 0)

    async def terminate(self) -> None:
        if await asyncio.to_thread(self.process.isalive):
            await asyncio.to_thread(self.process.terminate, True)


async def spawn_pty(rows: int, cols: int):
    if os.name == "nt":
        return await WindowsPty.spawn(rows, cols)
    return await LinuxPty.spawn(rows, cols)


@dataclass
class BrokerSession:
    session_id: str
    node_id: str
    cipher: TerminalCipher
    pty: object | None = None
    task: asyncio.Task | None = None
    open_timeout_task: asyncio.Task | None = None
    send_lock: asyncio.Lock | None = None


class BrokerRuntime:
    def __init__(self, config: BrokerConfig):
        self.config = config
        self.ws = None
        self.sessions: dict[str, BrokerSession] = {}

    async def send(self, message: dict) -> bool:
        if self.ws is None:
            return False
        try:
            await self.ws.send(json.dumps(message, separators=(",", ":")))
            return True
        except Exception:
            return False

    async def handle(self, message: dict) -> None:
        message_type = str(message.get("type") or "")
        session_id = str(message.get("session_id") or "")
        if message_type == "terminal_key_exchange":
            await self._exchange(message)
            return
        session = self.sessions.get(session_id)
        if session is None:
            return
        try:
            frame_type, payload = session.cipher.decrypt(message)
            if frame_type == "terminal_open":
                await self._open(session, payload)
            elif frame_type == "terminal_data":
                data = base64.b64decode(payload.get("data", ""), validate=True)
                if len(data) > INPUT_LIMIT or session.pty is None:
                    raise TerminalProtocolError("invalid terminal input")
                await session.pty.write(data)
            elif frame_type == "terminal_resize":
                if session.pty is not None:
                    rows = max(2, min(int(payload.get("rows", 24)), 200))
                    cols = max(10, min(int(payload.get("cols", 80)), 400))
                    await session.pty.resize(rows, cols)
            elif frame_type == "terminal_close":
                await self.close(session_id)
            else:
                raise TerminalProtocolError("unsupported terminal frame")
        except (TerminalProtocolError, ValueError, TypeError):
            await self.close(session_id)

    async def _exchange(self, message: dict) -> None:
        session_id = str(message.get("session_id") or "")
        if (
            message.get("version") != 3
            or message.get("mode") != "admin"
            or message.get("node_id") != self.config.node_id
            or not session_id
            or session_id in self.sessions
            or self.sessions
        ):
            return
        try:
            private, public = new_ephemeral_key()
            cipher = TerminalCipher.target(
                private,
                str(message.get("public_key") or ""),
                session_id=session_id,
                node_id=self.config.node_id,
                mode="admin",
            )
        except TerminalProtocolError:
            return
        session = BrokerSession(
            session_id=session_id,
            node_id=self.config.node_id,
            cipher=cipher,
            send_lock=asyncio.Lock(),
        )
        self.sessions[session_id] = session
        session.open_timeout_task = asyncio.create_task(
            self._expire_unopened(session_id)
        )
        await self.send({
            "type": "terminal_key_exchange",
            "version": 3,
            "session_id": session_id,
            "node_id": self.config.node_id,
            "mode": "admin",
            "public_key": public,
        })

    async def _open(self, session: BrokerSession, payload: dict) -> None:
        if session.task is not None:
            raise TerminalProtocolError("terminal already open")
        if session.open_timeout_task is not None:
            session.open_timeout_task.cancel()
            session.open_timeout_task = None
        if any(key in payload for key in ("command", "argv", "cwd", "env", "profile")):
            raise TerminalProtocolError("privileged shell parameters rejected")
        rows = max(2, min(int(payload.get("rows", 24)), 200))
        cols = max(10, min(int(payload.get("cols", 80)), 400))
        session.pty = await spawn_pty(rows, cols)
        session.task = asyncio.create_task(self._stream(session))

    async def _encrypted(self, session, frame_type, payload) -> bool:
        assert session.send_lock is not None
        async with session.send_lock:
            return await self.send(session.cipher.encrypt(frame_type, payload))

    async def _stream(self, session: BrokerSession) -> None:
        exit_code = -1
        read_task: asyncio.Task | None = None
        wait_task: asyncio.Task | None = None
        try:
            async def reader():
                while True:
                    chunk = await session.pty.read()
                    if not chunk:
                        return
                    if len(chunk) > OUTPUT_LIMIT:
                        chunk = chunk[:OUTPUT_LIMIT]
                    if not await self._encrypted(session, "terminal_data", {
                        "data": base64.b64encode(chunk).decode("ascii"),
                    }):
                        return

            read_task = asyncio.create_task(reader())
            wait_task = asyncio.create_task(session.pty.wait())
            done, _ = await asyncio.wait(
                {read_task, wait_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if read_task in done and wait_task not in done:
                await session.pty.terminate()
            exit_code = await wait_task
            await read_task
            await self._encrypted(session, "terminal_exit", {
                "exit_code": exit_code,
                "reason": "process exited",
            })
        except (OSError, EOFError):
            try:
                await self._encrypted(session, "terminal_error", {
                    "message": "privileged terminal process failed",
                })
            except TerminalProtocolError:
                pass
        finally:
            for task in (read_task, wait_task):
                if task is not None and not task.done():
                    task.cancel()
            if session.pty is not None:
                await session.pty.terminate()
            await self._forget(session.session_id)

    async def _forget(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            timeout_task = session.open_timeout_task
            if (
                timeout_task is not None
                and timeout_task is not asyncio.current_task()
                and not timeout_task.done()
            ):
                timeout_task.cancel()
            session.cipher.destroy()

    async def _expire_unopened(self, session_id: str) -> None:
        try:
            await asyncio.sleep(10)
            session = self.sessions.get(session_id)
            if session is not None and session.task is None:
                await self.close(session_id)
        except asyncio.CancelledError:
            pass

    async def close(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            return
        if session.pty is not None:
            await session.pty.terminate()
        task = session.task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._forget(session_id)

    async def shutdown(self) -> None:
        for session_id in list(self.sessions):
            await self.close(session_id)

    async def run(self) -> None:
        backoff = 1
        ssl_context = None
        if self.config.server_url.startswith("wss://"):
            ssl_context = ssl.create_default_context(cafile=self.config.ca_cert or None)
        while True:
            headers = {"Authorization": f"Bearer {self.config.token}"}
            kwargs = {
                "ssl": ssl_context,
                "ping_interval": 20,
                "ping_timeout": 10,
                "max_size": 100_000,
            }
            try:
                try:
                    async with websockets.connect(
                        self.config.server_url,
                        additional_headers=headers,
                        **kwargs,
                    ) as ws:
                        self.ws = ws
                        backoff = 1
                        logger.info("Privileged Broker connected for node %s", self.config.node_id)
                        async for raw in ws:
                            message = json.loads(raw)
                            if isinstance(message, dict):
                                await self.handle(message)
                except TypeError:
                    async with websockets.connect(
                        self.config.server_url,
                        extra_headers=headers,
                        **kwargs,
                    ) as ws:
                        self.ws = ws
                        backoff = 1
                        async for raw in ws:
                            message = json.loads(raw)
                            if isinstance(message, dict):
                                await self.handle(message)
            except (OSError, ssl.SSLError, websockets.ConnectionClosed, json.JSONDecodeError):
                logger.warning("Broker connection lost; retrying in %s seconds", backoff)
            finally:
                self.ws = None
                await self.shutdown()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def main() -> int:
    if os.name == "nt":
        # Installation must run this process as LocalSystem. Refuse a merely
        # elevated administrator process to keep the boundary explicit.
        import ctypes

        import subprocess

        identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        if "S-1-5-18" not in identity:
            logger.error("Windows Broker must run as SYSTEM")
            return 1
    elif hasattr(os, "geteuid") and os.geteuid() != 0:
        logger.error("Linux Broker must run as root")
        return 1
    parser = argparse.ArgumentParser(description="Web Cluster Manager privileged Broker")
    parser.add_argument("--server")
    parser.add_argument("--node-id")
    parser.add_argument("--ca-cert")
    args = parser.parse_args()
    try:
        config = BrokerConfig.from_args(args)
    except (ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 2
    try:
        asyncio.run(BrokerRuntime(config).run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
