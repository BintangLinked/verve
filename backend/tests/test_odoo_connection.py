"""Tests for the Odoo connection check and hardening of the live client (design 8.9).

Purpose: the connection is validated DIRECTLY against the configured database (without db.list), failures
are mapped to safe codes, no secrets leak into the status or logs, timeouts and retries are
bounded (reads only), redirects are not followed, and URLs that violate the SSRF policy never
result in an HTTP request.
"""
import json
import logging
import socket

import httpx
import pytest

from app.config import Settings
from app.errors import AppError
from app.odoo.errors import OdooTransientError
from app.odoo.live import LiveOdooClient

API_KEY = "super-secret-key-9f3a"


def resolver_to(*ips):
    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    return resolve


class Script:
    """Fake Odoo JSON-2 transport. Response keys: ("common", "version") for the version check, and
    ("object", model, method) for /json/2/<model>/<method>. Non-Response answers are sent as the JSON body as-is."""

    def __init__(self, answers=None, raises=None, status=200):
        self.answers = answers or {}
        self.raises = raises  # exception raised on every call
        self.status = status
        self.calls = []  # list of call keys

    def __call__(self, request):
        if request.url.path.endswith("/web/webclient/version_info"):
            key = ("common", "version")
        else:
            _, _, _, model, method = request.url.path.split("/")
            key = ("object", model, method)
        self.calls.append(key)
        if self.raises:
            raise self.raises("simulated network failure", request=request)
        if self.status != 200:
            return httpx.Response(self.status, text="gateway")
        answer = self.answers.get(key, [])
        if callable(answer):
            answer = answer(len(self.calls))
        if isinstance(answer, httpx.Response):
            return answer
        if key == ("common", "version"):
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": None, "result": answer})
        return httpx.Response(200, json=answer)


VERSION = {("common", "version"): {"server_version": "17.0", "server_version_info": [17, 0, 0, "final", 0, ""]}}
AUTH_OK = {("object", "res.users", "context_get"): {"lang": "en_US", "uid": 2}}
AUTH = ("object", "res.users", "context_get")


def make(script, resolver=None, allow_private=False, sleeps=None, **extra):
    return LiveOdooClient(
        "https://odoo.example.com", "odoo_db", API_KEY,
        transport=httpx.MockTransport(script),
        resolver=resolver or resolver_to("93.184.216.34"),
        allow_private_hosts=allow_private,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
        **extra,
    )


def rpc_error(message, name="odoo.exceptions.AccessError", debug="", status=422):
    return httpx.Response(status, json={"name": name, "message": message, "arguments": [message], "context": {}, "debug": debug})


# ---------------- check results ----------------

def test_check_connection_success_reports_database_and_version():
    status = make(Script({**VERSION, **AUTH_OK})).check_connection()
    assert (status.mode, status.connected, status.database, status.server_version) == ("live", True, "odoo_db", "17.0")
    assert status.error_code is None and status.error_message is None and status.checked_at is not None


def test_database_not_found_is_detected_without_db_list():
    # Odoo answers with an HTML 404 if the database in the X-Odoo-Database header does not exist.
    script = Script({**VERSION, AUTH: httpx.Response(404, text="<h1>Not Found</h1><p>No database is selected</p>")})
    status = make(script).check_connection()
    assert (status.connected, status.error_code, status.server_version) == (False, "DATABASE_NOT_FOUND", "17.0")
    assert not any(c[1] == "db" for c in script.calls)  # the database is validated directly, not through a list


def test_rejected_api_key_is_auth_failed():
    status = make(Script({**VERSION, AUTH: httpx.Response(401, json={"name": "werkzeug.exceptions.Unauthorized", "message": "Invalid apikey"})})).check_connection()
    assert (status.connected, status.error_code) == (False, "AUTH_FAILED")


def test_timeout_is_retried_a_bounded_number_of_times():
    script, sleeps = Script(raises=httpx.ReadTimeout), []
    status = make(script, sleeps=sleeps).check_connection()
    assert status.error_code == "TIMEOUT"
    assert script.calls.count(("common", "version")) == 3  # 1 attempt + 2 retries
    assert len(sleeps) == 2 and sleeps[0] < sleeps[1]  # the delay increases (backoff)


@pytest.mark.parametrize("script", [Script(raises=httpx.ConnectError), Script(status=502)])
def test_connection_errors_and_gateway_errors_are_unreachable(script):
    assert make(script).check_connection().error_code == "UNREACHABLE"


def test_ssrf_blocked_url_makes_no_http_call():
    script = Script({**VERSION, **AUTH_OK})
    status = make(script, resolver=resolver_to("169.254.169.254"), allow_private=True).check_connection()
    assert status.error_code == "URL_NOT_ALLOWED"
    assert script.calls == []


def test_unresolvable_host_is_unreachable_without_http_call():
    def fail(*args, **kwargs):
        raise socket.gaierror("no such host")

    script = Script({**VERSION, **AUTH_OK})
    assert make(script, resolver=fail).check_connection().error_code == "UNREACHABLE"
    assert script.calls == []


def test_private_host_allowed_only_with_the_flag():
    script = Script({**VERSION, **AUTH_OK})
    assert make(script, resolver=resolver_to("127.0.0.1")).check_connection().error_code == "URL_NOT_ALLOWED"
    assert make(Script({**VERSION, **AUTH_OK}), resolver=resolver_to("127.0.0.1"), allow_private=True).check_connection().connected


def test_unexpected_response_shape_is_unknown_not_a_crash():
    script = Script({("common", "version"): "not-a-dict", AUTH: "not-an-identity"})
    assert make(script).check_connection().error_code in {"AUTH_FAILED", "UNKNOWN"}


# ---------------- no secrets leak ----------------

def test_no_secret_reaches_the_status_or_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    leaky = rpc_error(f"login failed for key {API_KEY}", debug=f"Traceback ... password={API_KEY}", status=401)
    status = make(Script({**VERSION, AUTH: leaky})).check_connection()
    assert API_KEY not in repr(status)
    assert API_KEY not in caplog.text
    assert "Traceback" not in repr(status) + caplog.text
    # The status message is fixed text per code, not raw text from Odoo.
    assert status.error_message and "login failed" not in status.error_message


# ---------------- timeout, retry, redirect ----------------

def test_timeouts_are_configured():
    client = make(Script())
    assert (client._http.timeout.connect, client._http.timeout.read) == (5.0, 10.0)


def test_reads_are_retried_but_writes_are_not():
    attempts = {"read": 0}

    def flaky_read(call_number):
        attempts["read"] += 1
        return httpx.Response(503) if attempts["read"] == 1 else httpx.Response(200, json=[])

    reads = Script({**AUTH_OK, ("object", "stock.location", "search_read"): flaky_read, ("object", "stock.quant", "search_read"): []})
    make(reads).get_stock()
    assert reads.calls.count(("object", "stock.location", "search_read")) == 2  # fails once, succeeds on the retry

    writes = Script({**AUTH_OK, ("object", "stock.picking", "search_read"): [],
                     ("object", "stock.location", "search_read"): [{"id": 9}], ("object", "stock.picking.type", "search_read"): [{"id": 3}],
                     ("object", "stock.picking", "create"): httpx.Response(503)})
    with pytest.raises(OdooTransientError):
        make(writes).post_sale("SALE:x", 101, "SHOP1", 1)
    assert writes.calls.count(("object", "stock.picking", "create")) == 1  # writes are not retried within a single call


def test_redirects_are_not_followed():
    redirect = httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
    script = Script({("common", "version"): redirect})
    status = make(script).check_connection()
    assert status.connected is False
    assert script.calls.count(("common", "version")) == 1 + 2  # retries only; never follows Location


# ---------------- mock and factory ----------------

def test_mock_reports_a_connected_mock_status(odoo):
    status = odoo.check_connection()
    assert (status.mode, status.connected, status.database, status.server_version) == ("mock", True, "mock", "mock")


def test_missing_live_settings_become_a_503_not_a_crash(monkeypatch):
    from app.odoo import factory

    def boom(*args, **kwargs):
        raise ValueError("ODOO_MODE=live requires: ODOO_URL, ODOO_DB")

    monkeypatch.setattr(factory, "get_odoo_client", boom)
    monkeypatch.setattr(factory, "_stored_or_none", lambda: None)  # do not read the dev database configuration
    factory._cached_client.cache_clear()
    try:
        with pytest.raises(AppError) as exc:
            factory.get_odoo()
        assert (exc.value.status, exc.value.code) == (503, "ODOO_NOT_CONFIGURED")
    finally:
        factory._cached_client.cache_clear()


# ---------------- I2: the connection is pinned to the validated address ----------------

def test_requests_connect_to_the_validated_address_and_keep_host_and_sni():
    seen = []

    def capture(request):
        seen.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"server_version": "17.0"}})

    make(capture).check_connection()
    first = seen[0]
    assert first.url.host == "93.184.216.34"  # the validated IP, not a host name re-resolved at connection time
    assert first.headers["host"] == "odoo.example.com"
    assert first.extensions["sni_hostname"] == "odoo.example.com"  # the TLS certificate is still checked against the host name


def test_a_changed_dns_answer_is_caught_on_the_very_next_call():
    dns = {"ips": ["93.184.216.34"]}

    def flipping(host, port, *args, **kwargs):
        return resolver_to(*dns["ips"])(host, port)

    script = Script({("object", "stock.location", "search_read"): [], ("object", "stock.quant", "search_read"): [], **AUTH_OK})
    client = make(script, resolver=flipping)
    client.get_stock()  # passes
    dns["ips"] = ["10.0.0.5"]  # DNS now answers with a private address
    calls_before = len(script.calls)
    with pytest.raises(OdooTransientError) as exc:
        client.get_stock()
    assert exc.value.code == "URL_NOT_ALLOWED"
    assert len(script.calls) == calls_before  # no request is sent out


def test_environment_proxies_are_ignored():
    assert make(Script())._http.trust_env is False


def test_oversized_response_is_rejected():
    huge = httpx.Response(200, content=b"x" * (6 * 1024 * 1024))
    with pytest.raises(OdooTransientError):
        make(Script({("object", "stock.location", "search_read"): huge})).get_stock()


# ---------------- I4: total time limit for a single check ----------------

class StepClock:
    """Fake clock that advances 10 seconds each time it is read."""

    def __init__(self):
        self.now = -10.0

    def __call__(self):
        self.now += 10.0
        return self.now


def test_a_check_has_a_total_deadline_across_retries():
    script = Script(raises=httpx.ReadTimeout)
    status = make(script, check_deadline=25.0, clock=StepClock()).check_connection()
    assert status.error_code == "TIMEOUT"
    assert script.calls.count(("common", "version")) == 2  # the third attempt exceeds the 25-second limit and is not made


def test_an_expired_deadline_makes_no_request_at_all():
    script = Script({**VERSION, **AUTH_OK})
    assert make(script, check_deadline=0.0).check_connection().error_code == "TIMEOUT"
    assert script.calls == []


# ---------------- I3: secret sanitisation ----------------

@pytest.mark.parametrize("variant", [API_KEY, API_KEY.upper(), API_KEY.title()])
def test_scrubbing_ignores_case(variant):
    leaky = rpc_error(f"constraint failed for {variant}", name="psycopg2.errors.SerializationFailure")
    with pytest.raises(OdooTransientError) as exc:
        make(Script({("object", "stock.location", "search_read"): leaky, **AUTH_OK})).get_stock()
    assert API_KEY.lower() not in str(exc.value).lower()
