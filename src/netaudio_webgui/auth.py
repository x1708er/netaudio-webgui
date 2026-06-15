from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

# scrypt parameters: memory ≈ 128 * N * r ≈ 16 MiB, within OpenSSL's default
# 32 MiB maxmem, so no explicit maxmem is needed.
_N = 16384
_R = 8
_P = 1
_PREFIX = "scrypt$"

# Upper bounds for parameters parsed from a stored hash — guards against a
# tampered/huge N (which would make scrypt block for minutes or OOM).
_MAX_N = 1 << 20
_MAX_R = 64
_MAX_P = 64


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def is_hash(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    # base64 of standard alphabet never contains '$', so '$' is a safe separator.
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    if not is_hash(stored):
        return False
    try:
        _, n, r, p, salt_b64, hash_b64 = stored.split("$")
        n, r, p = int(n), int(r), int(p)
        if n > _MAX_N or r > _MAX_R or p > _MAX_P:
            return False
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=n, r=r, p=p, dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


class UserStore:
    """Username -> scrypt-hash map. Plaintext entries in the source file are
    hashed on load and written back, so the file only ever stores hashes after
    the first start."""

    def __init__(self, hashes: dict[str, str]):
        self._hashes = dict(hashes)

    @classmethod
    def from_plaintext(cls, users: dict[str, str]) -> "UserStore":
        return cls({u: hash_password(p) for u, p in users.items()})

    @classmethod
    def load(cls, path: str | Path) -> "UserStore":
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}

        hashes: dict[str, str] = {}
        changed = False
        for user, value in raw.items():
            if not isinstance(value, str) or not value:
                continue  # skip malformed/empty entries
            if is_hash(value):
                hashes[user] = value
            else:
                hashes[user] = hash_password(value)
                changed = True

        store = cls(hashes)
        if changed:
            store._write(path)
        return store

    def _write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._hashes, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".users-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
            os.chmod(path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def usernames(self) -> list[str]:
        return sorted(self._hashes)

    def verify(self, username: str, password: str) -> bool:
        stored = self._hashes.get(username)
        if stored is None:
            return False
        return verify_password(password, stored)


class SessionStore:
    """In-memory session table: token -> {username, created}. Sessions are lost
    on process restart (acceptable for a LAN tool)."""

    def __init__(self):
        self._sessions: dict[str, dict] = {}

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {"username": username, "created": time.time()}
        return token

    def get(self, token: str | None) -> dict | None:
        if not token:
            return None
        return self._sessions.get(token)

    def delete(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)
