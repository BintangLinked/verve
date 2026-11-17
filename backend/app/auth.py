"""Application authentication: admin login with a signed session cookie.

Purpose: the Odoo connection endpoints may only be opened by the admin. Built with the standard library
only (hashlib.scrypt for passwords, HMAC-SHA256 for session tokens), with no new dependencies.

Security properties maintained:
- Passwords are stored as a salted scrypt hash; comparison uses compare_digest.
- Login always computes a hash (even when the username is wrong) and returns an identical response, so
  the existence of a username cannot be inferred from the response content or the response time.
- Login attempts are limited per (IP, username) and per IP (LoginThrottle): a slot is RESERVED before the
  password is checked so parallel requests cannot bypass the limit, and the limiter memory is bounded.
- The session token carries a random id and an expiry, and is signed with a key derived from the session
  secret AND the password hash (changing the password revokes all sessions); forged, malformed, or
  expired tokens are rejected.
- Logout revokes the token on the server (SessionRevocations), so a cookie that was already copied is no longer valid.
- An empty configuration or a short secret makes authentication fail closed (nobody can sign in).

Relations: used by routers/auth.py (login/logout/me) and routers/odoo_connection.py (Depends(require_admin)).
Settings are read from app/config.py (all from the backend environment).
"""
import base64
import hashlib
import hmac
import os
import secrets
import threading
from collections import deque
from typing import NamedTuple
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession

from app.config import Settings, get_settings
from app.db import get_session
from app.errors import AppError
from app.services import app_settings

SESSION_COOKIE = "verve_session"
MIN_SECRET_LENGTH = 32

# scrypt parameters (about 16 MB of memory, tens of milliseconds per hash).
_N, _R, _P = 2**14, 8, 1


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------- password ----------------

def hash_password(password: str) -> str:
    """Scrypt hash in the format `scrypt$N$r$p$salt$hash` (random 16-byte salt)."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """True if the password matches. A malformed hash or one with unreasonable parameters is treated as a mismatch."""
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        n, r, p = int(n), int(r), int(p)
        if scheme != "scrypt" or n > 2**20 or r > 32 or p > 4:
            return False
        digest = hashlib.scrypt(password.encode(), salt=_b64d(salt), n=n, r=r, p=p, dklen=32)
        return hmac.compare_digest(digest, _b64d(expected))
    except (ValueError, TypeError):
        return False


# Dummy hash: used so the login time stays the same even when the admin username/hash does not exist yet.
_DUMMY_HASH = hash_password("dummy-password-for-constant-time")


# ---------------- configuration ----------------

def secret_value(settings: Settings) -> str:
    value = settings.session_secret
    return value.get_secret_value() if hasattr(value, "get_secret_value") else (value or "")


def auth_configured(settings: Settings) -> bool:
    """Authentication is enabled only when the username, password hash, and session secret (>= 32 characters) are present."""
    return bool(settings.admin_username and settings.admin_password_hash and len(secret_value(settings)) >= MIN_SECRET_LENGTH)


def authenticate_admin(username: str, password: str, settings: Settings) -> bool:
    """Check the admin credentials. The hash is always computed and both checks are always executed."""
    password_ok = verify_password(password, settings.admin_password_hash or _DUMMY_HASH)
    user_ok = hmac.compare_digest(username.encode(), (settings.admin_username or "").encode())
    return password_ok and user_ok


# ---------------- session token ----------------

class Session(NamedTuple):
    username: str
    token_id: str  # token signature; unique per login because it contains a random id
    expires: int  # epoch seconds


def signing_key(settings: Settings) -> str:
    """Token signing key = HMAC(session secret, admin password hash). Changing the admin password
    changes this key, so all existing sessions automatically become invalid."""
    return hmac.new(secret_value(settings).encode(), (settings.admin_password_hash or "").encode(), hashlib.sha256).hexdigest()


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_session_token(username: str, secret: str, ttl: timedelta, now: datetime) -> str:
    """Token `base64(username).randomId.expiry.signature`; signature = HMAC-SHA256 over the first three parts."""
    payload = f"{_b64e(username.encode())}.{secrets.token_hex(8)}.{int((now + ttl).timestamp())}"
    return f"{payload}.{_sign(payload, secret)}"


def read_session(token: str, secret: str, now: datetime) -> Optional[Session]:
    """Session if the token is valid and not expired; otherwise None (forged, malformed, expired)."""
    try:
        payload, _, signature = token.rpartition(".")
        if not payload or not hmac.compare_digest(signature, _sign(payload, secret)):
            return None
        name_part, _, rest = payload.partition(".")
        _, _, expires = rest.partition(".")
        if int(expires) <= int(now.timestamp()):
            return None
        return Session(_b64d(name_part).decode(), signature, int(expires))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def read_session_token(token: str, secret: str, now: datetime) -> Optional[str]:
    """Username if the token is valid and not expired; otherwise None."""
    session = read_session(token, secret, now)
    return session.username if session else None


class SessionRevocations:
    """List of revoked tokens (logout), held in process memory. An entry is removed once its token expires,
    and the size is bounded. Note: lost when the process restarts (revoked tokens become valid again until
    they expire); multiple instances or restart-resistant revocation require shared storage."""

    def __init__(self, max_entries: int = 10_000):
        self._revoked: dict[str, int] = {}
        self._max = max_entries
        self._lock = threading.Lock()

    def revoke(self, token_id: str, expires: int, now: datetime) -> None:
        with self._lock:
            ts = int(now.timestamp())
            for key in [k for k, exp in self._revoked.items() if exp <= ts]:
                del self._revoked[key]
            while len(self._revoked) >= self._max:
                del self._revoked[next(iter(self._revoked))]
            self._revoked[token_id] = expires

    def is_revoked(self, token_id: str) -> bool:
        with self._lock:
            return token_id in self._revoked


@lru_cache
def get_revocations() -> SessionRevocations:
    """One list for the whole process; tests replace it through dependency_overrides."""
    return SessionRevocations()


# ---------------- login attempt limiter ----------------

class LoginThrottle:
    """Limits login attempts per (IP, username) and per IP within a time window, in process memory.

    - A slot is RESERVED before the password is checked (reserve) and released when login succeeds (release);
      the check and the recording are one step under a lock, so parallel requests cannot bypass the limit.
    - The per-IP limit stops many usernames being guessed from a single address.
    - Memory is bounded: the table is capped at `max_keys` and expired entries are swept periodically.
    Limitation: the key uses the direct client address (X-Forwarded-For is ignored), so behind a reverse
    proxy all clients appear as one IP and an attacker can lock out the admin; multiple instances require
    shared storage (see hardening in the README).
    """

    def __init__(
        self,
        max_failures: int = 5,
        max_ip_failures: int = 20,
        window: timedelta = timedelta(minutes=5),
        max_keys: int = 10_000,
    ):
        self._max_user, self._max_ip, self._window, self._max_keys = max_failures, max_ip_failures, window, max_keys
        self._user: dict[str, deque] = {}
        self._ip: dict[str, deque] = {}
        self._last_sweep: Optional[datetime] = None
        self._lock = threading.Lock()

    def _recent(self, table: dict, key: str, now: datetime) -> deque:
        """Recent records for `key`, without CREATING an entry (a rejected attempt must not add memory)."""
        entries = table.get(key)
        if entries is None:
            return deque()
        while entries and now - entries[0] > self._window:
            entries.popleft()
        if not entries:
            del table[key]
            return deque()
        return entries

    def _sweep(self, now: datetime) -> None:
        if self._last_sweep is not None and now - self._last_sweep < self._window:
            return
        self._last_sweep = now
        for table in (self._user, self._ip):
            for key in list(table):
                self._recent(table, key, now)  # trim old records and remove entries that are already empty

    def _cap(self, table: dict) -> None:
        while len(table) > self._max_keys:
            del table[next(iter(table))]  # drop the oldest key

    def reserve(self, ip: str, username: str, now: datetime) -> bool:
        """Reserve one attempt slot. False if (IP, username) or the IP has already reached the limit."""
        with self._lock:
            self._sweep(now)
            user_key = f"{ip}|{username}"
            user_entries, ip_entries = self._recent(self._user, user_key, now), self._recent(self._ip, ip, now)
            if len(user_entries) >= self._max_user or len(ip_entries) >= self._max_ip:
                return False
            self._user.setdefault(user_key, user_entries).append(now)  # a new entry is created only when the slot is granted
            self._ip.setdefault(ip, ip_entries).append(now)
            self._cap(self._user)
            self._cap(self._ip)
            return True

    def release(self, ip: str, username: str) -> None:
        """Login succeeded: clear this username's records and one IP slot."""
        with self._lock:
            self._user.pop(f"{ip}|{username}", None)
            ip_entries = self._ip.get(ip)
            if ip_entries:
                ip_entries.pop()

    def size(self) -> int:
        with self._lock:
            return len(self._user) + len(self._ip)


@lru_cache
def get_throttle() -> LoginThrottle:
    """One limiter for the whole process; tests replace it through dependency_overrides."""
    return LoginThrottle()


# ---------------- FastAPI dependency ----------------

def get_effective_settings(settings: Settings = Depends(get_settings), session: DbSession = Depends(get_session)) -> Settings:
    """Settings with the admin password hash from the database if the admin has ever changed it through the application."""
    stored = app_settings.get_admin_password_hash(session)
    return settings.model_copy(update={"admin_password_hash": stored}) if stored else settings


def require_admin(
    request: Request,
    settings: Settings = Depends(get_effective_settings),
    revocations: SessionRevocations = Depends(get_revocations),
) -> str:
    """Login required: return the admin username from the session cookie, or 401 UNAUTHENTICATED.
    Forged, malformed, expired, revoked (logout), or pre-password-change tokens are rejected."""
    token = request.cookies.get(SESSION_COOKIE)
    session = None
    if token and auth_configured(settings):
        session = read_session(token, signing_key(settings), datetime.now(timezone.utc))
    if (
        session is None
        or revocations.is_revoked(session.token_id)
        or not hmac.compare_digest(session.username.encode(), (settings.admin_username or "").encode())
    ):
        raise AppError("UNAUTHENTICATED", "Sign in required", 401)
    return session.username
