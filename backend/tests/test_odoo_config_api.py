"""Odoo configuration stored through the application (Odoo Configuration menu).

Purpose: only the URL, database name and API key. The API key can only be WRITTEN: it is stored encrypted, is never
returned to the browser, and never appears in the database in its original form. An admin can
disconnect (Disconnect) and then connect to another Odoo project. Configuration in `.env` is no longer used.
"""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.config import Settings
from app.errors import AppError
from app.odoo import factory
from app.odoo.live import LiveOdooClient

PUBLIC_URL = "http://93.184.216.34"  # literal public IP: passes the SSRF policy without DNS
OTHER_URL = "http://93.184.216.35"
SECRET = "super-secret-api-key-123"
BODY = {"url": PUBLIC_URL, "database": "verve_database", "api_key": SECRET}
EMPTY = {"source": "mock", "configured": False, "url": None, "database": None, "api_key_set": False}


def test_requires_sign_in(client):
    assert client.get("/api/odoo/config").status_code == 401
    assert client.put("/api/odoo/config", json=BODY).status_code == 401
    assert client.delete("/api/odoo/config").status_code == 401


def test_default_state_is_mock_and_not_configured(admin_client):
    assert admin_client.get("/api/odoo/config").json() == EMPTY


def test_save_never_returns_or_stores_the_plain_api_key(admin_client, session):
    saved = admin_client.put("/api/odoo/config", json=BODY)
    assert saved.status_code == 200
    assert SECRET not in saved.text and SECRET not in admin_client.get("/api/odoo/config").text
    assert saved.json() == {"source": "database", "configured": True, "url": PUBLIC_URL, "database": "verve_database", "api_key_set": True}
    stored = session.execute(text("select value from app_settings where key = 'odoo.api_key'")).scalar_one()
    assert SECRET not in stored  # encrypted


def test_no_login_field_exists_anywhere(admin_client, session):
    admin_client.put("/api/odoo/config", json={**BODY, "login": "ignored@example.com"})  # unknown field is ignored
    assert "login" not in admin_client.get("/api/odoo/config").json()
    assert session.execute(text("select count(*) from app_settings where key = 'odoo.login'")).scalar_one() == 0


def test_api_key_required_on_first_save(admin_client):
    response = admin_client.put("/api/odoo/config", json={k: v for k, v in BODY.items() if k != "api_key"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "API_KEY_REQUIRED"


def test_blank_api_key_keeps_the_stored_one_for_the_same_server(admin_client, session, test_settings):
    admin_client.put("/api/odoo/config", json=BODY)
    again = admin_client.put("/api/odoo/config", json={**BODY, "url": PUBLIC_URL + "/", "api_key": ""})
    assert again.status_code == 200
    token = session.execute(text("select value from app_settings where key = 'odoo.api_key'")).scalar_one()
    key = test_settings.settings_encryption_key.get_secret_value().encode()
    assert Fernet(key).decrypt(token.encode()).decode() == SECRET


@pytest.mark.parametrize("change", [{"database": "other_db"}, {"url": OTHER_URL}])
def test_stored_key_is_never_reused_for_a_different_server_or_database(admin_client, change):
    # The old key must not be sent to another server/database: the admin must enter a new key.
    admin_client.put("/api/odoo/config", json=BODY)
    response = admin_client.put("/api/odoo/config", json={**BODY, **change, "api_key": ""})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "API_KEY_REQUIRED")


def test_blocked_url_is_rejected_on_save(admin_client):
    for url in ("http://169.254.169.254", "http://127.0.0.1:9090", "ftp://example.com"):
        response = admin_client.put("/api/odoo/config", json={**BODY, "url": url})
        assert response.status_code == 422, url
        assert response.json()["error"]["code"] == "URL_NOT_ALLOWED"


def test_saving_without_encryption_key_is_refused(admin_client, test_settings):
    admin_client.app.dependency_overrides[factory.get_settings] = lambda: test_settings.model_copy(update={"settings_encryption_key": None})
    response = admin_client.put("/api/odoo/config", json=BODY)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "ENCRYPTION_NOT_CONFIGURED")


def test_stored_config_builds_a_live_client_and_refreshes_on_change(admin_client, session, test_settings):
    assert factory.stored_client(session, test_settings) is None
    admin_client.put("/api/odoo/config", json=BODY)
    first = factory.stored_client(session, test_settings)
    assert isinstance(first, LiveOdooClient)
    assert factory.stored_client(session, test_settings) is first  # unchanged: the instance is reused
    admin_client.put("/api/odoo/config", json={**BODY, "database": "another_db"})
    assert factory.stored_client(session, test_settings) is not first


# ---------------- Disconnect ----------------

def test_disconnect_removes_the_stored_config_and_the_key(admin_client, session, test_settings):
    admin_client.put("/api/odoo/config", json=BODY)
    response = admin_client.delete("/api/odoo/config")
    assert response.status_code == 200 and response.json() == EMPTY
    assert session.execute(text("select count(*) from app_settings where key like 'odoo.%'")).scalar_one() == 0
    assert factory.stored_client(session, test_settings) is None


def test_disconnect_when_nothing_is_stored_is_harmless(admin_client):
    assert admin_client.delete("/api/odoo/config").status_code == 200


def test_disconnect_resets_the_connection_status_to_not_configured(admin_client):
    admin_client.put("/api/odoo/config", json=BODY)
    admin_client.delete("/api/odoo/config")
    status = admin_client.get("/api/odoo/connection").json()
    assert status["status"] == "disconnected" and status["error"]["code"] == "NOT_CONFIGURED"


def test_disconnect_does_not_fall_back_to_env_credentials_in_live_mode(monkeypatch, admin_client, session, test_settings):
    # Live mode without stored configuration: there is no client (credentials in .env are no longer used).
    live = test_settings.model_copy(update={"odoo_mode": "live"})
    monkeypatch.setattr(factory, "get_settings", lambda: live)
    monkeypatch.setattr(factory, "_stored_or_none", lambda: None)
    factory._cached_client.cache_clear()
    try:
        with pytest.raises(AppError) as exc:
            factory.get_odoo()
        assert (exc.value.status, exc.value.code) == (503, "ODOO_NOT_CONFIGURED")
        assert admin_client.get("/api/odoo/config").json()["source"] == "mock"  # the test uses test_settings (mock)
    finally:
        factory._cached_client.cache_clear()


def test_settings_have_no_odoo_credential_fields():
    # Commenting out/removing ODOO_URL, ODOO_DB, ODOO_LOGIN, ODOO_API_KEY from .env changes nothing: they are not read.
    fields = Settings.model_fields
    assert not {"odoo_url", "odoo_db", "odoo_login", "odoo_api_key"} & set(fields)


# ---------------- switch to another Odoo project ----------------

def _local_sale_data(session):
    session.execute(text("insert into locations (code, name, type) values ('WH', 'WH', 'WAREHOUSE')"))
    session.commit()


def test_switching_to_another_odoo_with_local_data_needs_explicit_reset(admin_client, session):
    admin_client.put("/api/odoo/config", json=BODY)
    _local_sale_data(session)
    refused = admin_client.put("/api/odoo/config", json={**BODY, "url": OTHER_URL})
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "ODOO_SWITCH_REQUIRES_RESET")
    assert admin_client.get("/api/odoo/config").json()["url"] == PUBLIC_URL  # unchanged

    accepted = admin_client.put("/api/odoo/config", json={**BODY, "url": OTHER_URL, "reset_local_data": True})
    assert accepted.status_code == 200 and accepted.json()["url"] == OTHER_URL
    assert session.execute(text("select count(*) from locations")).scalar_one() == 0  # old data (belonging to the previous Odoo) is deleted


def test_switch_is_remembered_across_a_disconnect(admin_client, session):
    admin_client.put("/api/odoo/config", json=BODY)
    admin_client.delete("/api/odoo/config")
    _local_sale_data(session)
    refused = admin_client.put("/api/odoo/config", json={**BODY, "database": "other_db"})
    assert refused.status_code == 409
    # Reconnecting to the SAME Odoo does not require a reset.
    assert admin_client.put("/api/odoo/config", json=BODY).status_code == 200
    assert session.execute(text("select count(*) from locations")).scalar_one() == 1


def test_switching_without_local_data_needs_no_reset(admin_client):
    admin_client.put("/api/odoo/config", json=BODY)
    assert admin_client.put("/api/odoo/config", json={**BODY, "url": OTHER_URL}).status_code == 200


def test_disconnect_also_removes_the_legacy_login_row(admin_client, session):
    # The previous version stored 'odoo.login'; that field has been removed and must not remain after Disconnect.
    session.execute(text("insert into app_settings (key, value) values ('odoo.login', 'legacy@example.com')"))
    session.commit()
    admin_client.delete("/api/odoo/config")
    assert session.execute(text("select count(*) from app_settings where key = 'odoo.login'")).scalar_one() == 0
