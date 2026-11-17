"""Inventory read business logic: stock status, filters, and movement history.

Purpose: converts SQL rows into API responses, and enforces the stock status rules
(design section 5): OUT (=0), LOW (0 < qty < threshold, strict), OK (>= threshold); central warehouse null.

Relations: uses repositories/inventory.py; `stock_status` is also used by services/replenishment.py (Task 6).
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.errors import AppError
from app.repositories import inventory as repo
from app.schemas.inventory import InventoryItem, InventoryResponse, Movement, StockCell


def stock_status(qty: int, threshold: int, location_type: str) -> Optional[str]:
    """Stock status of one variant at one location. The central warehouse has no threshold, so None."""
    if location_type == "WAREHOUSE":
        return None
    if qty == 0:
        return "OUT"
    if qty < threshold:
        return "LOW"
    return "OK"


def get_inventory(
    session: Session, store: Optional[str], q: Optional[str], status: Optional[str]
) -> InventoryResponse:
    if store and not repo.location_exists(session, store):
        raise AppError("STORE_NOT_FOUND", f"Unknown store '{store}'", 404)

    items: dict[int, InventoryItem] = {}  # dict preserves the variant order from the query
    for r in repo.fetch_stock_rows(session, store, q):
        item = items.get(r.variant_id)
        if item is None:
            item = items[r.variant_id] = InventoryItem(
                variant_id=r.variant_id,
                sku=r.sku,
                product_name=r.product_name,
                variant_name=r.variant_name,
                threshold=r.threshold,
                stock=[],
            )
        item.stock.append(
            StockCell(
                location_code=r.location_code,
                available=r.available,
                damaged=r.damaged,
                status=stock_status(r.available, r.threshold, r.location_type),
            )
        )

    result = list(items.values())
    if status:
        # Keep variants that have at least one location (within the store filter) with that status.
        result = [i for i in result if any(c.status == status for c in i.stock)]
    return InventoryResponse(as_of=datetime.now(timezone.utc), items=result)


def get_movements(session: Session, variant_id: int, limit: int) -> list[Movement]:
    if not repo.variant_exists(session, variant_id):
        raise AppError("VARIANT_NOT_FOUND", f"Unknown variant {variant_id}", 404)
    return [Movement(**r._mapping) for r in repo.fetch_movements(session, variant_id, limit)]
