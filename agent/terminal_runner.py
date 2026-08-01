from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass

try:
    from .task_profiles import TaskProfileStore
    from .terminal_crypto import (
        TerminalCipher,
        TerminalProtocolError,
        new_ephemeral_key,
    )
except ImportError:
    from task_profiles import TaskProfileStore
    from terminal_crypto import TerminalCipher, TerminalProtocolError, new_ephemeral_key


@dataclass
class LowTerminalSession:
    session_id: str
    node_id: str
    cipher: TerminalCipher
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None
    open_timeout_task: asyncio.Task | None = None
    sent_bytes: int = 0
    send_lock: asyncio.Lock | None = None


class AgentTerminalManager:
    """Runs fixed local argv aliases under the Agent service account."""

    def __init__(
        self,
        node_id_getter,
        profiles: TaskProfileStore,
        send,
        *,
        enabled: bool,
        max_sessions: int = 4,
    ):
        self._node_id_getter = node_id_getter
        self.profiles = profiles
        self.send = send
        self.enabled = enabled
        self.max_sessions = max(1, min(max_sessions, 16))
        self.sessions: dict[str, LowTerminalSession] = {}

    async def handle(self, message: dict) -> None:
        message_type = str(message.get("type") or "")
        session_id = str(message.get("session_id") or "")
        if message_type == "terminal_key_exchange":
            await self._key_exchange(message)
            return
        session = self.sessions.get(session_id)
        if session is None:
            return
        try:
            frame_type, payload = session.cipher.decrypt(message)
            if frame_type == "terminal_open":
                await self._open(session, payload)
            elif frame_type == "terminal_close":
                await self._close(session_id)
            else:
                await self._send_error(session, "unsupported low terminal frame")
                await self._close(session_id)
        except TerminalProtocolError:
            await self._close(session_id)

    async def _key_exchange(self, message: dict) -> None:
        if not self.enabled or len(self.sessions) >= self.max_sessions:
            return
        session_id = str(message.get("session_id") or "")
        node_id = str(message.get("node_id") or "")
        if (
            not session_id
            or session_id in self.sessions
            or node_id != self._node_id_getter()
            or message.get("mode") != "low"
            or message.get("version") != 3
        ):
            return
        try:
            private, public = new_ephemeral_key()
            cipher = TerminalCipher.target(
                private,
                str(message.get("public_key") or ""),
                session_id=session_id,
                node_id=node_id,
                mode="low",
            )
        except TerminalProtocolError:
            return
        session = LowTerminalSession(
            session_id=session_id,
            node_id=node_id,
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
            "node_id": node_id,
            "mode": "low",
            "public_key": public,
        })

    async def _open(self, session: LowTerminalSession, payload: dict) -> None:
        if session.task is not None:
            raise TerminalProtocolError("terminal already opened")
        if session.open_timeout_task is not None:
            session.open_timeout_task.cancel()
            session.open_timeout_task = None
        profile_name = payload.get("profile")
        if not isinstance(profile_name, str):
            raise TerminalProtocolError("terminal profile missing")
        profile = self.profiles.terminal_profiles.get(profile_name)
        if profile is None or self.profiles.format_version != 2:
            await self._send_error(session, "terminal profile unavailable")
            await self._close(session.session_id)
            return
        session.task = asyncio.create_task(self._run(session, profile))

    async def _send_frame(
        self,
        session: LowTerminalSession,
        frame_type: str,
        payload: dict,
    ) -> bool:
        assert session.send_lock is not None
        async with session.send_lock:
            return await self.send(session.cipher.encrypt(frame_type, payload))

    async def _run(self, session: LowTerminalSession, profile) -> None:
        exit_code = -1
        reason = "completed"
        try:
            session.process = await asyncio.create_subprocess_exec(
                *profile.argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=32 * 1024,
            )

            async def stream_output() -> None:
                assert session.process is not None and session.process.stdout is not None
                while True:
                    chunk = await session.process.stdout.read(32 * 1024)
                    if not chunk:
                        return
                    session.sent_bytes += len(chunk)
                    if session.sent_bytes > profile.output_limit:
                        raise OverflowError
                    if not await self._send_frame(session, "terminal_data", {
                        "data": base64.b64encode(chunk).decode("ascii"),
                    }):
                        raise ConnectionError

            reader = asyncio.create_task(stream_output())
            process_waiter = asyncio.create_task(session.process.wait())
            try:
                await asyncio.wait_for(
                    asyncio.gather(process_waiter, reader),
                    timeout=profile.timeout,
                )
                exit_code = process_waiter.result()
            except asyncio.TimeoutError:
                reason = "timeout"
                session.process.kill()
                await session.process.wait()
                await self._send_error(session, "terminal profile timed out")
            except OverflowError:
                reason = "output_limit"
                session.process.kill()
                await session.process.wait()
                await self._send_error(session, "terminal output limit exceeded")
            finally:
                for task in (reader, process_waiter):
                    if not task.done():
                        task.cancel()
                for task in (reader, process_waiter):
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            await self._send_frame(session, "terminal_exit", {
                "exit_code": exit_code,
                "reason": reason,
            })
        except (OSError, ConnectionError):
            await self._send_error(session, "terminal execution failed")
        finally:
            await self._forget(session.session_id)

    async def _send_error(self, session: LowTerminalSession, message: str) -> None:
        try:
            await self._send_frame(session, "terminal_error", {"message": message})
        except (TerminalProtocolError, ConnectionError):
            pass

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
                await self._close(session_id)
        except asyncio.CancelledError:
            pass

    async def _close(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            return
        process = session.process
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
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
            await self._close(session_id)
