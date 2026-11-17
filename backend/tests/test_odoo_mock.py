"""Tests for MockOdooClient (a stand-in for Odoo until an Odoo instance is available).

Purpose: to prove that the mock behaves like Odoo in the respects that matter to the integration:
stock decreases on a sale, the same reference does not deduct stock twice,
business rejection, transient failure (can be retried), and state shared across processes.
"""
import pytest

from app.odoo.errors import OdooBusinessError, OdooTransientError
from app.odoo.mock import MockOdooClient


def vid(client, sku: str) -> int:
    """Look up odoo_variant_id from the SKU."""
    return next(v.odoo_variant_id for v in client.list_variants() if v.sku == sku)


def qty(client, sku: str, location: str) -> int:
    """Sellable stock of one variant at one location according to the Odoo mock."""
    v = vid(client, sku)
    return next(s.qty_sellable for s in client.get_stock() if s.odoo_variant_id == v and s.location_code == location)


def test_get_stock_matches_sample(odoo):
    assert len(odoo.list_variants()) == 5
    assert {loc.code for loc in odoo.list_locations()} == {"WH", "SHOP1", "SHOP2"}
    assert len(odoo.get_stock()) == 15
    assert qty(odoo, "TS-BLK-L", "WH") == 40
    assert qty(odoo, "TS-BLK-L", "SHOP1") == 8
    assert qty(odoo, "TS-BLK-L", "SHOP2") == 6
    assert qty(odoo, "TS-WHT-L", "SHOP2") == 4


def test_post_sale_decrements_odoo_stock(odoo):
    result = odoo.post_sale("req-1", vid(odoo, "TS-BLK-L"), "SHOP1", 2)
    assert result.duplicate is False
    assert qty(odoo, "TS-BLK-L", "SHOP1") == 6


def test_post_sale_same_reference_is_duplicate(odoo):
    first = odoo.post_sale("req-1", vid(odoo, "TS-BLK-L"), "SHOP1", 2)
    second = odoo.post_sale("req-1", vid(odoo, "TS-BLK-L"), "SHOP1", 2)
    assert second.duplicate is True
    assert second.odoo_ref == first.odoo_ref
    assert qty(odoo, "TS-BLK-L", "SHOP1") == 6  # deducted only once


def test_post_sale_insufficient_raises_business_error(odoo):
    with pytest.raises(OdooBusinessError):
        odoo.post_sale("req-1", vid(odoo, "TS-BLK-L"), "SHOP1", 9)
    assert qty(odoo, "TS-BLK-L", "SHOP1") == 8
    # A rejected reference is not consumed: a retry with a valid quantity succeeds.
    assert odoo.post_sale("req-1", vid(odoo, "TS-BLK-L"), "SHOP1", 1).duplicate is False


def test_fail_next_transient_then_recovers(odoo):
    odoo.fail_next("transient", 1)
    with pytest.raises(OdooTransientError):
        odoo.post_sale("req-1", vid(odoo, "TS-BLK-L"), "SHOP1", 1)
    assert qty(odoo, "TS-BLK-L", "SHOP1") == 8  # the failure does not deduct stock
    assert odoo.post_sale("req-1", vid(odoo, "TS-BLK-L"), "SHOP1", 1).duplicate is False
    assert qty(odoo, "TS-BLK-L", "SHOP1") == 7


def test_fail_next_applies_to_get_stock(odoo):
    # Sync reads stock through get_stock; an artificial failure must be able to make it fail.
    odoo.fail_next("transient", 1)
    with pytest.raises(OdooTransientError):
        odoo.get_stock()
    assert len(odoo.get_stock()) == 15


def test_fail_next_business_raises_business_error(odoo):
    odoo.fail_next("business", 1)
    with pytest.raises(OdooBusinessError):
        odoo.post_sale("req-1", vid(odoo, "TS-BLK-L"), "SHOP1", 1)


def test_post_transfer_moves_stock(odoo):
    odoo.post_transfer("rep-1", vid(odoo, "TS-WHT-L"), "WH", "SHOP2", 5)
    assert qty(odoo, "TS-WHT-L", "WH") == 15
    assert qty(odoo, "TS-WHT-L", "SHOP2") == 9


def test_state_shared_across_instances(engine, odoo):
    # Simulates the API and the worker: two different processes, one "Odoo".
    other = MockOdooClient(engine)
    odoo.post_sale("req-1", vid(odoo, "TS-BLK-L"), "SHOP1", 3)
    assert qty(other, "TS-BLK-L", "SHOP1") == 5
