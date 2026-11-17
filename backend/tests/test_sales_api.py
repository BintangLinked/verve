"""Integration tests for the POST /api/sales API against a real PostgreSQL.

Purpose: to confirm the HTTP contract (status codes, error shape), the effect on the database (one
PENDING outbox row per sale), and that invalid input writes nothing.
"""
import pytest
from sqlalchemy import text


def body(vid, request_id="req-1", quantity=1, channel="POS", store_id="SHOP1", sku="TS-BLK-L"):
    payload = {"request_id": request_id, "variant_id": vid(sku), "quantity": quantity, "channel": channel}
    if store_id is not None:
        payload["store_id"] = store_id
    return payload


def test_post_sale_201_and_outbox_pending(client, seeded, vid, session):
    resp = client.post("/api/sales", json=body(vid))
    assert resp.status_code == 201
    data = resp.json()
    assert (data["location_code"], data["quantity"], data["remaining"]) == ("SHOP1", 1, 7)
    assert (data["odoo_status"], data["replayed"]) == ("PENDING", False)
    event = session.execute(text("select status, idempotency_key, event_type from integration_events")).one()
    assert tuple(event) == ("PENDING", "SALE:req-1", "SALE")


def test_post_sale_replay_200(client, seeded, vid):
    assert client.post("/api/sales", json=body(vid)).status_code == 201
    resp = client.post("/api/sales", json=body(vid))
    assert resp.status_code == 200
    assert resp.json()["replayed"] is True


def test_post_sale_409_codes(client, seeded, vid):
    too_many = client.post("/api/sales", json=body(vid, quantity=9))
    assert (too_many.status_code, too_many.json()["error"]["code"]) == (409, "INSUFFICIENT_STOCK")
    client.post("/api/sales", json=body(vid, request_id="dup", quantity=1))
    reused = client.post("/api/sales", json=body(vid, request_id="dup", quantity=2))
    assert (reused.status_code, reused.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")


@pytest.mark.parametrize("quantity", [0, -1, 1.5, 1001, "abc", True])
def test_invalid_quantity_422(client, seeded, vid, count, quantity):
    resp = client.post("/api/sales", json=body(vid, quantity=quantity))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert count("sales") == 0


def test_missing_fields_422(client, seeded, count):
    resp = client.post("/api/sales", json={})
    assert resp.status_code == 422
    assert count("sales") == 0


def test_pos_requires_store_id_422(client, seeded, vid, count):
    resp = client.post("/api/sales", json=body(vid, store_id=None))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert count("sales") == 0


def test_pos_unknown_store_404(client, seeded, vid, count):
    resp = client.post("/api/sales", json=body(vid, store_id="NOPE"))
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "STORE_NOT_FOUND")
    assert count("sales") == 0


def test_pos_with_warehouse_code_422(client, seeded, vid, count):
    resp = client.post("/api/sales", json=body(vid, store_id="WH"))
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "INVALID_STORE")
    assert count("sales") == 0


def test_unknown_variant_404(client, seeded, count):
    resp = client.post("/api/sales", json={"request_id": "x", "variant_id": 999999, "quantity": 1,
                                           "channel": "POS", "store_id": "SHOP1"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "VARIANT_NOT_FOUND")
    assert count("sales") == 0



@pytest.fixture(autouse=True)
def _sign_in(request):
    """All business endpoints require login: the tests in this file run as an authenticated admin."""
    if "client" in request.fixturenames:
        client = request.getfixturevalue("client")
        from tests.conftest import ADMIN_PASSWORD, ADMIN_USER

        assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).status_code == 200
