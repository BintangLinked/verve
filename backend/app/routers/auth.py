"""Application authentication endpoints: login, logout, and who am I.

Purpose: admin login with a username and password (from the backend environment), producing an httpOnly
session cookie. The cookie cannot be read by JavaScript, so no secret resides in the browser.
Repeated failed attempts are limited, and a wrong username cannot be distinguished from a wrong password.

Relations: uses app/auth.py; the frontend (hooks/useAuth.ts, components/LoginForm.tsx) calls it
with credentials: "include". Registered in app/main.py.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.auth import (
    SESSION_COOKIE,
    LoginThrottle,
    SessionRevocations,
    auth_configured,
    authenticate_admin,
    create_session_token,
    get_effective_settings,
    get_revocations,
    get_throttle,
    hash_password,
    read_session,
    require_admin,
    signing_key,
)
from app.config import Settings, get_settings
from app.db import get_session
from app.errors import AppError
from app.services import app_settings
from sqlalchemy.orm import Session as DbSession

log = logging.getLogger("app.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_effective_settings),
    throttle: LoginThrottle = Depends(get_throttle),
):
    if not auth_configured(settings):
        raise AppError("AUTH_NOT_CONFIGURED", "Application authentication is not configured", 503)
    now = datetime.now(timezone.utc)
    ip, username = (request.client.host if request.client else "unknown"), body.username.lower()
    # The slot is reserved BEFORE the password is checked (atomic), so parallel requests cannot bypass the limit.
    if not throttle.reserve(ip, username, now):
        raise AppError("TOO_MANY_ATTEMPTS", "Too many failed sign-in attempts. Try again later.", 429)
    if not authenticate_admin(body.username, body.password, settings):
        log.warning("auth.login_failed")  # no username/password in the log; the slot remains consumed
        raise AppError("INVALID_CREDENTIALS", "Invalid username or password", 401)
    throttle.release(ip, username)
    ttl = timedelta(minutes=settings.session_ttl_minutes)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(settings.admin_username, signing_key(settings), ttl, now),
        max_age=int(ttl.total_seconds()),
        httponly=True,  # not readable by JavaScript
        samesite="lax",  # not sent on cross-site POST
        secure=settings.cookie_secure,
        path="/",
    )
    log.info("auth.login_ok")
    return {"username": settings.admin_username}


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_effective_settings),
    revocations: SessionRevocations = Depends(get_revocations),
):
    """Revoke the token on the server (not only delete the cookie), so a cookie that was already copied is no longer valid."""
    token = request.cookies.get(SESSION_COOKIE)
    if token and auth_configured(settings):
        now = datetime.now(timezone.utc)
        session = read_session(token, signing_key(settings), now)
        if session:
            revocations.revoke(session.token_id, session.expires, now)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(request: Request, settings: Settings = Depends(get_effective_settings), username: str = Depends(require_admin)):
    """Who is logged in and when the session ends (for the User details page)."""
    session = read_session(request.cookies.get(SESSION_COOKIE, ""), signing_key(settings), datetime.now(timezone.utc))
    expires_at = datetime.fromtimestamp(session.expires, timezone.utc).isoformat().replace("+00:00", "Z") if session else None
    return {"username": username, "expires_at": expires_at}


class PasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


@router.put("/password", status_code=204)
def change_password(
    body: PasswordBody,
    response: Response,
    username: str = Depends(require_admin),
    settings: Settings = Depends(get_effective_settings),
    db: DbSession = Depends(get_session),
):
    """Change the admin password. The new hash is stored in the database and overrides ADMIN_PASSWORD_HASH in .env.
    The session signing key is derived from the hash, so all sessions (including this one) cease to be valid."""
    if not authenticate_admin(username, body.current_password, settings):
        raise AppError("INVALID_CURRENT_PASSWORD", "The current password is incorrect", 400)
    app_settings.set_admin_password_hash(db, hash_password(body.new_password))
    response.delete_cookie(SESSION_COOKIE, path="/")
    log.info("auth.password_changed")
