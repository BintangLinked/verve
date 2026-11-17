"""Response shape of the operational integration events API.

Purpose: the operator sees the delivery status to Odoo (PENDING/PROCESSING/DONE/FAILED), the number of
attempts, and the last error message to trace problems without opening the database.

Relations: used by routers/events.py; data comes from the integration_events table (outbox).
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

EventStatus = Literal["PENDING", "PROCESSING", "DONE", "FAILED"]


class EventOut(BaseModel):
    id: int
    event_type: str
    aggregate_id: int
    idempotency_key: str
    status: EventStatus
    attempts: int
    next_attempt_at: datetime
    last_error: Optional[str]
    created_at: datetime
    processed_at: Optional[datetime]
