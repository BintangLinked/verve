"""Tests for the Odoo connection status API, monitor and startup (design 8.9).

Purpose: the connection endpoint is admin-only, GET only reads the stored status (it does not call Odoo),
POST /test re-checks using the backend configuration without accepting credentials, responses never
contain the URL, login or API key, and an Odoo failure or empty configuration does not fail startup.
"""
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.odoo.base import SAFE_ERROR_MESSAGES, ConnectionStatus
from app.odoo.factory import get_odoo_or_none
from app.services.connection import ConnectionMonitor, startup_tasks

EXPECTED_FIELDS = {"mode", "status", "database", "odoo_version", "last_checked_at", "error"}


class FakeClient:
    """Fake client: counts checks and returns the status specified by the test."""

    def __init__(self, status=None, raises=None):
        self.checks = 0
        self._status = status
        self._raises = raises

    def check_connection(self):
        self.checks += 1
        if self._raises:
            raise self._raises
        return self._status


def failing(code):
    return ConnectionStatus("live", False, "odoo_db", "17.0", datetime.now(timezone.utc), code, SAFE_ERROR_MESSAGES[code])


def use(client, fake):
    client.app.dependency_overrides[get_odoo_or_none] = lambda: fake


# ---------------- access rights ----------------

@pytest.mark.parametrize("method,path", [("get", "/api/odoo/connection"), ("post", "/api/odoo/connection/test")])
def test_connection_endpoints_require_login(client, method, path):
    resp = getattr(client, method)(path)
    assert (resp.status_code, resp.json()["error"]["code"]) == (401, "UNAUTHENTICATED")


# ---------------- status ----------------

def test_status_before_any_check_is_unknown(admin_client):
    body = admin_client.get("/api/odoo/connection").json()
    assert set(body) == EXPECTED_FIELDS
    assert (body["mode"], body["status"], body["database"], body["last_checked_at"], body["error"]) == (
        "mock", "unknown", None, None, None)


def test_test_endpoint_checks_and_get_returns_the_cached_result(admin_client):
    fake = FakeClient(ConnectionStatus("live", True, "odoo_db", "17.0", datetime.now(timezone.utc)))
    use(admin_client, fake)

    tested = admin_client.post("/api/odoo/connection/test")
    assert tested.status_code == 200
    assert set(tested.json()) == EXPECTED_FIELDS
    assert (tested.json()["status"], tested.json()["database"], tested.json()["odoo_version"]) == ("connected", "odoo_db", "17.0")
    assert tested.json()["last_checked_at"]

    first, second = admin_client.get("/api/odoo/connection"), admin_client.get("/api/odoo/connection")
    assert first.json() == second.json() == tested.json()
    assert fake.checks == 1  # GET does not call Odoo


def test_mock_mode_reports_connected(admin_client):
    body = admin_client.post("/api/odoo/connection/test").json()
    assert (body["mode"], body["status"], body["database"], body["odoo_version"]) == ("mock", "connected", "mock", "mock")


def test_failed_check_returns_a_sanitised_error(admin_client):
    use(admin_client, FakeClient(failing("AUTH_FAILED")))
    body = admin_client.post("/api/odoo/connection/test").json()
    assert (body["status"], body["error"]) == ("disconnected", {"code": "AUTH_FAILED", "message": SAFE_ERROR_MESSAGES["AUTH_FAILED"]})
    assert set(body) == EXPECTED_FIELDS  # no URL, login or key


def test_unexpected_crash_in_a_check_becomes_unknown_not_a_500(admin_client):
    use(admin_client, FakeClient(raises=RuntimeError("boom with secret-token-123")))
    resp = admin_client.post("/api/odoo/connection/test")
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == "UNKNOWN"
    assert "secret-token-123" not in resp.text


def test_missing_configuration_is_reported_not_raised(admin_client):
    use(admin_client, None)
    body = admin_client.post("/api/odoo/connection/test").json()
    assert (body["status"], body["error"]["code"]) == ("disconnected", "NOT_CONFIGURED")


def test_request_body_is_ignored_so_credentials_cannot_be_supplied(admin_client):
    fake = FakeClient(ConnectionStatus("live", True, "odoo_db", "17.0", datetime.now(timezone.utc)))
    use(admin_client, fake)
    resp = admin_client.post("/api/odoo/connection/test", json={"url": "http://169.254.169.254", "api_key": "x", "database": "evil"})
    assert resp.status_code == 200
    assert resp.json()["database"] == "odoo_db"  # still the backend configuration
    assert fake.checks == 1


def test_connection_responses_are_not_cacheable(admin_client):
    assert admin_client.get("/api/odoo/connection").headers["cache-control"] == "no-store"
    assert admin_client.post("/api/odoo/connection/test").headers["cache-control"] == "no-store"


# ---------------- startup ----------------

def test_startup_with_missing_configuration_does_not_raise():
    monitor = ConnectionMonitor("live")

    def not_configured():
        raise ValueError("ODOO_MODE=live requires: ODOO_URL")

    startup_tasks(lambda: pytest.fail("seed must not run"), not_configured, monitor)
    status = monitor.current()
    assert (status.connected, status.error_code) == (False, "NOT_CONFIGURED")
    assert status.checked_at is not None


def test_startup_skips_seeding_when_odoo_is_down():
    monitor = ConnectionMonitor("live")
    startup_tasks(lambda: pytest.fail("seed must not run"), lambda: FakeClient(failing("UNREACHABLE")), monitor)
    assert monitor.current().error_code == "UNREACHABLE"


def test_startup_seeds_the_sample_data_when_connected(session_factory, odoo, count):
    monitor = ConnectionMonitor("mock")
    startup_tasks(session_factory, lambda: odoo, monitor)
    assert monitor.current().connected is True
    assert count("locations") == 3


def test_app_starts_and_serves_requests_when_odoo_is_not_configured(monkeypatch):
    from app import main
    from app.services import connection

    def not_configured():
        raise ValueError("ODOO_MODE=live requires: ODOO_URL")

    connection.get_monitor.cache_clear()
    monkeypatch.setattr(main, "get_odoo", not_configured)
    try:
        with TestClient(main.create_app()) as client:  # `with` runs the lifespan
            assert client.get("/health").status_code == 200
            for _ in range(50):  # the startup check runs in a background thread
                if connection.get_monitor().current().checked_at:
                    break
                time.sleep(0.1)
            assert connection.get_monitor().current().error_code == "NOT_CONFIGURED"
    finally:
        connection.get_monitor.cache_clear()
