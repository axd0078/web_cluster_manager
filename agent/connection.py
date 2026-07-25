from __future__ import annotations

import asyncio
import json
import logging
import platform
import socket
import ssl

import websockets

try:
    from .config import AgentConfig
except ImportError:  # Script execution from the agent directory.
    from config import AgentConfig

logger = logging.getLogger("agent.connection")


class AgentCredentialRejected(RuntimeError):
    pass


class AgentConnection:
    def __init__(self, config: AgentConfig):
        self.config = config
        self._ws = None
        self._running = False
        self._handlers: list = []
        self._connect_handlers: list = []
        self._disconnect_handlers: list = []

    def on_message(self, handler):
        self._handlers.append(handler)

    def on_connect(self, handler):
        self._connect_handlers.append(handler)

    def on_disconnect(self, handler):
        self._disconnect_handlers.append(handler)

    async def connect(self):
        headers = {"Authorization": f"Bearer {self.config.agent_token}"}
        ssl_context = None
        if self.config.server_url.startswith("wss://"):
            ssl_context = ssl.create_default_context(cafile=self.config.ca_cert or None)
        kwargs = {
            "ping_interval": self.config.heartbeat_interval,
            "ping_timeout": 10,
            "ssl": ssl_context,
            "max_size": 2_000_000,
        }
        try:
            self._ws = await websockets.connect(
                self.config.server_url, additional_headers=headers, **kwargs,
            )
        except TypeError:
            # websockets 13 legacy compatibility.
            self._ws = await websockets.connect(
                self.config.server_url, extra_headers=headers, **kwargs,
            )
        logger.info("Connected to %s", self.config.server_url)
        for handler in self._connect_handlers:
            await handler()

    async def receive_loop(self):
        while self._running and self._ws:
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                continue
            for handler in self._handlers:
                try:
                    await handler(msg)
                except Exception:
                    logger.exception("Handler error")

    async def send(self, data: dict) -> bool:
        if not self._ws:
            return False
        try:
            await self._ws.send(json.dumps(data))
            return True
        except Exception:
            logger.exception("Send error")
            return False

    async def run_forever(self):
        self._running = True
        backoff = 1
        while self._running:
            try:
                await self.connect()
                backoff = 1
                await self.receive_loop()
            except websockets.ConnectionClosed as exc:
                if exc.code == 4001:
                    raise AgentCredentialRejected("Agent credential rejected") from exc
                logger.warning("Connection closed (%s), reconnecting in %ss", exc.code, backoff)
            except (OSError, ssl.SSLError, asyncio.TimeoutError) as exc:
                logger.warning("Connection failed (%s), reconnecting in %ss", exc, backoff)
            finally:
                self._ws = None
                for handler in self._disconnect_handlers:
                    try:
                        await handler()
                    except Exception:
                        logger.exception("Disconnect handler error")
            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def get_os_info() -> str:
    return f"{platform.system()} {platform.release()} {platform.version()}"
