"""Licence tokens: a small JSON payload signed with the server's Ed25519 key. The client bundles only the PUBLIC
key, so it can verify a cached token offline but can never forge one.

payload = {"m": machine_id, "st": "trial"|"licensed"|"expired", "exp": unix_seconds_or_null, "iat": server_time,
           "ver": 1}
token   = base64url(payload_json) + "." + base64url(signature)"""
from __future__ import annotations

import base64
import json
from typing import Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload: dict[str, Any], private_key: Ed25519PrivateKey) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _b64e(body) + "." + _b64e(private_key.sign(body))


def verify(token: str, public_key: Ed25519PublicKey) -> Optional[dict[str, Any]]:
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _b64d(body_b64)
        public_key.verify(_b64d(sig_b64), body)
        return json.loads(body)
    except (ValueError, InvalidSignature, TypeError):
        return None


def load_public_key(hex_or_b64: str) -> Ed25519PublicKey:
    s = hex_or_b64.strip()
    raw = bytes.fromhex(s) if len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s) else _b64d(s)
    return Ed25519PublicKey.from_public_bytes(raw)
