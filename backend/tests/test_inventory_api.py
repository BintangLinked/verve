"""Tests for the inventory read API: GET /api/inventory and GET /api/inventory/{id}/movements.

Purpose: to confirm the API contract (design section 9): store/q/status filters, error codes,
the no-store header, and movement history ordered newest first.
"""
import pytest


@pytest.fixture()
def synced(client):
    """Populate the projection from the Odoo mock (sample data) before the test runs."""
    assert client.post("/api/inventory/sync").status_code == 200


def skus(resp):
    assert resp.status_code == 200
    return sorted(i["sku"] for i in resp.json()["items"])


def test_filter_by_store(client, synced):
    resp = client.get("/api/inventory", params={"store": "SHOP1"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert [c["location_code"] for c in item["stock"]] == ["SHOP1"]


def test_filter_unknown_store_404(client, synced):
    resp = client.get("/api/inventory", params={"store": "NOPE"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "STORE_NOT_FOUND"


def test_filter_invalid_status_422(client, synced):
    resp = client.get("/api/inventory", params={"status": "BROKEN"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_filter_status_low(client, synced):
    assert skus(client.get("/api/inventory", params={"status": "LOW"})) == ["TS-WHT-L"]


def test_filter_status_out_is_empty_for_sample_data(client, synced):
    assert skus(client.get("/api/inventory", params={"status": "OUT"})) == []


def test_search_matches_sku_and_name_case_insensitive(client, synced):
    assert skus(client.get("/api/inventory", params={"q": "serum"})) == ["SERUM-50"]
    assert skus(client.get("/api/inventory", params={"q": "TOTE"})) == ["TOTE-ECO"]
    assert skus(client.get("/api/inventory", params={"q": "blk"})) == ["TS-BLK-L", "TS-BLK-M"]
    assert skus(client.get("/api/inventory", params={"q": "zzz"})) == []


def test_inventory_response_is_not_cacheable(client, synced):
    resp = client.get("/api/inventory")
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json()["as_of"]


def test_movements_newest_first_and_404(client, odoo, synced):
    body = client.get("/api/inventory").json()
    variant_id = next(i["variant_id"] for i in body["items"] if i["sku"] == "TS-BLK-L")

    # Change stock directly in "Odoo" and then sync again: it must be recorded as the latest adjustment.
    odoo.post_sale("direct-1", 101, "SHOP1", 2)
    client.post("/api/inventory/sync")

    resp = client.get(f"/api/inventory/{variant_id}/movements")
    assert resp.status_code == 200
    movements = resp.json()
    assert len(movements) == 4  # 3 from the initial sync + 1 adjustment
    newest = movements[0]
    assert (newest["type"], newest["qty_delta"], newest["location_code"], newest["bucket"]) == (
        "SYNC_ADJUSTMENT",
        -2,
        "SHOP1",
        "sellable",
    )
    assert len(client.get(f"/api/inventory/{variant_id}/movements", params={"limit": 2}).json()) == 2
    assert client.get(f"/api/inventory/{variant_id}/movements", params={"limit": 0}).status_code == 422
    assert client.get("/api/inventory/999999/movements").status_code == 404



@pytest.fixture(autouse=True)
def _sign_in(request):
    """All business endpoints require login: the tests in this file run as an authenticated admin."""
    if "client" in request.fixturenames:
        client = request.getfixturevalue("client")
        from tests.conftest import ADMIN_PASSWORD, ADMIN_USER

        assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).status_code == 200
