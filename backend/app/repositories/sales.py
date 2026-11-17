"""Sales and stock deduction data access.

Purpose: these two SQL statements are the core of anti-overselling and anti-duplication:
- insert_sale_if_new : INSERT ... ON CONFLICT DO NOTHING on request_id (the idempotency gate)
- decrement_stock    : a single atomic UPDATE with the condition `qty_sellable >= n`; PostgreSQL locks
                       the row so two buyers of the last unit are serialised and only one succeeds.

Relations: used by services/sales.py. The `sales` and `stock_levels` tables belong to the application.
"""
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


def insert_sale_if_new(
    session: Session, *, request_id: str, payload_hash: str, variant_id: int, location_id: int, qty: int, channel: str
):
    """Return the new sale row, or None if request_id already exists (duplicate)."""
    return session.execute(
        text(
            "INSERT INTO sales (request_id, payload_hash, variant_id, location_id, qty, channel) "
            "VALUES (:r, :h, :v, :l, :q, :c) ON CONFLICT (request_id) DO NOTHING "
            "RETURNING id, request_id, variant_id, qty, channel, odoo_status"
        ),
        {"r": request_id, "h": payload_hash, "v": variant_id, "l": location_id, "q": qty, "c": channel},
    ).first()


def get_sale_by_request_id(session: Session, request_id: str):
    return session.execute(
        text(
            "SELECT s.id, s.request_id, s.payload_hash, s.variant_id, s.location_id, s.qty, s.channel, "
            "s.odoo_status, l.code AS location_code FROM sales s JOIN locations l ON l.id = s.location_id "
            "WHERE s.request_id = :r"
        ),
        {"r": request_id},
    ).first()


def decrement_stock(session: Session, variant_id: int, location_id: int, qty: int) -> Optional[int]:
    """Reduce sellable stock. Returns the remaining stock, or None if stock is insufficient."""
    return session.execute(
        text(
            "UPDATE stock_levels SET qty_sellable = qty_sellable - :q "
            "WHERE variant_id = :v AND location_id = :l AND qty_sellable >= :q RETURNING qty_sellable"
        ),
        {"q": qty, "v": variant_id, "l": location_id},
    ).scalar_one_or_none()


def add_damaged(session: Session, variant_id: int, location_id: int, qty: int) -> None:
    """Add damaged stock. Never touches qty_sellable."""
    session.execute(
        text(
            "INSERT INTO stock_levels (variant_id, location_id, qty_sellable, qty_damaged) VALUES (:v, :l, 0, :q) "
            "ON CONFLICT (variant_id, location_id) DO UPDATE SET qty_damaged = stock_levels.qty_damaged + :q"
        ),
        {"v": variant_id, "l": location_id, "q": qty},
    )
