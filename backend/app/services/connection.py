"""Odoo connection status monitor and startup tasks (design 8.9).

Purpose: stores the LAST connection check result in process memory, so the GET status does not need to
call Odoo (cheap, and cannot be used to flood Odoo). A new check occurs only
at startup and when the admin presses "Test connection".

Startup must not fail because of Odoo: `startup_tasks` swallows all errors and only records the status.
It runs in a background thread (app/main.py) so the API serves requests immediately even if Odoo is slow
or down, and loads the initial data only if the connection succeeds.

Relations: uses OdooClient.check_connection (app/odoo/*), seed_if_empty (services/sync.py); used by
routers/odoo_connection.py and app/main.py.
"""
import logging
import threading
from datetime import datetime, timezone
from functools import lru_cache
from typing import Callable, Optional

from app.config import get_settings
from app.odoo.base import SAFE_ERROR_MESSAGES, ConnectionStatus, OdooClient
from app.services.sync import seed_if_empty

log = logging.getLogger("app.connection")


class ConnectionMonitor:
    def __init__(self, mode: str):
        self._mode = mode
        self._lock = threading.Lock()
        # checked_at None = never checked; the API displays it as status "unknown".
        self._status = ConnectionStatus(mode, False, None, None, None)

    def current(self) -> ConnectionStatus:
        with self._lock:
            return self._status

    def _error(self, code: str) -> ConnectionStatus:
        return ConnectionStatus(self._mode, False, None, None, datetime.now(timezone.utc), code, SAFE_ERROR_MESSAGES[code])

    def check(self, client: Optional[OdooClient]) -> ConnectionStatus:
        """Check the connection and then store the result. Never raises errors: a missing client
        (empty configuration) becomes NOT_CONFIGURED, and an unexpected error becomes UNKNOWN."""
        if client is None:
            status = self._error("NOT_CONFIGURED")
        else:
            try:
                status = client.check_connection()
            except Exception as exc:
                # Only the error type is recorded; its message may contain sensitive data.
                log.warning("odoo.connection_check_crashed type=%s", type(exc).__name__)
                status = self._error("UNKNOWN")
        with self._lock:
            self._status = status
        return status


@lru_cache
def get_monitor() -> ConnectionMonitor:
    """One monitor for the whole process; tests replace it through dependency_overrides."""
    return ConnectionMonitor(get_settings().odoo_mode)


def startup_tasks(session_factory, client_factory: Callable[[], OdooClient], monitor: ConnectionMonitor) -> None:
    """Initial connection check, then load the initial data if connected. Never raises errors."""
    try:
        client = client_factory()
    except Exception as exc:
        log.warning("startup.odoo_not_configured type=%s", type(exc).__name__)
        monitor.check(None)
        return
    if monitor.check(client).connected:
        seed_if_empty(session_factory, client)  # seed_if_empty swallows errors itself
