"""Operational integration events endpoints: view the outbox queue and retry FAILED events.

Purpose: a troubleshooting facility (design 13.1), e.g. "why has the stock not changed in Odoo?" or
"the replenishment request failed". HTTP layer only; the logic is in services/outbox.py.

Admin only (session login): `last_error` contains operational error text from Odoo.

Relations: calls services/outbox.py; uses require_admin (app/auth.py); registered in app/main.py.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_session
from app.schemas.events import EventOut, EventStatus
from app.services import outbox as outbox_service

router = APIRouter(
    prefix="/api/integration-events", tags=["integration-events"], dependencies=[Depends(require_admin)]
)  # last_error contains operational text: admin only


@router.get("", response_model=list[EventOut])
def list_events(
    status: Optional[EventStatus] = None,
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
):
    return outbox_service.list_events(session, status, limit)


@router.post("/{event_id}/retry", response_model=EventOut)
def retry_event(event_id: int, session: Session = Depends(get_session)):
    return outbox_service.retry_failed_event(session, event_id)
