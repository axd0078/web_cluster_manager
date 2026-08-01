from __future__ import annotations

# Keep the Agent protocol implementation byte-for-byte compatible with the
# Server while allowing the Agent to run directly from its own directory.
import base64
import hashlib
import json
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PROTOCOL_VERSION = 3
SERVER_TO_TARGET = "server_to_target"
TARGET_TO_SERVER = "target_to_server"
_NONCE_PREFIX = {SERVER_TO_TARGET: b"S2T3", TARGET_TO_SERVER: b"T2S3"}


class TerminalProtocolError(ValueError):
    pass


def new_ephemeral_key() -> tuple[X25519PrivateKey, str]:
    private = X25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return private, base64.b64encode(raw).decode("ascii")


def _context(session_id: str, node_id: str, mode: str) -> bytes:
    return f"wcm-terminal-v3|{session_id}|{node_id}|{mode}".encode()


def _derive(private, peer_b64, session_id, node_id, mode):
    try:
        peer = X25519PublicKey.from_public_bytes(base64.b64decode(peer_b64, validate=True))
    except (ValueError, TypeError) as exc:
        raise TerminalProtocolError("invalid terminal public key") from exc
    context = _context(session_id, node_id, mode)
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=hashlib.sha256(context).digest(),
        info=context + b"|directional-keys",
    ).derive(private.exchange(peer))
    return material[:32], material[32:]


def _aad(session_id, node_id, mode, direction, sequence, frame_type):
    return json.dumps({
        "direction": direction, "mode": mode, "node_id": node_id,
        "sequence": sequence, "session_id": session_id, "type": frame_type,
        "version": 3,
    }, separators=(",", ":"), sort_keys=True).encode()


def _nonce(direction, sequence):
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
    def target(cls, private, peer_b64, *, session_id, node_id, mode):
        inbound, outbound = _derive(private, peer_b64, session_id, node_id, mode)
        return cls(
            session_id, node_id, mode, TARGET_TO_SERVER, SERVER_TO_TARGET,
            bytearray(outbound), bytearray(inbound),
        )

    def encrypt(self, frame_type, payload):
        if self.destroyed:
            raise TerminalProtocolError("terminal keys destroyed")
        sequence = self.send_sequence
        plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        ciphertext = ChaCha20Poly1305(bytes(self._send_key)).encrypt(
            _nonce(self.send_direction, sequence),
            plaintext,
            _aad(
                self.session_id, self.node_id, self.mode, self.send_direction,
                sequence, frame_type,
            ),
        )
        self.send_sequence += 1
        return {
            "type": frame_type, "version": 3, "session_id": self.session_id,
            "sequence": sequence,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    def decrypt(self, frame):
        if self.destroyed:
            raise TerminalProtocolError("terminal keys destroyed")
        frame_type = frame.get("type")
        sequence = frame.get("sequence")
        if (
            frame.get("version") != 3
            or frame.get("session_id") != self.session_id
            or not isinstance(frame_type, str)
            or sequence != self.receive_sequence
        ):
            raise TerminalProtocolError("terminal frame context rejected")
        try:
            plaintext = ChaCha20Poly1305(bytes(self._receive_key)).decrypt(
                _nonce(self.receive_direction, sequence),
                base64.b64decode(frame.get("ciphertext", ""), validate=True),
                _aad(
                    self.session_id, self.node_id, self.mode, self.receive_direction,
                    sequence, frame_type,
                ),
            )
            payload = json.loads(plaintext)
        except Exception as exc:
            raise TerminalProtocolError("terminal frame authentication failed") from exc
        if not isinstance(payload, dict):
            raise TerminalProtocolError("terminal payload must be an object")
        self.receive_sequence += 1
        return frame_type, payload

    def destroy(self):
        for key in (self._send_key, self._receive_key):
            for index in range(len(key)):
                key[index] = 0
        self.destroyed = True
