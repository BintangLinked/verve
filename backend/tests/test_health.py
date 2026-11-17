"""Tests for the /health endpoint and the standard error shape.

Purpose: to confirm that (1) the health check verifies the database, and (2) all errors use
the shape {"error": {"code", "message", "details"}} in accordance with the Global Constraints.
"""
from sqlalchemy.exc import OperationalError

from app.db import get_session


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_503_when_database_down(client):
    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("select 1", {}, Exception("database down"))

    def _broken():
        yield BrokenSession()

    client.app.dependency_overrides[get_session] = _broken
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "DB_UNAVAILABLE"


def test_validation_error_shape(client):
    # Temporary route for this test only, so that a 422 can be triggered before any business endpoint exists.
    @client.app.get("/_uji")
    def _uji(n: int):
        return {"n": n}

    resp = client.get("/_uji", params={"n": "bukan-angka"})
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]


def test_unknown_route_uses_error_shape(client):
    resp = client.get("/not-found")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
