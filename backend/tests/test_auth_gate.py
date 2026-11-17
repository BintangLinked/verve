"""All application endpoints (other than /health and login) require login.

Purpose: the dashboard opens after login; the stock, sales and replenishment APIs also reject
requests without a session (401) so that login is more than a cosmetic screen.
"""
import pytest

from tests.conftest import ADMIN_PASSWORD, ADMIN_USER

PROTECTED = [
    ("get", "/api/inventory"),
    ("get", "/api/inventory/1/movements"),
    ("post", "/api/inventory/sync"),
    ("post", "/api/sales"),
    ("get", "/api/replenishment"),
    ("post", "/api/replenishment/request"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_endpoint_requires_sign_in(client, method, path):
    response = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_health_stays_public(client):
    assert client.get("/health").status_code == 200


def test_signed_in_user_can_read_inventory(client, seeded):
    client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert client.get("/api/inventory").status_code == 200
