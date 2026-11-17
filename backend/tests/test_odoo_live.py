"""Tests for LiveOdooClient against a fake HTTP transport (httpx.MockTransport).

Purpose: an Odoo instance is not yet available, so what is verified here is the client behaviour:
the shape of JSON-2 calls, searching `origin` BEFORE creating a document (duplicate prevention),
error mapping (5xx/timeout = transient, Odoo error = business), and the API key not leaking into logs.
Transport: Odoo JSON-2 (`/json/2/<model>/<method>`, bearer API key, no login).
"""
import json
import logging
import socket

import httpx
import pytest

from app.odoo.errors import OdooBusinessError, OdooTransientError
from app.odoo.live import LiveOdooClient

API_KEY = "secret-key-123"


class FakeOdoo:
    """Fake JSON-2 transport: records calls and answers according to the `results` table per (model, method)."""

    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []  # (model, method, body)
        self.headers = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        _, _, _, model, method = request.url.path.split("/")  # /json/2/<model>/<method>
        self.calls.append((model, method, json.loads(request.content)))
        self.headers.append(request.headers)
        return httpx.Response(200, json=self.results.get((model, method), []))

    def orm_calls(self):
        return [(m, meth) for m, meth, _ in self.calls]


def public_resolver(host, port, *args, **kwargs):
    """Fake resolver: all hosts point to a public IP, so the SSRF policy passes without a network."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def client_for(handler):
    return LiveOdooClient(
        "http://odoo.test", "odoo_db", API_KEY,
        transport=httpx.MockTransport(handler), resolver=public_resolver, sleep=lambda seconds: None,
    )


NEW_PICKING = {
    ("stock.picking", "search_read"): [],
    ("stock.location", "search_read"): [{"id": 9, "name": "SHOP1", "complete_name": "SHOP1", "usage": "internal"}],
    ("stock.picking.type", "search_read"): [{"id": 3}],
    ("stock.picking", "create"): [77],
    ("stock.picking", "read"): [{"id": 77, "name": "WH/OUT/00077", "state": "done"}],
}


def test_post_sale_searches_origin_before_create():
    fake = FakeOdoo(NEW_PICKING)
    result = client_for(fake).post_sale("req-1", 101, "SHOP1", 2)

    orm = fake.orm_calls()
    assert orm[0] == ("stock.picking", "search_read")  # check the reference first
    assert orm.index(("stock.picking", "create")) > 0
    first_domain = fake.calls[0][2]["domain"]
    assert ["origin", "=", "req-1"] in first_domain
    assert (result.duplicate, result.odoo_ref) == (False, "WH/OUT/00077")


def test_post_sale_returns_duplicate_when_picking_exists():
    fake = FakeOdoo({("stock.picking", "search_read"): [{"id": 5, "name": "WH/OUT/00005", "state": "done"}]})
    result = client_for(fake).post_sale("req-1", 101, "SHOP1", 2)
    assert (result.duplicate, result.odoo_ref) == (True, "WH/OUT/00005")
    assert ("stock.picking", "create") not in fake.orm_calls()  # does not create a second document
    assert ("stock.picking", "button_validate") not in fake.orm_calls()  # already completed: not touched again


def test_post_sale_resumes_unfinished_picking():
    # The previous process died after create but before validate: the picking is still a draft.
    fake = FakeOdoo({
        ("stock.picking", "search_read"): [{"id": 5, "name": "WH/OUT/00005", "state": "draft"}],
        ("stock.picking", "read"): [{"id": 5, "name": "WH/OUT/00005", "state": "done"}],
    })
    result = client_for(fake).post_sale("req-1", 101, "SHOP1", 2)
    orm = fake.orm_calls()
    assert ("stock.picking", "create") not in orm  # does not create a second document
    assert ("stock.picking", "button_validate") in orm  # but it is completed, so Odoo stock is deducted
    assert (result.duplicate, result.odoo_ref) == (True, "WH/OUT/00005")


@pytest.mark.parametrize("status", [503, 429, 408])
def test_5xx_and_throttling_map_to_transient(status):
    client = client_for(lambda request: httpx.Response(status, text="try later"))
    with pytest.raises(OdooTransientError):
        client.get_stock()


def test_timeout_maps_to_transient():
    def timeout(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(OdooTransientError):
        client_for(timeout).get_stock()


def rpc_error(name: str, status: int = 422):
    """Transport that answers with a JSON-2 error named `name` (the shape of an Odoo error body)."""

    def handler(request):
        return httpx.Response(status, json={"name": name, "message": "details", "arguments": ["details"], "context": {}})

    return handler


def test_known_rpc_business_error_maps_to_business():
    with pytest.raises(OdooBusinessError):
        client_for(rpc_error("odoo.exceptions.UserError")).post_sale("req-1", 101, "SHOP1", 2)


def test_unknown_rpc_error_maps_to_transient():
    # A transient Odoo condition (e.g. a database serialisation failure) must not immediately become a permanent FAILED.
    with pytest.raises(OdooTransientError):
        client_for(rpc_error("psycopg2.errors.SerializationFailure")).post_sale("req-1", 101, "SHOP1", 2)


def test_authentication_failure_maps_to_transient_auth_failed():
    def reject(request):
        return httpx.Response(401, json={"name": "werkzeug.exceptions.Unauthorized", "message": "Invalid apikey"})

    with pytest.raises(OdooTransientError) as exc:
        client_for(reject).get_stock()
    assert exc.value.code == "AUTH_FAILED"


def test_permission_denied_is_a_business_error():
    with pytest.raises(OdooBusinessError):
        client_for(rpc_error("odoo.exceptions.AccessError", status=403)).get_stock()


def test_picking_not_done_after_validation_is_business_error():
    # If button_validate only returns a wizard and the picking is not yet `done`, do not report success.
    results = {**NEW_PICKING, ("stock.picking", "read"): [{"id": 77, "name": "WH/OUT/00077", "state": "assigned"}]}
    with pytest.raises(OdooBusinessError):
        client_for(FakeOdoo(results)).post_sale("req-1", 101, "SHOP1", 2)


def test_api_key_sent_as_bearer_with_database_header_and_not_logged(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeOdoo({("stock.location", "search_read"): [], ("stock.quant", "search_read"): []})
    client_for(fake).get_stock()

    assert all(h["authorization"] == f"bearer {API_KEY}" and h["x-odoo-database"] == "odoo_db" for h in fake.headers)
    assert API_KEY not in caplog.text
    # There is no login anywhere: only the URL, database and API key.
    assert all("login" not in json.dumps(body) for _, _, body in fake.calls)


def test_null_result_means_none():
    """An Odoo method that returns nothing answers `null`; that is a success without a value."""
    handler = lambda request: httpx.Response(200, content=b"null")  # noqa: E731
    assert client_for(handler)._call("stock.quant", "action_apply_inventory", {"ids": [1]}) is None


def test_calls_use_the_json2_url_shape():
    fake = FakeOdoo({("stock.location", "search_read"): [], ("stock.quant", "search_read"): []})
    client_for(fake).get_stock()
    assert fake.calls[0][:2] == ("stock.location", "search_read")
    assert fake.calls[0][2]["domain"] == [["usage", "=", "internal"]]


def test_variant_names_ignore_internal_reference_prefix():
    """Odoo includes the internal code in display_name: '[TS-BLK-L] Cotton T-Shirt (Black / L)'.
    The product name and variant name in the application must not contain that code."""
    fake = FakeOdoo({
        ("product.product", "search_read"): [
            {"id": 3, "product_tmpl_id": [1, "Cotton T-Shirt"], "display_name": "[TS-BLK-L] Cotton T-Shirt (Black / L)", "default_code": "TS-BLK-L"},
            {"id": 1, "product_tmpl_id": [2, "[SERUM-50] Hydrating Face Serum"], "display_name": "[SERUM-50] Hydrating Face Serum", "default_code": "SERUM-50"},
        ],
        ("stock.warehouse.orderpoint", "search_read"): [],
    })
    shirt, serum = client_for(fake).list_variants()

    assert (shirt.product_name, shirt.variant_name) == ("Cotton T-Shirt", "Black / L")
    assert (serum.product_name, serum.variant_name) == ("Hydrating Face Serum", "Default")


def test_stock_move_has_no_name_field():
    """Odoo 19 has no `name` field on stock.move; sending it makes create fail."""
    fake = FakeOdoo(NEW_PICKING)
    client_for(fake).post_sale("req-9", 101, "SHOP1", 1)

    picking_vals = next(body["vals_list"][0] for m, meth, body in fake.calls if (m, meth) == ("stock.picking", "create"))
    move = picking_vals["move_ids"][0][2]
    assert "name" not in move
    assert (move["product_id"], move["product_uom_qty"]) == (101, 1)
