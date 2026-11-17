"""Concurrency tests: proof that overselling and duplicate sales are not possible (design 7.2 and 7.3).

Purpose: two requests are sent truly simultaneously (thread + barrier) to a real PostgreSQL.
The safeguard is not a lock in the application code, but a single atomic UPDATE
(`WHERE qty_sellable >= n`) and a unique constraint on request_id.
"""
import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import text


def fire_together(app, payloads):
    """Send one request per payload simultaneously; return the list of HTTP statuses."""
    barrier = Barrier(len(payloads))

    def post(payload):
        client = TestClient(app)  # one client per thread
        from tests.conftest import ADMIN_PASSWORD, ADMIN_USER

        client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})  # the endpoint requires login
        barrier.wait()  # login completes first; only the sales are raced
        return client.post("/api/sales", json=payload).status_code

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return sorted(pool.map(post, payloads))


def set_stock(session, sku, location, quantity):
    session.execute(
        text(
            "update stock_levels set qty_sellable = :q where "
            "variant_id = (select id from variants where sku = :s) and "
            "location_id = (select id from locations where code = :l)"
        ),
        {"q": quantity, "s": sku, "l": location},
    )
    session.commit()


def test_two_buyers_last_unit(client, seeded, vid, session, qty, count):
    set_stock(session, "TS-BLK-L", "SHOP1", 1)
    base = {"variant_id": vid("TS-BLK-L"), "quantity": 1, "channel": "POS", "store_id": "SHOP1"}

    statuses = fire_together(client.app, [{**base, "request_id": "buyer-a"}, {**base, "request_id": "buyer-b"}])

    assert statuses == [201, 409]  # exactly one succeeds
    assert qty("TS-BLK-L", "SHOP1") == 0
    assert count("sales") == 1
    assert count("integration_events") == 1


def test_identical_concurrent_requests_one_sale(client, seeded, vid, session, qty, count):
    set_stock(session, "TS-BLK-L", "SHOP1", 5)
    payload = {"request_id": "same", "variant_id": vid("TS-BLK-L"), "quantity": 1, "channel": "POS", "store_id": "SHOP1"}

    statuses = fire_together(client.app, [payload, payload])

    assert statuses == [200, 201]  # one is created, one is answered as a replay
    assert qty("TS-BLK-L", "SHOP1") == 4
    assert count("sales") == 1



@pytest.fixture(autouse=True)
def _sign_in(request):
    """All business endpoints require login: the tests in this file run as an authenticated admin."""
    if "client" in request.fixturenames:
        client = request.getfixturevalue("client")
        from tests.conftest import ADMIN_PASSWORD, ADMIN_USER

        assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).status_code == 200
