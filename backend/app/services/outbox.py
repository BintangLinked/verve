"""Outbox worker: sends sale/replenishment events to Odoo (design sections 8.4 and 8.5).

Purpose: the sale is final in the local database; this module delivers it to Odoo
reliably. Flow of one event: claim (PENDING -> PROCESSING, committed so the row lock is released) ->
call Odoo -> outcome in a new transaction:
  - success           : DONE, source document SYNCED/SUBMITTED
  - temporary failure : retried with exponential backoff; FAILED after `max_attempts`
  - business rejection: straight to FAILED (no point retrying without operator action)
The Odoo call is NOT made inside a transaction that locks the event row.

Double-deduction prevention: the reference to Odoo is always the same `idempotency_key` (= request_id), and
Odoo/the mock recognises that reference. So an event that is resent (e.g. the worker dies after Odoo
accepted but before DONE was written) does not deduct stock twice.

Relations: uses OdooClient (app/odoo/base.py) and repositories/events.py; events are written by
services/sales.py and services/replenishment.py; run by app/worker.py.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.errors import AppError
from app.odoo.base import OdooClient
from app.odoo.errors import OdooBusinessError, OdooTransientError
from app.repositories import events as repo

log = logging.getLogger("app.outbox")

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 5  # delay before retry n = 5 seconds * 2**n
STALE_AFTER = timedelta(minutes=5)  # PROCESSING for longer than this is considered stuck


@dataclass(frozen=True)
class ClaimedEvent:
    id: int
    event_type: str
    aggregate_id: int
    idempotency_key: str
    payload: dict
    attempts: int


def claim_next(session: Session, now: datetime) -> Optional[ClaimedEvent]:
    """Claim one due event. None if there is none (or all are locked by other workers)."""
    row = repo.claim_next_due(session, now)
    if row is None:
        session.rollback()
        return None
    repo.mark_processing(session, row.id, now)
    session.commit()  # releases the row lock; the PROCESSING status prevents other workers from taking it
    return ClaimedEvent(row.id, row.event_type, row.aggregate_id, row.idempotency_key, row.payload, row.attempts)


def _send(client: OdooClient, ev: ClaimedEvent):
    p = ev.payload
    if ev.event_type == "SALE":
        return client.post_sale(ev.idempotency_key, p["odoo_variant_id"], p["location_code"], p["qty"])
    return client.post_transfer(ev.idempotency_key, p["odoo_variant_id"], p["from_code"], p["to_code"], p["qty"])


def _fail(session: Session, ev: ClaimedEvent, attempts: int, error: str, now: datetime) -> None:
    repo.mark_failed(session, ev.id, attempts, error, now)
    repo.set_aggregate_status(session, ev.event_type, ev.aggregate_id, "FAILED")
    session.commit()
    log.error("outbox.failed event_id=%d key=%s attempts=%d error=%s", ev.id, ev.idempotency_key, attempts, error)


def _retry_or_fail(session: Session, ev: ClaimedEvent, error: str, now: datetime, max_attempts: int) -> None:
    """Count one failed attempt: reschedule with exponential backoff, or FAILED at the limit."""
    attempts = ev.attempts + 1
    if attempts >= max_attempts:
        _fail(session, ev, attempts, error, now)
        return
    retry_at = now + timedelta(seconds=BACKOFF_BASE_SECONDS * 2**attempts)
    repo.mark_retry(session, ev.id, attempts, retry_at, error)
    session.commit()
    log.warning("outbox.retry event_id=%d key=%s attempts=%d error=%s", ev.id, ev.idempotency_key, attempts, error)


def process_next(session: Session, client: OdooClient, now: datetime, max_attempts: int = MAX_ATTEMPTS) -> bool:
    """Process one due event. False if there is no work."""
    repo.requeue_stale(session, now - STALE_AFTER)
    session.commit()

    ev = claim_next(session, now)
    if ev is None:
        return False

    try:
        result = _send(client, ev)
    except OdooBusinessError as exc:
        _fail(session, ev, ev.attempts + 1, f"{type(exc).__name__}: {exc}", now)
    except Exception as exc:
        # An Odoo temporary failure AND an unexpected error (bug, corrupt payload) are treated the same: counted
        # as an attempt and then retried with backoff, and become FAILED after the limit. Without this,
        # the event would hang in PROCESSING forever without ever being reported as failed.
        session.rollback()
        if not isinstance(exc, OdooTransientError):
            log.exception("outbox.unexpected_error event_id=%d key=%s", ev.id, ev.idempotency_key)
        # Our own Odoo error messages are already sanitised; for an unexpected error only the type is stored because
        # its free text may contain data from Odoo (last_error is stored in the database).
        summary = f"{type(exc).__name__}: {exc}" if isinstance(exc, OdooTransientError) else type(exc).__name__
        _retry_or_fail(session, ev, summary, now, max_attempts)
    else:
        repo.mark_done(session, ev.id, now)
        repo.set_aggregate_status(session, ev.event_type, ev.aggregate_id, "OK")
        session.commit()
        log.info(
            "outbox.done event_id=%d key=%s odoo_ref=%s duplicate=%s contract=v1",
            ev.id, ev.idempotency_key, result.odoo_ref, result.duplicate,
        )
    return True


def list_events(session: Session, status: Optional[str], limit: int):
    return repo.list_events(session, status, limit)


def retry_failed_event(session: Session, event_id: int):
    """Put a FAILED event back in the queue (after the operator has fixed the cause)."""
    event = repo.get_event(session, event_id)
    if event is None:
        raise AppError("EVENT_NOT_FOUND", f"Unknown event {event_id}", 404)
    if event.status != "FAILED":
        raise AppError("NOT_FAILED", "Only FAILED events can be retried", 409)
    repo.reset_failed(session, event_id)
    repo.set_aggregate_status(session, event.event_type, event.aggregate_id, "PENDING")
    session.commit()
    return repo.get_event(session, event_id)
