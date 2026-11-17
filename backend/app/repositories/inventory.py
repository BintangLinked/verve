"""Inventory data access: all SQL for locations, products, variants, stock, and stock movements.

Purpose: the repository layer contains SQL only; there are no business rules here. The rules
(stock status, filters, synchronisation) are in services/inventory.py and services/sync.py.

Relations: used by services/sync.py (write) and services/inventory.py (read).
These tables belong to the application (see migrations/0001_initial.py).
"""
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.odoo.base import OdooLocation, OdooVariant


# ---------------- write (used by sync) ----------------

def upsert_location(session: Session, loc: OdooLocation) -> int:
    return session.execute(
        text(
            "INSERT INTO locations (code, name, type, odoo_location_id) VALUES (:code, :name, :type, :oid) "
            "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, type = EXCLUDED.type, "
            "odoo_location_id = EXCLUDED.odoo_location_id RETURNING id"
        ),
        {"code": loc.code, "name": loc.name, "type": loc.type, "oid": loc.odoo_id},
    ).scalar_one()


def upsert_product(session: Session, odoo_template_id: int, name: str) -> int:
    return session.execute(
        text(
            "INSERT INTO products (name, odoo_template_id) VALUES (:name, :tid) "
            "ON CONFLICT (odoo_template_id) DO UPDATE SET name = EXCLUDED.name RETURNING id"
        ),
        {"name": name, "tid": odoo_template_id},
    ).scalar_one()


def upsert_variant(session: Session, product_id: int, v: OdooVariant) -> int:
    return session.execute(
        text(
            "INSERT INTO variants (product_id, sku, name, odoo_variant_id, low_stock_threshold) "
            "VALUES (:pid, :sku, :name, :vid, :thr) "
            "ON CONFLICT (odoo_variant_id) DO UPDATE SET product_id = EXCLUDED.product_id, sku = EXCLUDED.sku, "
            "name = EXCLUDED.name, low_stock_threshold = EXCLUDED.low_stock_threshold RETURNING id"
        ),
        {"pid": product_id, "sku": v.sku, "name": v.variant_name, "vid": v.odoo_variant_id, "thr": v.low_stock_threshold},
    ).scalar_one()


def lock_stock_levels(session: Session) -> None:
    """Lock all stock rows (in a fixed order to avoid deadlock). Sales that touch the same
    rows wait until the sync completes, so no sale is overwritten."""
    session.execute(text("SELECT 1 FROM stock_levels ORDER BY variant_id, location_id FOR UPDATE"))


def pending_sale_qty(session: Session) -> dict[tuple[int, int], int]:
    """Quantity of local sales NOT YET reflected in Odoo stock, per (variant, location).

    Includes FAILED events: Odoo has not deducted the stock but the units have already been sold. If FAILED is not
    counted, the next sync "revives" those units and the same units could be sold twice.
    A FAILED sale keeps reserving stock until the operator reruns its event (which then becomes DONE)."""
    rows = session.execute(
        text(
            "SELECT s.variant_id, s.location_id, SUM(s.qty) AS qty FROM sales s "
            "JOIN integration_events e ON e.aggregate_id = s.id AND e.event_type = 'SALE' "
            "WHERE e.status IN ('PENDING', 'PROCESSING', 'FAILED') GROUP BY s.variant_id, s.location_id"
        )
    ).all()
    return {(r.variant_id, r.location_id): int(r.qty) for r in rows}


def current_stock(session: Session) -> dict[tuple[int, int], tuple[int, int]]:
    rows = session.execute(text("SELECT variant_id, location_id, qty_sellable, qty_damaged FROM stock_levels")).all()
    return {(r.variant_id, r.location_id): (r.qty_sellable, r.qty_damaged) for r in rows}


def upsert_stock(session: Session, variant_id: int, location_id: int, sellable: int, damaged: int) -> None:
    session.execute(
        text(
            "INSERT INTO stock_levels (variant_id, location_id, qty_sellable, qty_damaged, synced_at) "
            "VALUES (:v, :l, :s, :d, now()) "
            "ON CONFLICT (variant_id, location_id) DO UPDATE SET qty_sellable = EXCLUDED.qty_sellable, "
            "qty_damaged = EXCLUDED.qty_damaged, synced_at = EXCLUDED.synced_at"
        ),
        {"v": variant_id, "l": location_id, "s": sellable, "d": damaged},
    )


def insert_movement(
    session: Session,
    variant_id: int,
    location_id: int,
    bucket: str,
    type_: str,
    qty_delta: int,
    ref_type: Optional[str] = None,
    ref_id: Optional[int] = None,
) -> None:
    session.execute(
        text(
            "INSERT INTO stock_movements (variant_id, location_id, bucket, type, qty_delta, ref_type, ref_id) "
            "VALUES (:v, :l, :b, :t, :d, :rt, :ri)"
        ),
        {"v": variant_id, "l": location_id, "b": bucket, "t": type_, "d": qty_delta, "rt": ref_type, "ri": ref_id},
    )


def has_any_location(session: Session) -> bool:
    return session.execute(text("SELECT 1 FROM locations LIMIT 1")).first() is not None


# ---------------- read ----------------

def location_exists(session: Session, code: str) -> bool:
    return session.execute(text("SELECT 1 FROM locations WHERE code = :c"), {"c": code}).first() is not None


def variant_exists(session: Session, variant_id: int) -> bool:
    return session.execute(text("SELECT 1 FROM variants WHERE id = :v"), {"v": variant_id}).first() is not None


def _escape_like(value: str) -> str:
    """Escape LIKE wildcard characters so that a search for '%' or '_' is treated as plain text."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def fetch_stock_rows(session: Session, location_code: Optional[str], q: Optional[str]):
    """One row per (variant, location), ordered: product, variant, then central warehouse first and shops after."""
    where, params = [], {}
    if location_code:
        where.append("l.code = :store")
        params["store"] = location_code
    if q:
        where.append(
            "(p.name ILIKE :pat ESCAPE '\\' OR v.name ILIKE :pat ESCAPE '\\' OR v.sku ILIKE :pat ESCAPE '\\')"
        )
        params["pat"] = f"%{_escape_like(q)}%"
    sql = (
        "SELECT v.id AS variant_id, v.sku, p.name AS product_name, v.name AS variant_name, "
        "v.low_stock_threshold AS threshold, l.code AS location_code, l.type AS location_type, "
        "sl.qty_sellable AS available, sl.qty_damaged AS damaged "
        "FROM stock_levels sl JOIN variants v ON v.id = sl.variant_id JOIN products p ON p.id = v.product_id "
        "JOIN locations l ON l.id = sl.location_id "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY p.name, v.name, v.id, (l.type = 'WAREHOUSE') DESC, l.code"
    )
    return session.execute(text(sql), params).all()


def fetch_movements(session: Session, variant_id: int, limit: int):
    return session.execute(
        text(
            "SELECT m.id, l.code AS location_code, m.bucket, m.type, m.qty_delta, m.ref_type, m.ref_id, m.created_at "
            "FROM stock_movements m JOIN locations l ON l.id = m.location_id WHERE m.variant_id = :v "
            "ORDER BY m.created_at DESC, m.id DESC LIMIT :limit"
        ),
        {"v": variant_id, "limit": limit},
    ).all()


# ---------------- single-row lookup (used by sales) ----------------

def get_variant(session: Session, variant_id: int):
    """Local variant together with its id in Odoo (required by the outbox event payload)."""
    return session.execute(
        text("SELECT id, odoo_variant_id FROM variants WHERE id = :v"), {"v": variant_id}
    ).first()


def get_location(session: Session, code: str):
    return session.execute(text("SELECT id, code, type FROM locations WHERE code = :c"), {"c": code}).first()


def get_sellable_qty(session: Session, variant_id: int, location_id: int) -> int:
    """Currently sellable stock; 0 if there is no stock row yet."""
    value = session.execute(
        text("SELECT qty_sellable FROM stock_levels WHERE variant_id = :v AND location_id = :l"),
        {"v": variant_id, "l": location_id},
    ).scalar_one_or_none()
    return value or 0
