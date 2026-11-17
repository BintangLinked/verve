"""Tests for the replenishment API (design section 9).

Purpose: GET /api/replenishment only lists store/variant pairs below the threshold;
POST /api/replenishment/request creates an idempotent request with one outbox event,
rejects duplicate requests that are still PENDING, and does not change local stock (stock only
changes after the transfer completes in Odoo and the next sync).
"""
import pytest
from sqlalchemy import text


def body(vid, request_id="rep-1", store_id="SHOP2", quantity=6, sku="TS-WHT-L"):
    return {"request_id": request_id, "store_id": store_id, "variant_id": vid(sku), "quantity": quantity}


def test_list_returns_only_white_l_shop2_for_sample_data(client, seeded, vid):
    items = client.get("/api/replenishment").json()
    assert len(items) == 1
    assert items[0] == {
        "store_code": "SHOP2",
        "variant_id": vid("TS-WHT-L"),
        "sku": "TS-WHT-L",
        "variant_name": "White / L",
        "available": 4,
        "threshold": 5,
        "status": "LOW",
        "warehouse_available": 20,
        "suggested_quantity": 6,  # (5 * 2) - 4
        "pending_request": False,
    }


def test_list_flags_pending_request(client, seeded, vid):
    client.post("/api/replenishment/request", json=body(vid))
    assert client.get("/api/replenishment").json()[0]["pending_request"] is True


def test_request_creates_pending_and_outbox_event(client, seeded, vid, session):
    resp = client.post("/api/replenishment/request", json=body(vid))
    assert resp.status_code == 201
    data = resp.json()
    assert (data["status"], data["store_id"], data["quantity"], data["replayed"]) == ("PENDING", "SHOP2", 6, False)
    event = session.execute(
        text("select event_type, idempotency_key, status, payload from integration_events")
    ).one()
    assert (event.event_type, event.idempotency_key, event.status) == ("REPLENISHMENT", "REPLENISHMENT:rep-1", "PENDING")
    assert event.payload == {"odoo_variant_id": 103, "from_code": "WH", "to_code": "SHOP2", "qty": 6}


def test_request_replay_200(client, seeded, vid, count):
    assert client.post("/api/replenishment/request", json=body(vid)).status_code == 201
    resp = client.post("/api/replenishment/request", json=body(vid))
    assert resp.status_code == 200
    assert resp.json()["replayed"] is True
    assert count("replenishment_requests") == 1
    assert count("integration_events") == 1


def test_request_key_reused_409(client, seeded, vid):
    client.post("/api/replenishment/request", json=body(vid, quantity=6))
    resp = client.post("/api/replenishment/request", json=body(vid, quantity=7))
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")


def test_second_pending_request_same_pair_409(client, seeded, vid, count):
    client.post("/api/replenishment/request", json=body(vid, request_id="rep-1"))
    resp = client.post("/api/replenishment/request", json=body(vid, request_id="rep-2"))
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "OPEN_REQUEST_EXISTS")
    assert count("replenishment_requests") == 1
    assert count("integration_events") == 1


def test_new_request_allowed_after_previous_submitted(client, seeded, vid, session):
    client.post("/api/replenishment/request", json=body(vid, request_id="rep-1"))
    session.execute(text("update replenishment_requests set status = 'SUBMITTED'"))
    session.commit()
    assert client.post("/api/replenishment/request", json=body(vid, request_id="rep-2")).status_code == 201


def test_request_to_warehouse_422(client, seeded, vid, count):
    resp = client.post("/api/replenishment/request", json=body(vid, store_id="WH"))
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "INVALID_STORE")
    assert count("replenishment_requests") == 0


def test_request_unknown_store_and_variant_404(client, seeded, vid):
    unknown_store = client.post("/api/replenishment/request", json=body(vid, store_id="NOPE"))
    assert (unknown_store.status_code, unknown_store.json()["error"]["code"]) == (404, "STORE_NOT_FOUND")
    payload = {**body(vid), "variant_id": 999999}
    unknown_variant = client.post("/api/replenishment/request", json=payload)
    assert (unknown_variant.status_code, unknown_variant.json()["error"]["code"]) == (404, "VARIANT_NOT_FOUND")


@pytest.mark.parametrize("quantity", [0, -1, 1001, 1.5])
def test_request_invalid_quantity_422(client, seeded, vid, count, quantity):
    resp = client.post("/api/replenishment/request", json=body(vid, quantity=quantity))
    assert resp.status_code == 422
    assert count("replenishment_requests") == 0


def test_sale_and_replenishment_may_share_a_request_id(client, seeded, vid, count):
    # Two different clients may use the same request number; the event key is prefixed with the type,
    # so they do not collide (and the reference in Odoo is not ambiguous either).
    sale = {"request_id": "1001", "variant_id": vid("TS-BLK-L"), "quantity": 1, "channel": "POS", "store_id": "SHOP1"}
    assert client.post("/api/sales", json=sale).status_code == 201
    assert client.post("/api/replenishment/request", json=body(vid, request_id="1001")).status_code == 201
    assert count("integration_events") == 2


def test_request_does_not_change_stock(client, seeded, vid, qty):
    before = (qty("TS-WHT-L", "WH"), qty("TS-WHT-L", "SHOP2"))
    assert client.post("/api/replenishment/request", json=body(vid)).status_code == 201  # the request is actually created
    assert (qty("TS-WHT-L", "WH"), qty("TS-WHT-L", "SHOP2")) == before



@pytest.fixture(autouse=True)
def _sign_in(request):
    """All business endpoints require login: the tests in this file run as an authenticated admin."""
    if "client" in request.fixturenames:
        client = request.getfixturevalue("client")
        from tests.conftest import ADMIN_PASSWORD, ADMIN_USER

        assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).status_code == 200
