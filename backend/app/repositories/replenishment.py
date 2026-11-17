"""Replenishment request data access.

Purpose: SQL for the replenishment needs list and for creating requests. Its idempotency pattern is the same
as for sales: INSERT ... ON CONFLICT (request_id) DO NOTHING. The rule "one PENDING request
per shop/variant" is enforced by a partial unique index (migration 0004); a violation surfaces as an
IntegrityError that the service translates into 409 OPEN_REQUEST_EXISTS.

Relations: used by services/replenishment.py. The replenishment_requests table belongs to the application.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session


def fetch_below_threshold(session: Session):
    """Shop/variant pairs with stock < threshold, together with central warehouse stock and the PENDING request flag."""
    return session.execute(
        text(
            "SELECT l.code AS store_code, v.id AS variant_id, v.sku, v.name AS variant_name, "
            "sl.qty_sellable AS available, v.low_stock_threshold AS threshold, "
            "COALESCE(wh.qty_sellable, 0) AS warehouse_available, "
            "EXISTS (SELECT 1 FROM replenishment_requests r WHERE r.store_location_id = l.id "
            "        AND r.variant_id = v.id AND r.status = 'PENDING') AS pending_request "
            "FROM stock_levels sl "
            "JOIN locations l ON l.id = sl.location_id AND l.type = 'STORE' "
            "JOIN variants v ON v.id = sl.variant_id "
            "LEFT JOIN locations whl ON whl.code = 'WH' "
            "LEFT JOIN stock_levels wh ON wh.variant_id = v.id AND wh.location_id = whl.id "
            "WHERE sl.qty_sellable < v.low_stock_threshold "
            "ORDER BY l.code, v.sku"
        )
    ).all()


def insert_request_if_new(
    session: Session, *, request_id: str, payload_hash: str, store_location_id: int, variant_id: int, qty: int
):
    """Return the new row, or None if request_id already exists."""
    return session.execute(
        text(
            "INSERT INTO replenishment_requests (request_id, payload_hash, store_location_id, variant_id, qty_requested) "
            "VALUES (:r, :h, :s, :v, :q) ON CONFLICT (request_id) DO NOTHING "
            "RETURNING id, request_id, variant_id, qty_requested, status"
        ),
        {"r": request_id, "h": payload_hash, "s": store_location_id, "v": variant_id, "q": qty},
    ).first()


def get_request_by_request_id(session: Session, request_id: str):
    return session.execute(
        text(
            "SELECT r.id, r.request_id, r.payload_hash, r.variant_id, r.qty_requested, r.status, "
            "l.code AS store_code FROM replenishment_requests r JOIN locations l ON l.id = r.store_location_id "
            "WHERE r.request_id = :r"
        ),
        {"r": request_id},
    ).first()
