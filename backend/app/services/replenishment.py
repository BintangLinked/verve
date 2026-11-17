"""Replenishment business logic (design section 9).

Purpose:
- Needs list: shop/variant pairs with stock < threshold (strict), together with central warehouse stock and
  suggested quantity = 2 x threshold - current stock.
- Request: created idempotently (PENDING + one REPLENISHMENT outbox event in one transaction).
  A request does NOT change or reserve local stock and does not check warehouse stock; stock only
  changes after the worker sends the transfer to Odoo and the next sync reads the result.

Relations: uses stock_status from services/inventory.py, fingerprint from services/idempotency.py,
repositories/{replenishment,inventory,events}.py. The event is consumed by services/outbox.py (creates a central
warehouse -> shop transfer in Odoo).
"""
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.repositories import events as events_repo
from app.repositories import inventory as inventory_repo
from app.repositories import replenishment as repo
from app.schemas.replenishment import ReplenishmentItem, ReplenishmentRequest, ReplenishmentResponse
from app.services.idempotency import fingerprint
from app.services.inventory import stock_status

log = logging.getLogger("app.replenishment")

MAIN_WAREHOUSE = "WH"  # source of all replenishment


def list_replenishment(session: Session) -> list[ReplenishmentItem]:
    return [
        ReplenishmentItem(
            store_code=r.store_code,
            variant_id=r.variant_id,
            sku=r.sku,
            variant_name=r.variant_name,
            available=r.available,
            threshold=r.threshold,
            status=stock_status(r.available, r.threshold, "STORE"),
            warehouse_available=r.warehouse_available,
            suggested_quantity=r.threshold * 2 - r.available,
            pending_request=r.pending_request,
        )
        for r in repo.fetch_below_threshold(session)
    ]


def _response(row, store_code: str, replayed: bool) -> ReplenishmentResponse:
    return ReplenishmentResponse(
        id=row.id,
        request_id=row.request_id,
        store_id=store_code,
        variant_id=row.variant_id,
        quantity=row.qty_requested,
        status=row.status,
        replayed=replayed,
    )


def create_request(session: Session, cmd: ReplenishmentRequest) -> tuple[ReplenishmentResponse, bool]:
    """Create a replenishment request. Returns (response, newly_created); False means replay."""
    variant = inventory_repo.get_variant(session, cmd.variant_id)
    if variant is None:
        raise AppError("VARIANT_NOT_FOUND", f"Unknown variant {cmd.variant_id}", 404)
    store = inventory_repo.get_location(session, cmd.store_id)
    if store is None:
        raise AppError("STORE_NOT_FOUND", f"Unknown store '{cmd.store_id}'", 404)
    if store.type != "STORE":
        raise AppError("INVALID_STORE", "Replenishment must target a store location", 422)
    payload_hash = fingerprint(store.code, cmd.variant_id, cmd.quantity)

    try:
        row = repo.insert_request_if_new(
            session,
            request_id=cmd.request_id,
            payload_hash=payload_hash,
            store_location_id=store.id,
            variant_id=variant.id,
            qty=cmd.quantity,
        )
        if row is None:
            existing = repo.get_request_by_request_id(session, cmd.request_id)
            session.rollback()
            if existing.payload_hash != payload_hash:
                raise AppError("IDEMPOTENCY_KEY_REUSED", "request_id was already used with a different payload", 409)
            log.info("replenishment.replayed request_id=%s", cmd.request_id)
            return _response(existing, existing.store_code, replayed=True), False

        events_repo.insert_event(
            session,
            "REPLENISHMENT",
            row.id,
            f"REPLENISHMENT:{cmd.request_id}",  # type prefix: does not collide with sale keys
            {"odoo_variant_id": variant.odoo_variant_id, "from_code": MAIN_WAREHOUSE, "to_code": store.code,
             "qty": cmd.quantity},
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # Only a partial unique index violation (migration 0004) means "a PENDING request already exists
        # for this shop/variant pair". Any other violation is a bug and must not be disguised.
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != "ux_replenishment_one_pending":
            raise
        log.warning("replenishment.rejected reason=open_request_exists request_id=%s", cmd.request_id)
        raise AppError("OPEN_REQUEST_EXISTS", "A pending request already exists for this store and variant", 409) from exc
    except AppError:
        raise
    except Exception:
        session.rollback()
        raise

    log.info("replenishment.created request_id=%s store=%s qty=%d", cmd.request_id, store.code, cmd.quantity)
    return _response(row, store.code, replayed=False), True
