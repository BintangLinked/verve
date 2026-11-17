"""Tests for stock synchronisation from Odoo to the local projection (design section 8.7).

Purpose: to prove that sync (1) loads the initial data from Odoo, (2) does not overwrite
local sales not yet delivered to Odoo, (3) records an adjustment only when there is a
change, and (4) changes nothing when Odoo is unreachable.
"""
import pytest
from sqlalchemy import text


def inventory(client, **params):
    resp = client.get("/api/inventory", params=params)
    assert resp.status_code == 200
    return resp.json()


def cell(body, sku, location):
    item = next(i for i in body["items"] if i["sku"] == sku)
    return next(c for c in item["stock"] if c["location_code"] == location)


def test_sync_seeds_sample_data(client):
    resp = client.post("/api/inventory/sync")
    assert resp.status_code == 200
    assert resp.json() == {"variants": 5, "locations": 3, "adjustments": 15}
    body = inventory(client)
    white_shop2 = cell(body, "TS-WHT-L", "SHOP2")
    assert white_shop2["available"] == 4
    assert white_shop2["status"] == "LOW"
    assert cell(body, "TS-BLK-L", "WH")["status"] is None  # the central warehouse has no threshold


def test_second_sync_without_changes_writes_no_adjustments(client):
    client.post("/api/inventory/sync")
    resp = client.post("/api/inventory/sync")
    assert resp.json()["adjustments"] == 0


def add_unsent_sale(session, status):
    """Sale of 2 units of TS-BLK-L at SHOP1 whose event has not yet been received by Odoo, with a given event status."""
    variant_id = session.execute(text("select id from variants where sku = 'TS-BLK-L'")).scalar_one()
    location_id = session.execute(text("select id from locations where code = 'SHOP1'")).scalar_one()
    sale_id = session.execute(
        text(
            "insert into sales (request_id, payload_hash, variant_id, location_id, qty, channel) "
            "values ('r1', 'h', :v, :l, 2, 'POS') returning id"
        ),
        {"v": variant_id, "l": location_id},
    ).scalar_one()
    session.execute(
        text(
            "insert into integration_events (event_type, aggregate_id, idempotency_key, payload, status) "
            "values ('SALE', :s, 'SALE:r1', '{}'::jsonb, :st)"
        ),
        {"s": sale_id, "st": status},
    )
    session.commit()


def test_sync_subtracts_pending_sales(client, session):
    client.post("/api/inventory/sync")
    add_unsent_sale(session, "PENDING")
    client.post("/api/inventory/sync")
    # Odoo is still 8 (the sale has not been delivered); projection = 8 - 2 pending.
    assert cell(inventory(client), "TS-BLK-L", "SHOP1")["available"] == 6


def test_sync_keeps_failed_sale_deducted(client, session):
    # A sale that failed to be delivered to Odoo must NOT be "revived" through sync: Odoo has not yet
    # deducted its stock, but that unit has already been sold. Otherwise the same unit could be sold twice.
    client.post("/api/inventory/sync")
    add_unsent_sale(session, "FAILED")
    client.post("/api/inventory/sync")
    assert cell(inventory(client), "TS-BLK-L", "SHOP1")["available"] == 6


def test_sync_odoo_down_leaves_projection_unchanged(client, odoo):
    client.post("/api/inventory/sync")
    before = inventory(client)["items"]

    odoo.fail_next("transient", 1)
    resp = client.post("/api/inventory/sync")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "ODOO_UNAVAILABLE"
    assert inventory(client)["items"] == before



@pytest.fixture(autouse=True)
def _sign_in(request):
    """All business endpoints require login: the tests in this file run as an authenticated admin."""
    if "client" in request.fixturenames:
        client = request.getfixturevalue("client")
        from tests.conftest import ADMIN_PASSWORD, ADMIN_USER

        assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).status_code == 200
