"""Response shape of the Odoo connection status API.

Purpose: only data that is safe to display: mode, status, database name, Odoo version, last check
time, and the sanitised error (code + fixed text). The URL, login, and API key are NEVER present
here, so they cannot leak through this endpoint.

Relations: used by routers/odoo_connection.py; mirrored in the frontend/lib/types.ts (OdooConnection).
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.odoo.base import ConnectionStatus


class ConnectionErrorOut(BaseModel):
    code: str
    message: str


class OdooConnectionOut(BaseModel):
    mode: Literal["mock", "live"]
    status: Literal["connected", "disconnected", "unknown"]
    database: Optional[str]
    odoo_version: Optional[str]
    last_checked_at: Optional[datetime]
    error: Optional[ConnectionErrorOut]


def to_out(status: ConnectionStatus) -> OdooConnectionOut:
    if status.checked_at is None:
        state = "unknown"
    else:
        state = "connected" if status.connected else "disconnected"
    error = ConnectionErrorOut(code=status.error_code, message=status.error_message) if status.error_code else None
    return OdooConnectionOut(
        mode=status.mode,
        status=state,
        database=status.database,
        odoo_version=status.server_version,
        last_checked_at=status.checked_at,
        error=error,
    )
