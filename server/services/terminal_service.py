from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import update

from config import settings
from core.connection_manager import FrontendPrincipal, manager
from core.terminal_crypto import (
    TerminalCipher,
    TerminalProtocolError,
    new_ephemeral_key,
)
from database import async_session
from models.terminal import TerminalSession, TerminalTicket


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TerminalRuntime:
    session_id: str
    user_id: str
    node_id: str
    mode: str
    profile: str | None
    browser: WebSocket
    principal: FrontendPrincipal
    target_kind: str
    created_monotonic: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    private_key: object | None = None
    cipher: TerminalCipher | None = None
    handshake: asyncio.Future | None = None
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    browser_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    queued_bytes: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    end_reason: str = "closed"
    closing: bool = False
    writer_task: asyncio.Task | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TerminalService:
    def __init__(self) -> None:
        self._sessions: dict[str, TerminalRuntime] = {}
        self._admission_lock = asyncio.Lock()

    def active_sessions(self) -> tuple[TerminalRuntime, ...]:
        return tuple(self._sessions.values())

    def privileged_count(self) -> int:
        return sum(item.mode == "admin" and not item.closing for item in self._sessions.values())

    def privileged_conflict(self, user_id: str, node_id: str) -> bool:
        return any(
            item.mode == "admin"
            and not item.closing
            and (item.user_id == user_id or item.node_id == node_id)
            for item in self._sessions.values()
        )

    async def run_browser(
        self,
        ws: WebSocket,
        principal: FrontendPrincipal,
        ticket: TerminalTicket,
    ) -> None:
        target_kind = "broker" if ticket.mode == "admin" else "agent"
        async with self._admission_lock:
            if ticket.mode == "admin" and (
                self.privileged_count() >= settings.TERMINAL_MAX_GLOBAL_PRIVILEGED
                or self.privileged_conflict(principal.user_id, ticket.node_id)
            ):
                await ws.send_json({
                    "type": "error",
                    "message": "特权终端并发限制已达到",
                })
                await ws.close(code=4008, reason="Privileged terminal limit")
                return
            session = TerminalSession(
                user_id=principal.user_id,
                node_id=ticket.node_id,
                mode=ticket.mode,
                status="opening",
            )
            async with async_session() as db:
                db.add(session)
                await db.commit()
                await db.refresh(session)
            runtime = TerminalRuntime(
                session_id=session.id,
                user_id=principal.user_id,
                node_id=ticket.node_id,
                mode=ticket.mode,
                profile=ticket.profile,
                browser=ws,
                principal=principal,
                target_kind=target_kind,
            )
            runtime.handshake = asyncio.get_running_loop().create_future()
            runtime.writer_task = asyncio.create_task(self._browser_writer(runtime))
            self._sessions[session.id] = runtime
        try:
            await self._open_target(runtime)
            await ws.send_json({
                "type": "ready",
                "session_id": session.id,
                "mode": ticket.mode,
            })
            await self._mark_started(session.id)
            await self._browser_loop(runtime)
        except (ConnectionError, asyncio.TimeoutError, TerminalProtocolError) as exc:
            runtime.end_reason = str(exc)[:200] or "terminal setup failed"
            await self._queue_browser(runtime, {
                "type": "error",
                "message": "终端连接建立失败",
            })
        except WebSocketDisconnect:
            runtime.end_reason = "browser disconnected"
        finally:
            await self.close(runtime.session_id, runtime.end_reason)

    async def _open_target(self, runtime: TerminalRuntime) -> None:
        private, public = new_ephemeral_key()
        runtime.private_key = private
        key_frame = {
            "type": "terminal_key_exchange",
            "version": 3,
            "session_id": runtime.session_id,
            "node_id": runtime.node_id,
            "mode": runtime.mode,
            "public_key": public,
        }
        sent = await self._send_target(runtime, key_frame)
        if not sent:
            raise ConnectionError("privileged Broker offline" if runtime.mode == "admin" else "Agent offline")
        peer_public = await asyncio.wait_for(
            runtime.handshake,
            timeout=settings.TERMINAL_AUTH_TIMEOUT_SECONDS,
        )
        runtime.cipher = TerminalCipher.server(
            private,
            peer_public,
            session_id=runtime.session_id,
            node_id=runtime.node_id,
            mode=runtime.mode,
        )
        runtime.private_key = None
        payload = {"rows": 24, "cols": 80}
        if runtime.mode == "low":
            payload["profile"] = runtime.profile
        if not await self._send_encrypted(runtime, "terminal_open", payload):
            raise ConnectionError("terminal target disconnected")

    async def _browser_loop(self, runtime: TerminalRuntime) -> None:
        while not runtime.ended.is_set():
            now = time.monotonic()
            hard_remaining = (
                runtime.created_monotonic
                + settings.TERMINAL_MAX_LIFETIME_SECONDS
                - now
            )
            idle_remaining = (
                runtime.last_activity
                + settings.TERMINAL_IDLE_TIMEOUT_SECONDS
                - now
            )
            auth_remaining = runtime.principal.remaining_seconds()
            timeout = min(hard_remaining, idle_remaining, auth_remaining)
            if timeout <= 0:
                runtime.end_reason = (
                    "authentication expired" if auth_remaining <= 0
                    else "terminal session timed out"
                )
                break
            receive_task = asyncio.create_task(runtime.browser.receive_text())
            ended_task = asyncio.create_task(runtime.ended.wait())
            done, pending = await asyncio.wait(
                {receive_task, ended_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if not done:
                continue
            if ended_task in done and ended_task.result():
                if not receive_task.done():
                    receive_task.cancel()
                break
            raw = receive_task.result()
            if len(raw.encode("utf-8")) > settings.TERMINAL_INPUT_FRAME_BYTES * 2:
                runtime.end_reason = "browser frame too large"
                break
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            message_type = message.get("type")
            if message_type == "ping":
                await self._queue_browser(runtime, {"type": "pong"})
            elif message_type in {"close", "cancel"}:
                runtime.end_reason = "closed by user"
                break
            elif message_type == "resize":
                if runtime.mode != "admin":
                    continue
                rows = max(2, min(int(message.get("rows", 24)), 200))
                cols = max(10, min(int(message.get("cols", 80)), 400))
                runtime.last_activity = time.monotonic()
                if not await self._send_encrypted(
                    runtime,
                    "terminal_resize",
                    {"rows": rows, "cols": cols},
                ):
                    runtime.end_reason = "Broker disconnected"
                    break
            elif message_type == "input":
                if runtime.mode != "admin":
                    await self._queue_browser(runtime, {
                        "type": "error",
                        "message": "普通终端不接受自由输入",
                    })
                    continue
                data = message.get("data")
                if not isinstance(data, str):
                    continue
                encoded = data.encode("utf-8")
                if len(encoded) > settings.TERMINAL_INPUT_FRAME_BYTES:
                    runtime.end_reason = "input frame too large"
                    break
                runtime.bytes_in += len(encoded)
                runtime.last_activity = time.monotonic()
                if not await self._send_encrypted(runtime, "terminal_data", {
                    "data": base64.b64encode(encoded).decode("ascii"),
                }):
                    runtime.end_reason = "Broker disconnected"
                    break

    async def handle_target_message(
        self,
        node_id: str,
        message: dict,
        *,
        target_kind: str,
    ) -> None:
        session_id = str(message.get("session_id") or "")
        runtime = self._sessions.get(session_id)
        if (
            runtime is None
            or runtime.node_id != node_id
            or runtime.target_kind != target_kind
            or runtime.closing
        ):
            return
        if message.get("type") == "terminal_key_exchange":
            if (
                message.get("version") != 3
                or message.get("mode") != runtime.mode
                or message.get("node_id") != runtime.node_id
                or runtime.handshake is None
                or runtime.handshake.done()
            ):
                await self.close(session_id, "invalid key exchange")
                return
            public_key = message.get("public_key")
            if not isinstance(public_key, str):
                await self.close(session_id, "invalid key exchange")
                return
            runtime.handshake.set_result(public_key)
            return
        if runtime.cipher is None:
            await self.close(session_id, "encrypted frame before key exchange")
            return
        if len(str(message.get("ciphertext") or "")) > (
            settings.TERMINAL_OUTPUT_FRAME_BYTES * 2
        ):
            await self.close(session_id, "target frame too large")
            return
        try:
            frame_type, payload = runtime.cipher.decrypt(message)
        except TerminalProtocolError:
            await self.close(session_id, "terminal frame rejected")
            return
        runtime.last_activity = time.monotonic()
        if frame_type == "terminal_data":
            try:
                data = base64.b64decode(payload.get("data", ""), validate=True)
            except (ValueError, TypeError):
                await self.close(session_id, "invalid terminal output")
                return
            if len(data) > settings.TERMINAL_OUTPUT_FRAME_BYTES:
                await self.close(session_id, "terminal output frame too large")
                return
            runtime.bytes_out += len(data)
            await self._queue_browser(runtime, {
                "type": "data",
                "data": data.decode("utf-8", errors="replace"),
            }, size=len(data))
        elif frame_type == "terminal_exit":
            await self._queue_browser(runtime, {
                "type": "exit",
                "exit_code": int(payload.get("exit_code", -1)),
                "reason": str(payload.get("reason") or "process exited")[:200],
            })
            runtime.end_reason = str(payload.get("reason") or "process exited")[:200]
            runtime.ended.set()
        elif frame_type == "terminal_error":
            await self._queue_browser(runtime, {
                "type": "error",
                "message": str(payload.get("message") or "terminal error")[:200],
            })
        else:
            await self.close(session_id, "unexpected terminal frame")

    async def _queue_browser(
        self,
        runtime: TerminalRuntime,
        message: dict,
        *,
        size: int | None = None,
    ) -> None:
        if runtime.closing:
            return
        message_size = size if size is not None else len(json.dumps(message))
        if runtime.queued_bytes + message_size > settings.TERMINAL_QUEUE_BYTES:
            runtime.end_reason = "browser queue limit exceeded"
            runtime.ended.set()
            return
        runtime.queued_bytes += message_size
        await runtime.browser_queue.put((message, message_size))

    async def _browser_writer(self, runtime: TerminalRuntime) -> None:
        try:
            while True:
                message, size = await runtime.browser_queue.get()
                await runtime.browser.send_json(message)
                runtime.queued_bytes = max(0, runtime.queued_bytes - size)
        except (WebSocketDisconnect, RuntimeError):
            runtime.end_reason = "browser disconnected"
            runtime.ended.set()

    async def _send_encrypted(
        self,
        runtime: TerminalRuntime,
        frame_type: str,
        payload: dict,
    ) -> bool:
        if runtime.cipher is None:
            return False
        async with runtime.send_lock:
            frame = runtime.cipher.encrypt(frame_type, payload)
            return await self._send_target(runtime, frame)

    async def _send_target(self, runtime: TerminalRuntime, frame: dict) -> bool:
        if runtime.target_kind == "broker":
            return await manager.send_to_broker(runtime.node_id, frame)
        return await manager.send_to_agent(runtime.node_id, frame)

    async def close(self, session_id: str, reason: str = "closed") -> None:
        runtime = self._sessions.get(session_id)
        if runtime is None or runtime.closing:
            return
        runtime.closing = True
        runtime.end_reason = (reason or "closed")[:200]
        if runtime.cipher is not None:
            try:
                await self._send_encrypted(runtime, "terminal_close", {})
            except TerminalProtocolError:
                pass
            runtime.cipher.destroy()
        if runtime.handshake is not None and not runtime.handshake.done():
            runtime.handshake.cancel()
        runtime.ended.set()
        writer = runtime.writer_task
        if writer is not None and writer is not asyncio.current_task():
            # Give the final exit/error frame one event-loop turn to flush.
            await asyncio.sleep(0)
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass
        self._sessions.pop(session_id, None)
        async with async_session() as db:
            session = await db.get(TerminalSession, session_id)
            if session is not None:
                session.status = "closed"
                session.bytes_in = runtime.bytes_in
                session.bytes_out = runtime.bytes_out
                session.ended_at = _utcnow()
                session.end_reason = runtime.end_reason
                await db.commit()

    async def _mark_started(self, session_id: str) -> None:
        async with async_session() as db:
            session = await db.get(TerminalSession, session_id)
            if session is not None:
                session.status = "active"
                session.started_at = _utcnow()
                await db.commit()

    async def target_disconnected(self, node_id: str, target_kind: str) -> None:
        for runtime in list(self._sessions.values()):
            if runtime.node_id == node_id and runtime.target_kind == target_kind:
                runtime.end_reason = f"{target_kind} disconnected"
                runtime.ended.set()

    async def close_user_privileged(self, user_id: str, reason: str) -> None:
        for runtime in list(self._sessions.values()):
            if runtime.user_id == user_id and runtime.mode == "admin":
                await self.close(runtime.session_id, reason)

    async def shutdown(self) -> None:
        for session_id in list(self._sessions):
            await self.close(session_id, "Server shutting down")

    async def recover_after_restart(self) -> None:
        now = _utcnow()
        async with async_session() as db:
            await db.execute(
                update(TerminalSession)
                .where(TerminalSession.status.in_(["opening", "active"]))
                .values(
                    status="closed",
                    ended_at=now,
                    end_reason="Server restarted",
                )
            )
            await db.commit()


terminal_service = TerminalService()
