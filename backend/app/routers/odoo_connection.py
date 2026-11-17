"""Odoo connection status endpoints, admin only.

- GET  /api/odoo/connection       : STORED status (does not call Odoo)
- POST /api/odoo/connection/test  : re-check using the backend configuration

No endpoint accepts credentials: POST /test has no body model, so any request content
is ignored and the configuration from the backend environment is always used. The response does not contain the
URL, login, or API key (see schemas/connection.py).

Relations: uses ConnectionMonitor (services/connection.py), require_admin (app/auth.py), and
get_odoo_or_none (app/odoo/factory.py); used by the "Odoo Connection" panel in the frontend.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Response

from app.auth import require_admin
from app.odoo.base import OdooClient
from app.odoo.factory import get_odoo_or_none
from app.schemas.connection import OdooConnectionOut, to_out
from app.services.connection import ConnectionMonitor, get_monitor

router = APIRouter(prefix="/api/odoo/connection", tags=["odoo-connection"], dependencies=[Depends(require_admin)])


@router.get("", response_model=OdooConnectionOut)
def get_connection(response: Response, monitor: ConnectionMonitor = Depends(get_monitor)):
    response.headers["Cache-Control"] = "no-store"
    return to_out(monitor.current())


@router.post("/test", response_model=OdooConnectionOut)
def test_connection(
    response: Response,
    monitor: ConnectionMonitor = Depends(get_monitor),
    client: Optional[OdooClient] = Depends(get_odoo_or_none),
):
    response.headers["Cache-Control"] = "no-store"
    return to_out(monitor.check(client))
