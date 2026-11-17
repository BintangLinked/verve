"""Sales business logic (design sections 7.1 to 7.3).

Purpose: records sales safely. All steps run in ONE PostgreSQL transaction:
  1. Idempotency gate    : INSERT sales ... ON CONFLICT (request_id) DO NOTHING
  2. Atomic stock deduction: UPDATE ... WHERE qty_sellable >= n (failure -> roll back everything)
  3. Record the stock movement (SALE)
  4. Write the outbox event (PENDING) so the worker sends it to Odoo
  5. Commit

Location rule: POS deducts the stock of the named shop; ONLINE always deducts the central warehouse (WH).
A request rejected because of insufficient stock does NOT consume its request_id (full rollback).

Relations: uses repositories/{sales,inventory,events}.py; called by routers/sales.py.
The event written here is consumed by services/outbox.py (Task 5).
"""
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.repositories import events as events_repo
from app.repositories import inventory as inventory_repo
from app.repositories import sales as repo
from app.schemas.sales import SaleRequest, SaleResponse
from app.services.idempotency import fingerprint

log = logging.getLogger("app.sales")

MAIN_WAREHOUSE = "WH"  # online sales are always fulfilled from the central warehouse


def _resolve_location(session: Session, cmd: SaleRequest):
    code = MAIN_WAREHOUSE if cmd.channel == "ONLINE" else cmd.store_id
    location = inventory_repo.get_location(session, code)
    if location is None:
        raise AppError("STORE_NOT_FOUND", f"Unknown store '{code}'", 404)
    if cmd.channel == "POS" and location.type != "STORE":
        raise AppError("INVALID_STORE", "POS sales must target a store location", 422)
    return location


def _payload_hash(cmd: SaleRequest, location_code: str) -> str:
    """Fingerprint of the sale content; store_id for ONLINE is excluded (the resolved location is what is counted)."""
    return fingerprint(cmd.variant_id, cmd.quantity, cmd.channel, location_code)


def _response(sale, location_code: str, remaining: int, replayed: bool) -> SaleResponse:
    return SaleResponse(
        id=sale.id,
        request_id=sale.request_id,
        variant_id=sale.variant_id,
        location_code=location_code,
        quantity=sale.qty,
        channel=sale.channel,
        remaining=remaining,
        odoo_status=sale.odoo_status,
        replayed=replayed,
    )


def record_sale(session: Session, cmd: SaleRequest) -> tuple[SaleResponse, bool]:
    """Record a sale. Returns (response, newly_created). newly_created=False means replay."""
    variant = inventory_repo.get_variant(session, cmd.variant_id)
    if variant is None:
        raise AppError("VARIANT_NOT_FOUND", f"Unknown variant {cmd.variant_id}", 404)
    location = _resolve_location(session, cmd)
    payload_hash = _payload_hash(cmd, location.code)

    try:
        # Step 1: idempotency gate.
        sale = repo.insert_sale_if_new(
            session,
            request_id=cmd.request_id,
            payload_hash=payload_hash,
            variant_id=variant.id,
            location_id=location.id,
            qty=cmd.quantity,
            channel=cmd.channel,
        )
        if sale is None:
            existing = repo.get_sale_by_request_id(session, cmd.request_id)
            remaining = inventory_repo.get_sellable_qty(session, existing.variant_id, existing.location_id)
            session.rollback()  # nothing was written; end the transaction
            if existing.payload_hash != payload_hash:
                log.warning("sale.rejected reason=key_reused request_id=%s", cmd.request_id)
                raise AppError(
                    "IDEMPOTENCY_KEY_REUSED", "request_id was already used with a different payload", 409
                )
            log.info("sale.replayed request_id=%s", cmd.request_id)
            return _response(existing, existing.location_code, remaining, replayed=True), False

        # Step 2: deduct stock atomically. IntegrityError (CHECK qty >= 0) is only the last safeguard.
        try:
            remaining = repo.decrement_stock(session, variant.id, location.id, cmd.quantity)
        except IntegrityError:
            session.rollback()
            remaining = None
        if remaining is None:
            session.rollback()  # also rolls back the sale row from step 1
            available = inventory_repo.get_sellable_qty(session, variant.id, location.id)
            session.rollback()
            log.warning(
                "sale.rejected reason=insufficient_stock request_id=%s available=%d requested=%d",
                cmd.request_id, available, cmd.quantity,
            )
            raise AppError(
                "INSUFFICIENT_STOCK",
                "Not enough sellable stock",
                409,
                {"available": available, "requested": cmd.quantity},
            )

        # Steps 3 and 4: stock history and outbox event, in the same transaction.
        inventory_repo.insert_movement(session, variant.id, location.id, "sellable", "SALE", -cmd.quantity, "SALE", sale.id)
        events_repo.insert_event(
            session,
            "SALE",
            sale.id,
            f"SALE:{cmd.request_id}",  # type prefix: event key namespace separate from replenishment
            {"odoo_variant_id": variant.odoo_variant_id, "location_code": location.code, "qty": cmd.quantity},
        )
        session.commit()  # step 5
    except AppError:
        raise
    except Exception:
        session.rollback()
        raise

    log.info("sale.created request_id=%s location=%s qty=%d remaining=%d", cmd.request_id, location.code, cmd.quantity, remaining)
    return _response(sale, location.code, remaining, replayed=False), True


def record_damaged_return(session: Session, variant_id: int, location_code: str, quantity: int) -> None:
    """Record a damaged return: increases qty_damaged only, does NOT increase sellable stock (design 6.3).
    Has no public endpoint yet; used by tests and can be called from an operational script."""
    if quantity < 1:
        raise AppError("VALIDATION_ERROR", "quantity must be at least 1", 422)
    if inventory_repo.get_variant(session, variant_id) is None:
        raise AppError("VARIANT_NOT_FOUND", f"Unknown variant {variant_id}", 404)
    location = inventory_repo.get_location(session, location_code)
    if location is None:
        raise AppError("STORE_NOT_FOUND", f"Unknown location '{location_code}'", 404)
    try:
        repo.add_damaged(session, variant_id, location.id, quantity)
        inventory_repo.insert_movement(session, variant_id, location.id, "damaged", "RETURN_DAMAGED", quantity, "RETURN")
        session.commit()
    except Exception:
        session.rollback()
        raise
