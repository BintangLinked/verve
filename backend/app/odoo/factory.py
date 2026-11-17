"""Selects the OdooClient implementation: stored configuration (Odoo configuration menu), or mock.

Purpose: the only place that knows mock or live. Routers, services, and the worker simply
call `get_odoo_client()`.

Relations: reads app/config.py and app_settings; produces MockOdooClient (mock.py) or LiveOdooClient (live.py).
"""
import threading
from functools import lru_cache
from typing import Optional

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.errors import AppError
from app.odoo.base import OdooClient
from app.odoo.mock import MockOdooClient
from app.services import app_settings


def get_odoo_client(settings: Settings | None = None, engine: Engine | None = None) -> OdooClient:
    """Client from the environment: mock mode only. Odoo credentials are NOT read from the environment; live mode
    uses the stored configuration (stored_client). Without it, live mode means not configured."""
    settings = settings or get_settings()
    if settings.odoo_mode == "mock":
        if engine is None:
            from app.db import engine as default_engine

            engine = default_engine
        return MockOdooClient(engine)
    raise ValueError("Odoo is not connected: open Odoo configuration and enter the URL, database and API key")


@lru_cache
def _cached_client() -> OdooClient:
    return get_odoo_client()


_stored = {"fingerprint": None, "client": None}
_stored_lock = threading.Lock()


def stored_client(session: Session, settings: Settings) -> Optional[OdooClient]:
    """Live client from the configuration stored through the application, or None if there is none yet.
    The instance is reused while the configuration does not change; the API and the worker (separate processes)
    rebuild it themselves when the configuration fingerprint changes."""
    config = app_settings.load_odoo_config(session, settings)
    if config is None:
        return None
    fp = app_settings.fingerprint(session)
    with _stored_lock:
        if _stored["fingerprint"] != fp or _stored["client"] is None:
            from app.odoo.live import LiveOdooClient

            _stored["client"] = LiveOdooClient(
                config.url, config.database, config.api_key,
                allow_private_hosts=settings.odoo_allow_private_hosts,
            )
            _stored["fingerprint"] = fp
        return _stored["client"]


def _stored_or_none() -> Optional[OdooClient]:
    """Stored configuration if present. A database problem or a missing table must not bring down
    the caller: treated as not yet connected."""
    try:
        from app.db import SessionLocal

        with SessionLocal() as session:
            return stored_client(session, get_settings())
    except Exception:
        return None


def get_odoo() -> OdooClient:
    """FastAPI dependency: the Odoo client. The stored configuration (database) takes precedence; without it only mock mode has a client.
    Tests replace it through app.dependency_overrides[get_odoo]. An incomplete live configuration becomes 503
    ODOO_NOT_CONFIGURED (not a crash), and is not cached so it recovers after the configuration is corrected."""
    stored = _stored_or_none()
    if stored is not None:
        return stored
    try:
        return _cached_client()
    except ValueError as exc:
        raise AppError("ODOO_NOT_CONFIGURED", str(exc), 503) from exc


def get_odoo_or_none() -> Optional[OdooClient]:
    """Like get_odoo, but None if the configuration is incomplete. Used by the connection status endpoint:
    an empty configuration must appear as status NOT_CONFIGURED, not a 503 error."""
    try:
        return get_odoo()
    except AppError:
        return None
