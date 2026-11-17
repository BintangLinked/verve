"""Tests for application authentication (admin login with a session cookie).

Purpose: the Odoo connection endpoint may only be opened by an admin. What is verified: the password hash and session
token are secure (cannot be forged or used after expiry), login does not reveal whether a
username exists, repeated failed attempts are limited, the cookie is httpOnly, and an empty configuration
makes authentication fail closed.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.auth import LoginThrottle, create_session_token, hash_password, read_session_token, verify_password
from conftest import ADMIN_PASSWORD, ADMIN_USER

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
SECRET = "x" * 40


def login(client, username=ADMIN_USER, password=ADMIN_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ---------------- unit: password and session token ----------------

def test_password_hash_roundtrip_and_salt():
    stored = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", stored) is True
    assert verify_password("wrong", stored) is False
    assert hash_password("s3cret-pass") != stored  # random salt


def test_verify_password_rejects_malformed_hash():
    assert verify_password("x", "garbage") is False
    assert verify_password("x", "scrypt$notnumbers$8$1$AAAA$BBBB") is False


def test_session_token_roundtrip_and_expiry():
    token = create_session_token("admin", SECRET, timedelta(minutes=60), NOW)
    assert read_session_token(token, SECRET, NOW + timedelta(minutes=59)) == "admin"
    assert read_session_token(token, SECRET, NOW + timedelta(minutes=61)) is None


def test_session_token_tamper_and_wrong_secret_rejected():
    token = create_session_token("admin", SECRET, timedelta(minutes=60), NOW)
    payload, _, signature = token.rpartition(".")
    assert read_session_token(payload + "." + signature[:-1] + ("0" if signature[-1] != "0" else "1"), SECRET, NOW) is None
    assert read_session_token("x" + token, SECRET, NOW) is None
    assert read_session_token(token, "y" * 40, NOW) is None
    assert read_session_token("garbage", SECRET, NOW) is None
    assert read_session_token("", SECRET, NOW) is None


# ---------------- API: login, session, logout ----------------

def test_login_success_sets_httponly_cookie(client):
    resp = login(client)
    assert resp.status_code == 200
    assert resp.json() == {"username": ADMIN_USER}
    cookie = resp.headers["set-cookie"].lower()
    assert "verve_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie.replace("samesite", "")  # cookie_secure is False by default


def test_login_cookie_is_secure_when_configured(client, test_settings):
    from app.config import get_settings

    client.app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(update={"cookie_secure": True})
    assert "secure" in login(client).headers["set-cookie"].lower().replace("samesite", "")


def test_wrong_password_and_unknown_user_look_identical(client):
    wrong_password = login(client, password="not-the-password")
    unknown_user = login(client, username="nobody", password="not-the-password")
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()
    assert wrong_password.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert "set-cookie" not in wrong_password.headers


def test_login_throttled_after_repeated_failures(client):
    for _ in range(5):
        assert login(client, password="bad").status_code == 401
    resp = login(client)  # correct password, but already throttled
    assert (resp.status_code, resp.json()["error"]["code"]) == (429, "TOO_MANY_ATTEMPTS")


def test_me_requires_session(client):
    assert client.get("/api/auth/me").json()["error"]["code"] == "UNAUTHENTICATED"
    login(client)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200 and resp.json()["username"] == ADMIN_USER and "expires_at" in resp.json()


def test_garbage_and_forged_cookies_do_not_authenticate(client):
    client.cookies.set("verve_session", "garbage")
    assert client.get("/api/auth/me").status_code == 401
    forged = create_session_token(ADMIN_USER, "z" * 40, timedelta(minutes=60), datetime.now(timezone.utc))
    client.cookies.set("verve_session", forged)  # signed with the wrong secret
    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_session(client):
    login(client)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_unconfigured_auth_fails_closed(client, test_settings):
    from app.config import get_settings

    client.app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(
        update={"admin_username": None, "admin_password_hash": None, "session_secret": None}
    )
    resp = login(client)
    assert (resp.status_code, resp.json()["error"]["code"]) == (503, "AUTH_NOT_CONFIGURED")
    assert client.get("/api/auth/me").status_code == 401


def test_short_session_secret_counts_as_unconfigured(client, test_settings):
    from app.config import get_settings

    client.app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(update={"session_secret": "short"})
    assert login(client).status_code == 503


def test_cors_allows_credentials_for_the_configured_origin(client):
    resp = client.options(
        "/api/auth/login",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type"},
    )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert resp.headers["access-control-allow-credentials"] == "true"


# ---------------- I1: login throttle (bounded, atomic, per IP) ----------------

def test_tokens_are_unique_per_login():
    first = create_session_token("admin", SECRET, timedelta(minutes=60), NOW)
    second = create_session_token("admin", SECRET, timedelta(minutes=60), NOW)
    assert first != second  # random id: revoking one token does not revoke another login in the same second


def test_throttle_memory_is_bounded_and_expired_keys_are_swept():
    throttle = LoginThrottle(max_keys=100)
    for i in range(500):
        throttle.reserve("10.0.0.1", f"user{i}", NOW)
    assert throttle.size() <= 201  # each new username does NOT grow memory without bound
    throttle.reserve("10.0.0.2", "someone", NOW + timedelta(minutes=10))
    assert throttle.size() <= 3  # expired entries are swept


def test_throttle_reservation_is_atomic_under_concurrency():
    throttle = LoginThrottle()
    with ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(lambda _: throttle.reserve("1.1.1.1", "admin", NOW), range(30)))
    assert results.count(True) == 5  # the limit of 5 holds even when 30 attempts arrive concurrently


def test_per_ip_limit_applies_across_usernames():
    throttle = LoginThrottle(max_failures=5, max_ip_failures=20)
    allowed = [throttle.reserve("1.1.1.1", f"user{i}", NOW) for i in range(30)]
    assert allowed.count(True) == 20  # guessing many usernames from a single IP is still limited


def test_attempts_from_other_ips_are_counted_separately():
    throttle = LoginThrottle()
    assert [throttle.reserve("1.1.1.1", "admin", NOW) for _ in range(6)].count(True) == 5
    assert throttle.reserve("2.2.2.2", "admin", NOW) is True  # other IPs are not locked out


def test_successful_login_releases_the_reserved_slots():
    throttle = LoginThrottle()
    for _ in range(4):
        assert throttle.reserve("1.1.1.1", "admin", NOW)
    throttle.release("1.1.1.1", "admin")
    assert all(throttle.reserve("1.1.1.1", "admin", NOW) for _ in range(5))


# ---------------- I6: logout revokes the session on the server ----------------

def test_logout_revokes_the_token_on_the_server(client):
    login(client)
    stolen = client.cookies.get("verve_session")
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set("verve_session", stolen)  # a copied token is reused after logout
    resp = client.get("/api/auth/me")
    assert (resp.status_code, resp.json()["error"]["code"]) == (401, "UNAUTHENTICATED")


def test_changing_the_admin_password_invalidates_existing_sessions(client, test_settings):
    from app.config import get_settings

    login(client)
    assert client.get("/api/auth/me").status_code == 200
    changed = test_settings.model_copy(update={"admin_password_hash": hash_password("another-password-123")})
    client.app.dependency_overrides[get_settings] = lambda: changed
    assert client.get("/api/auth/me").status_code == 401  # the signing key changes as well


# ---------------- change password and session info (iteration 3) ----------------

def test_me_reports_when_the_session_expires(admin_client):
    body = admin_client.get("/api/auth/me").json()
    assert body["username"] == "admin" and body["expires_at"].endswith("Z")


def test_change_password_rejects_a_wrong_current_password(admin_client):
    response = admin_client.put("/api/auth/password", json={"current_password": "nope", "new_password": "a-brand-new-password"})
    assert (response.status_code, response.json()["error"]["code"]) == (400, "INVALID_CURRENT_PASSWORD")


def test_change_password_rejects_a_short_new_password(admin_client):
    from tests.conftest import ADMIN_PASSWORD

    response = admin_client.put("/api/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "short"})
    assert response.status_code == 422


def test_change_password_ends_sessions_and_new_password_works(admin_client):
    from tests.conftest import ADMIN_PASSWORD

    response = admin_client.put("/api/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "a-brand-new-password"})
    assert response.status_code == 204
    assert admin_client.get("/api/auth/me").status_code == 401  # the old session is no longer valid
    assert admin_client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}).status_code == 401
    assert admin_client.post("/api/auth/login", json={"username": "admin", "password": "a-brand-new-password"}).status_code == 200
