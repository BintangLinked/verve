"""Tests for the integration events operational API (design section 9).

Purpose: an operator can view outbox events (especially FAILED ones) and requeue them
once the problem is fixed, without touching the database directly.
"""
from sqlalchemy import text


def make_sale(client, vid, request_id):
    body = {"request_id": request_id, "variant_id": vid("TS-BLK-L"), "quantity": 1, "channel": "POS", "store_id": "SHOP1"}
    assert client.post("/api/sales", json=body).status_code == 201


def set_status(session, key, status):
    session.execute(
        text("update integration_events set status = :s, attempts = 3, last_error = 'boom' where idempotency_key = :k"),
        {"s": status, "k": key},
    )
    session.commit()


def test_events_endpoints_require_login(client):
    assert client.get("/api/integration-events").status_code == 401
    assert client.post("/api/integration-events/1/retry").status_code == 401


def test_list_filter_by_status(client, admin_client, seeded, vid, session):
    make_sale(client, vid, "a")
    make_sale(client, vid, "b")
    set_status(session, "SALE:b", "FAILED")

    all_events = client.get("/api/integration-events").json()
    assert sorted(e["idempotency_key"] for e in all_events) == ["SALE:a", "SALE:b"]

    failed = client.get("/api/integration-events", params={"status": "FAILED"}).json()
    assert [(e["idempotency_key"], e["status"], e["last_error"]) for e in failed] == [("SALE:b", "FAILED", "boom")]
    assert client.get("/api/integration-events", params={"status": "NOPE"}).status_code == 422


def test_retry_failed_event_resets_to_pending(client, admin_client, seeded, vid, session):
    make_sale(client, vid, "a")
    set_status(session, "SALE:a", "FAILED")
    session.execute(text("update sales set odoo_status = 'FAILED' where request_id = 'a'"))
    session.commit()
    event_id = session.execute(text("select id from integration_events")).scalar_one()

    resp = client.post(f"/api/integration-events/{event_id}/retry")

    assert resp.status_code == 200
    body = resp.json()
    assert (body["status"], body["attempts"], body["last_error"]) == ("PENDING", 0, None)
    assert session.execute(text("select odoo_status from sales where request_id = 'a'")).scalar_one() == "PENDING"


def test_retry_non_failed_409(client, admin_client, seeded, vid, session):
    make_sale(client, vid, "a")
    event_id = session.execute(text("select id from integration_events")).scalar_one()
    resp = client.post(f"/api/integration-events/{event_id}/retry")
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "NOT_FAILED")


def test_retry_unknown_event_404(client, admin_client, seeded):
    resp = client.post("/api/integration-events/999999/retry")
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "EVENT_NOT_FOUND")
