"""Synchronisation of master data and stock from Odoo to the local projection (design section 8.7).

Purpose: Odoo is the source of truth for stock; sync copies master data and quantities to the
local tables. Projection = Odoo stock minus local sales NOT YET reflected in Odoo
(outbox events PENDING, PROCESSING, or FAILED), so sales not yet sent, including those that
failed and await an operator decision, are not overwritten and their units cannot be sold twice.

Guarantees:
- All Odoo reads complete BEFORE any write; if Odoo fails, nothing changes.
- Stock rows are locked before calculating pending sales, so concurrent sales
  wait and are not lost through being overwritten.
- Every quantity change is recorded as a SYNC_ADJUSTMENT movement (shown in the detail drawer).

Relations: uses OdooClient (app/odoo/base.py) and repositories/inventory.py; called by
routers/inventory.py (POST /api/inventory/sync) and by the app/main.py lifespan when the database is still empty.
"""
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.errors import AppError
from app.odoo.base import OdooClient
from app.odoo.errors import OdooBusinessError, OdooTransientError
from app.repositories import inventory as repo

log = logging.getLogger("app.sync")


@dataclass(frozen=True)
class SyncResult:
    variants: int
    locations: int
    adjustments: int


def sync_from_odoo(session: Session, client: OdooClient) -> SyncResult:
    # 1) Read everything from Odoo first (nothing is written to the database yet).
    try:
        locations = client.list_locations()
        variants = client.list_variants()
        stock = client.get_stock()
    except (OdooTransientError, OdooBusinessError) as exc:
        log.warning("sync.odoo_unavailable error=%s", exc)
        raise AppError("ODOO_UNAVAILABLE", "Odoo is unavailable", 502) from exc

    try:
        # 2) Master data.
        location_ids = {loc.code: repo.upsert_location(session, loc) for loc in locations}
        variant_ids = {}
        for v in variants:
            product_id = repo.upsert_product(session, v.odoo_template_id, v.product_name)
            variant_ids[v.odoo_variant_id] = repo.upsert_variant(session, product_id, v)

        # 3) Lock stock, then calculate pending sales and current quantities.
        repo.lock_stock_levels(session)
        pending = repo.pending_sale_qty(session)
        current = repo.current_stock(session)

        # 4) Write the projection and record the adjustments.
        adjustments = 0
        for s in stock:
            vid, lid = variant_ids.get(s.odoo_variant_id), location_ids.get(s.location_code)
            if vid is None or lid is None:
                continue  # stock for variants/locations that are not in the Odoo master data
            sellable = max(s.qty_sellable - pending.get((vid, lid), 0), 0)
            old_sellable, old_damaged = current.get((vid, lid), (0, 0))
            repo.upsert_stock(session, vid, lid, sellable, s.qty_damaged)
            if sellable != old_sellable:
                repo.insert_movement(session, vid, lid, "sellable", "SYNC_ADJUSTMENT", sellable - old_sellable, "SYNC")
                adjustments += 1
            if s.qty_damaged != old_damaged:
                repo.insert_movement(session, vid, lid, "damaged", "SYNC_ADJUSTMENT", s.qty_damaged - old_damaged, "SYNC")
                adjustments += 1
        session.commit()
    except Exception:
        session.rollback()
        raise

    log.info("sync.done variants=%d locations=%d adjustments=%d", len(variants), len(locations), adjustments)
    return SyncResult(len(variants), len(locations), adjustments)


def seed_if_empty(session_factory, client: OdooClient) -> None:
    """Called when the application starts: load the initial data if the location table is still empty.
    A failure is only logged; the application keeps running and sync can be triggered manually."""
    try:
        with session_factory() as session:
            if repo.has_any_location(session):
                return
            sync_from_odoo(session, client)
    except Exception:
        log.exception("sync.seed_failed")
