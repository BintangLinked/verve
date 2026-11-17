"""Data access for the integration_events table (outbox).

Purpose: writes events that the worker later sends to Odoo. The event is written in the SAME TRANSACTION
as the sale/replenishment, so there cannot be a sale without an event (or the reverse).

Relations: used by services/sales.py and services/replenishment.py; Task 5 adds the
claim/process functions for the worker in this file.
"""
import json

from sqlalchemy import text
from sqlalchemy.orm import Session


def insert_event(session: Session, event_type: str, aggregate_id: int, idempotency_key: str, payload: dict) -> None:
    session.execute(
        text(
            "INSERT INTO integration_events (event_type, aggregate_id, idempotency_key, payload) "
            "VALUES (:t, :a, :k, CAST(:p AS JSONB))"
        ),
        {"t": event_type, "a": aggregate_id, "k": idempotency_key, "p": json.dumps(payload)},
    )


# ---------------- used by the outbox worker (services/outbox.py) ----------------

# Status of the event's source document, per event type. Key: delivery outcome; value: status in the source table.
_AGGREGATE = {
    "SALE": ("sales", "odoo_status", {"OK": "SYNCED", "FAILED": "FAILED", "PENDING": "PENDING"}),
    "REPLENISHMENT": ("replenishment_requests", "status", {"OK": "SUBMITTED", "FAILED": "FAILED", "PENDING": "PENDING"}),
}


def set_aggregate_status(session: Session, event_type: str, aggregate_id: int, outcome: str) -> None:
    """Synchronise the status of the source document (sale or replenishment request) with the event outcome.
    Table/column names come from the constants table above, not from user input."""
    table, column, mapping = _AGGREGATE[event_type]
    extra = ", updated_at = now()" if table == "replenishment_requests" else ""
    session.execute(
        text(f"UPDATE {table} SET {column} = :s{extra} WHERE id = :id"), {"s": mapping[outcome], "id": aggregate_id}
    )


def requeue_stale(session: Session, cutoff) -> int:
    """PROCESSING events claimed before `cutoff` are considered stuck (worker died) and returned to PENDING."""
    return session.execute(
        text("UPDATE integration_events SET status = 'PENDING' WHERE status = 'PROCESSING' AND next_attempt_at < :c"),
        {"c": cutoff},
    ).rowcount


def claim_next_due(session: Session, now):
    """Take one due event and lock its row. SKIP LOCKED: an event currently locked by
    another worker is skipped, not waited for, so two workers never take the same event."""
    return session.execute(
        text(
            "SELECT id, event_type, aggregate_id, idempotency_key, payload, attempts FROM integration_events "
            "WHERE status = 'PENDING' AND next_attempt_at <= :now ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
        ),
        {"now": now},
    ).first()


def mark_processing(session: Session, event_id: int, now) -> None:
    # next_attempt_at is used as the claim time while the status is PROCESSING (to detect stuck events).
    session.execute(
        text("UPDATE integration_events SET status = 'PROCESSING', next_attempt_at = :now WHERE id = :id"),
        {"now": now, "id": event_id},
    )


def mark_done(session: Session, event_id: int, now) -> None:
    session.execute(
        text("UPDATE integration_events SET status = 'DONE', processed_at = :now, last_error = NULL WHERE id = :id"),
        {"now": now, "id": event_id},
    )


def mark_retry(session: Session, event_id: int, attempts: int, next_attempt_at, error: str) -> None:
    session.execute(
        text(
            "UPDATE integration_events SET status = 'PENDING', attempts = :a, next_attempt_at = :n, last_error = :e "
            "WHERE id = :id"
        ),
        {"a": attempts, "n": next_attempt_at, "e": error, "id": event_id},
    )


def mark_failed(session: Session, event_id: int, attempts: int, error: str, now) -> None:
    session.execute(
        text(
            "UPDATE integration_events SET status = 'FAILED', attempts = :a, last_error = :e, processed_at = :now "
            "WHERE id = :id"
        ),
        {"a": attempts, "e": error, "now": now, "id": event_id},
    )


# ---------------- used by the operational API (routers/events.py) ----------------

_EVENT_COLUMNS = (
    "id, event_type, aggregate_id, idempotency_key, status, attempts, next_attempt_at, last_error, "
    "created_at, processed_at"
)


def list_events(session: Session, status, limit: int):
    where = "WHERE status = :s " if status else ""
    return session.execute(
        text(f"SELECT {_EVENT_COLUMNS} FROM integration_events {where}ORDER BY id DESC LIMIT :limit"),
        {"s": status, "limit": limit},
    ).all()


def get_event(session: Session, event_id: int):
    return session.execute(
        text(f"SELECT {_EVENT_COLUMNS} FROM integration_events WHERE id = :id"), {"id": event_id}
    ).first()


def reset_failed(session: Session, event_id: int) -> None:
    session.execute(
        text(
            "UPDATE integration_events SET status = 'PENDING', attempts = 0, next_attempt_at = now(), "
            "last_error = NULL, processed_at = NULL WHERE id = :id"
        ),
        {"id": event_id},
    )
