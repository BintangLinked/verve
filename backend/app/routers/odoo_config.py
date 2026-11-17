"""Odoo connection configuration through the application, admin only. Only three fields: URL, database name, API key.

- GET    /api/odoo/config : URL, database, and whether an API key exists (the API key is NEVER returned)
- PUT    /api/odoo/config : save the configuration; the API key can only be written, and is stored encrypted
- DELETE /api/odoo/config : disconnect (Disconnect): delete the stored URL, database, and API key

Odoo credentials are not read from `.env`; the configuration stored in the database is the only source for a real Odoo.

The SSRF policy still applies: the URL is validated when saved and again on every connection. Permitting private
addresses/localhost remains possible only through ODOO_ALLOW_PRIVATE_HOSTS on the server, and cannot be changed from the UI.

Moving to another Odoo/database: the stored key is never reused (a new key is required), and if there is local
data belonging to the previous Odoo (the Odoo ids in it are not valid in the new Odoo), the admin must approve a local data reset.

Relations: services/app_settings.py (storage), odoo/url_policy.py (URL validation), odoo/factory.py
(the client is rebuilt automatically by the API and the worker when the configuration changes); used by the /odoo page.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.config import Settings, get_settings
from app.db import get_session
from app.errors import AppError
from app.odoo.url_policy import UrlNotAllowed, validate_odoo_url
from app.services import app_settings
from app.services.connection import ConnectionMonitor, get_monitor

router = APIRouter(prefix="/api/odoo/config", tags=["odoo-config"], dependencies=[Depends(require_admin)])


class OdooConfigOut(BaseModel):
    source: Literal["database", "mock", "none"]  # none = live mode without configuration (not yet connected)
    configured: bool
    url: Optional[str]
    database: Optional[str]
    api_key_set: bool  # indicator only; the value is never sent


class OdooConfigIn(BaseModel):
    url: str = Field(min_length=1, max_length=300)
    database: str = Field(min_length=1, max_length=100)
    api_key: Optional[str] = Field(default=None, max_length=500)  # empty = keep (only for the same server+database)
    reset_local_data: bool = False  # agrees to delete local data belonging to the previous Odoo when moving to another Odoo


def _current(session: Session, settings: Settings) -> OdooConfigOut:
    stored = app_settings.stored_summary(session)
    if stored:
        return OdooConfigOut(source="database", configured=True, api_key_set=True, **stored)
    source = "mock" if settings.odoo_mode == "mock" else "none"
    return OdooConfigOut(source=source, configured=False, url=None, database=None, api_key_set=False)


@router.get("", response_model=OdooConfigOut)
def get_config(response: Response, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)):
    response.headers["Cache-Control"] = "no-store"
    return _current(session, settings)


@router.put("", response_model=OdooConfigOut)
def save_config(
    body: OdooConfigIn,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        url = validate_odoo_url(body.url, allow_private=settings.odoo_allow_private_hosts)
    except UrlNotAllowed as exc:
        raise AppError("URL_NOT_ALLOWED", f"The Odoo URL is not allowed: {exc}", 422) from exc
    database = body.database.strip()
    try:
        if app_settings.is_other_odoo(session, url, database):
            counts = app_settings.local_data_counts(session)
            if counts["locations"] or counts["variants"] or counts["sales"] or counts["unsent_events"]:
                if not body.reset_local_data:
                    raise AppError(
                        "ODOO_SWITCH_REQUIRES_RESET",
                        "This is a different Odoo server or database. The local catalogue, stock, sales and Odoo events "
                        f"({counts['variants']} variants, {counts['sales']} sales, {counts['unsent_events']} unsent events) "
                        "belong to the previous Odoo and must be reset first.",
                        409,
                    )
                app_settings.reset_local_data(session)
        app_settings.save_odoo_config(session, settings, url, database, body.api_key)
        session.commit()  # the configuration and the local data reset are saved together or not at all
    except Exception:
        session.rollback()
        raise
    return _current(session, settings)


@router.delete("", response_model=OdooConfigOut)
def disconnect(
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    monitor: ConnectionMonitor = Depends(get_monitor),
):
    """Disconnect Odoo: the stored URL, database, and API key are deleted. Events not yet sent remain waiting
    in the outbox and are sent after reconnecting to the same Odoo."""
    response.headers["Cache-Control"] = "no-store"
    app_settings.clear_odoo_config(session)
    session.commit()
    monitor.check(None)  # connection status becomes NOT_CONFIGURED
    return _current(session, settings)
