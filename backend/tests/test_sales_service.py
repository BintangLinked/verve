"""Unit tests for the sales service (design section 7.1).

Purpose: to prove the sales business rules directly on the service, without HTTP:
stock location per channel, idempotency (replay vs reused key), full rollback when
stock is insufficient, and damaged returns that never become sellable stock.
"""
import pytest
from sqlalchemy import text

from app.errors import AppError
from app.schemas.sales import SaleRequest
from app.services.sales import record_damaged_return, record_sale


def cmd(vid, request_id="req-1", quantity=1, channel="POS", store_id="SHOP1", sku="TS-BLK-L"):
    return SaleRequest(request_id=request_id, variant_id=vid(sku), quantity=quantity, channel=channel, store_id=store_id)


def test_pos_sale_deducts_from_store(session, seeded, vid, qty):
    result, created = record_sale(session, cmd(vid, quantity=2))
    assert created is True
    assert (result.location_code, result.remaining, result.odoo_status) == ("SHOP1", 6, "PENDING")
    assert qty("TS-BLK-L", "SHOP1") == 6


def test_online_sale_deducts_from_warehouse_and_ignores_store_id(session, seeded, vid, qty):
    result, _ = record_sale(session, cmd(vid, channel="ONLINE", store_id="SHOP1"))
    assert result.location_code == "WH"
    assert qty("TS-BLK-L", "WH") == 39
    assert qty("TS-BLK-L", "SHOP1") == 8  # the store is untouched


def test_same_request_returns_replay_not_second_sale(session, seeded, vid, qty, count):
    record_sale(session, cmd(vid, quantity=2))
    result, created = record_sale(session, cmd(vid, quantity=2))
    assert created is False
    assert result.replayed is True
    assert qty("TS-BLK-L", "SHOP1") == 6  # deducted only once
    assert count("sales") == 1


def test_same_key_different_quantity_raises_key_reused(session, seeded, vid, qty):
    record_sale(session, cmd(vid, quantity=2))
    with pytest.raises(AppError) as exc:
        record_sale(session, cmd(vid, quantity=3))
    assert (exc.value.code, exc.value.status) == ("IDEMPOTENCY_KEY_REUSED", 409)
    assert qty("TS-BLK-L", "SHOP1") == 6


def test_insufficient_stock_rolls_back_everything(session, seeded, vid, qty, count):
    with pytest.raises(AppError) as exc:
        record_sale(session, cmd(vid, quantity=5, sku="TS-WHT-L", store_id="SHOP2"))  # stock is only 4
    assert (exc.value.code, exc.value.status) == ("INSUFFICIENT_STOCK", 409)
    assert exc.value.details == {"available": 4, "requested": 5}
    assert qty("TS-WHT-L", "SHOP2") == 4
    assert count("sales") == 0
    assert count("integration_events") == 0
    assert count("stock_movements", "type = 'SALE'") == 0


def test_rejected_key_not_consumed(session, seeded, vid, qty):
    request = cmd(vid, quantity=5, sku="TS-WHT-L", store_id="SHOP2")
    with pytest.raises(AppError):
        record_sale(session, request)
    session.execute(text("update stock_levels set qty_sellable = 10 where variant_id = :v and location_id = "
                         "(select id from locations where code = 'SHOP2')"), {"v": vid("TS-WHT-L")})
    session.commit()
    result, created = record_sale(session, request)  # same key, stock is now sufficient
    assert created is True and result.remaining == 5


def test_damaged_return_not_sellable(session, seeded, vid, qty):
    record_sale(session, cmd(vid, quantity=4, sku="TS-WHT-L", store_id="SHOP2"))  # stock becomes 0
    record_damaged_return(session, vid("TS-WHT-L"), "SHOP2", 3)
    assert qty("TS-WHT-L", "SHOP2") == 0  # damaged stock does not add to sellable stock
    damaged = session.execute(
        text("select qty_damaged from stock_levels where variant_id = :v and location_id = "
             "(select id from locations where code = 'SHOP2')"),
        {"v": vid("TS-WHT-L")},
    ).scalar_one()
    assert damaged == 3
    with pytest.raises(AppError) as exc:  # a damaged unit cannot be sold
        record_sale(session, cmd(vid, request_id="req-2", quantity=1, sku="TS-WHT-L", store_id="SHOP2"))
    assert exc.value.code == "INSUFFICIENT_STOCK"
