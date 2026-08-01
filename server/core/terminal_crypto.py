from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


PROTOCOL_VERSION = 3
SERVER_TO_TARGET = "server_to_target"
TARGET_TO_SERVER = "target_to_server"
_NONCE_PREFIX = {
    SERVER_TO_TARGET: b"S2T3",
    TARGET_TO_SERVER: b"T2S3",
}


class TerminalProtocolError(ValueError):
    pass


def new_ephemeral_key() -> tuple[X25519PrivateKey, str]:
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, base64.b64encode(public).decode("ascii")


def _context(session_id: str, node_id: str, mode: str) -> bytes:
    return (
        f"wcm-terminal-v{PROTOCOL_VERSION}|{session_id}|{node_id}|{mode}"
    ).encode("utf-8")


def derive_keys(
    private_key: X25519PrivateKey,
    peer_public_b64: str,
    *,
    session_id: str,
    node_id: str,
    mode: str,
) -> tuple[bytes, bytes]:
    try:
        peer_raw = base64.b64decode(peer_public_b64, validate=True)
        peer = X25519PublicKey.from_public_bytes(peer_raw)
    except (ValueError, TypeError) as exc:
        raise TerminalProtocolError("invalid terminal public key") from exc
    context = _context(session_id, node_id, mode)
    shared = private_key.exchange(peer)
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=hashlib.sha256(context).digest(),
        info=context + b"|directional-keys",
    ).derive(shared)
    return material[:32], material[32:]


def _aad(
    *,
    session_id: str,
    node_id: str,
    mode: str,
    direction: str,
    sequence: int,
    frame_type: str,
) -> bytes:
    return json.dumps(
        {
            "direction": direction,
            "mode": mode,
            "node_id": node_id,
            "sequence": sequence,
            "session_id": session_id,
            "type": frame_type,
            "version": PROTOCOL_VERSION,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _nonce(direction: str, sequence: int) -> bytes:
    if direction not in _NONCE_PREFIX or not 0 <= sequence < 2**64:
        raise TerminalProtocolError("invalid terminal nonce")
    return _NONCE_PREFIX[direction] + sequence.to_bytes(8, "big")


@dataclass
class TerminalCipher:
    session_id: str
    node_id: str
    mode: str
    send_direction: str
    receive_direction: str
    _send_key: bytearray
    _receive_key: bytearray
    send_sequence: int = 0
    receive_sequence: int = 0
    destroyed: bool = False

    @classmethod
    def server(
        cls,
        private_key: X25519PrivateKey,
        peer_public_b64: str,
        *,
        session_id: str,
        node_id: str,
        mode: str,
    ) -> "TerminalCipher":
        outbound, inbound = derive_keys(
            private_key,
            peer_public_b64,
            session_id=session_id,
            node_id=node_id,
            mode=mode,
        )
        return cls(
            session_id,
            node_id,
            mode,
            SERVER_TO_TARGET,
            TARGET_TO_SERVER,
            bytearray(outbound),
            bytearray(inbound),
        )

    @classmethod
    def target(
        cls,
        private_key: X25519PrivateKey,
        peer_public_b64: str,
        *,
        session_id: str,
        node_id: str,
        mode: str,
    ) -> "TerminalCipher":
        inbound, outbound = derive_keys(
            private_key,
            peer_public_b64,
            session_id=session_id,
            node_id=node_id,
            mode=mode,
        )
        return cls(
            session_id,
            node_id,
            mode,
            TARGET_TO_SERVER,
            SERVER_TO_TARGET,
            bytearray(outbound),
            bytearray(inbound),
        )

    def encrypt(self, frame_type: str, payload: dict) -> dict:
        if self.destroyed:
            raise TerminalProtocolError("terminal keys destroyed")
        sequence = self.send_sequence
        plaintext = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        aad = _aad(
            session_id=self.session_id,
            node_id=self.node_id,
            mode=self.mode,
            direction=self.send_direction,
            sequence=sequence,
            frame_type=frame_type,
        )
        ciphertext = ChaCha20Poly1305(bytes(self._send_key)).encrypt(
            _nonce(self.send_direction, sequence),
            plaintext,
            aad,
        )
        self.send_sequence += 1
        return {
            "type": frame_type,
            "version": PROTOCOL_VERSION,
            "session_id": self.session_id,
            "sequence": sequence,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    def decrypt(self, frame: dict) -> tuple[str, dict]:
        if self.destroyed:
            raise TerminalProtocolError("terminal keys destroyed")
        if (
            frame.get("version") != PROTOCOL_VERSION
            or frame.get("session_id") != self.session_id
        ):
            raise TerminalProtocolError("terminal frame context mismatch")
        frame_type = frame.get("type")
        sequence = frame.get("sequence")
        if not isinstance(frame_type, str) or sequence != self.receive_sequence:
            raise TerminalProtocolError("terminal frame sequence rejected")
        try:
            ciphertext = base64.b64decode(frame.get("ciphertext", ""), validate=True)
            aad = _aad(
                session_id=self.session_id,
                node_id=self.node_id,
                mode=self.mode,
                direction=self.receive_direction,
                sequence=sequence,
                frame_type=frame_type,
            )
            plaintext = ChaCha20Poly1305(bytes(self._receive_key)).decrypt(
                _nonce(self.receive_direction, sequence),
                ciphertext,
                aad,
            )
            payload = json.loads(plaintext)
        except Exception as exc:
            raise TerminalProtocolError("terminal frame authentication failed") from exc
        if not isinstance(payload, dict):
            raise TerminalProtocolError("terminal payload must be an object")
        self.receive_sequence += 1
        return frame_type, payload

    def destroy(self) -> None:
        for key in (self._send_key, self._receive_key):
            for index in range(len(key)):
                key[index] = 0
        self.destroyed = True
