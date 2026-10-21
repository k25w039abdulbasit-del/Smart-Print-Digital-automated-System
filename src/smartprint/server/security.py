"""Hashing, signed tokens and print authorisations - stdlib only (no JWT dependency)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Optional

_PBKDF2_ITERS = 240_000


def hash_password(password: str, *, iters: int = _PBKDF2_ITERS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def new_secret(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(payload: dict[str, Any], secret: str, ttl_s: int) -> str:
    body = {**payload, "exp": int(time.time()) + ttl_s}
    raw = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def verify_token(token: str, secret: str) -> Optional[dict[str, Any]]:
    try:
        raw, sig = token.split(".")
        good = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        body = json.loads(_unb64(raw))
        return body if body.get("exp", 0) >= time.time() else None
    except Exception:
        return None


def hmac_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def print_authorisation(secret: str, *, job_id: str, file_sha256: str, settings: dict,
                        total_minor: int, nonce: str) -> str:
    """Signature that binds *this* file + *these* settings + *this* paid amount.

    The print worker refuses to run a job unless the signature still matches, so a
    swapped file, edited settings or a hand-edited DB row cannot reach the printer.
    """
    canon = json.dumps(settings, sort_keys=True, separators=(",", ":"))
    return hmac_hex(secret, f"auth|{job_id}|{file_sha256}|{canon}|{total_minor}|{nonce}")


class LoginThrottle:
    """Tiny in-memory brute-force guard: 5 failures -> 60 s lockout per key."""

    def __init__(self, max_failures: int = 5, lock_s: int = 60):
        self.max, self.lock_s = max_failures, lock_s
        self._fails: dict[str, list[float]] = {}

    def blocked(self, key: str) -> bool:
        now = time.time()
        recent = [t for t in self._fails.get(key, []) if now - t < self.lock_s]
        self._fails[key] = recent
        return len(recent) >= self.max

    def failure(self, key: str) -> None:
        self._fails.setdefault(key, []).append(time.time())

    def success(self, key: str) -> None:
        self._fails.pop(key, None)
