"""LiveOdooClient: a real Odoo client through the Odoo JSON-2 API (`/json/2/<model>/<method>`) with an API key.

Odoo 19 marks `/jsonrpc` (execute_kw) as deprecated and schedules its removal in Odoo 22; JSON-2 is its
replacement. Another advantage: authentication needs only an API key (header `Authorization: bearer ...`) and the
database name (header `X-Odoo-Database`), WITHOUT a login. For that reason the connection configuration is only URL, database, API key.

Purpose: implements the OdooClient contract (base.py) so that moving from mock to Odoo
only changes the configuration source. Credentials exist only in the backend process and are never written to the log
or returned to the API. Verified against a local Odoo 19.0+e (see docs/design.md 8.3a).

Connection: the database is validated DIRECTLY through a single `res.users/context_get` call (without listing databases,
which the Odoo administrator can disable). The server version is read from `/web/webclient/version_info`.

Hardening:
- SSRF: the URL is checked (odoo/url_policy.py) on EVERY request, and the connection is PINNED to the IP address resulting from
  the check (Host header and SNI remain the original host name), so DNS rebinding between the check and the
  connection is not possible. Redirects are not followed and proxies from the environment (HTTP(S)_PROXY) are ignored.
- Timeout: connect 5 seconds, read 10 seconds per attempt, a total limit of 20 seconds for one connection check,
  and the response size is limited to 5 MB.
- Limited retry (max 2, exponential backoff) only for reads; writes are not
  repeated within a single call (repeated writes are handled by the outbox idempotently).

Double-deduction prevention: Odoo has no built-in idempotency key, so before creating a document we
search for a `stock.picking` with `origin` = the reference. If it exists and is done, it is returned as a
duplicate; if it exists but is not done (e.g. the process was interrupted), it is continued until done.

Error mapping: timeout/connection/HTTP 5xx/408/429 -> OdooTransientError (retried), API key rejected (401) or
database not found (404 without a JSON body) -> OdooTransientError with a specific code (a correctable
misconfiguration), Odoo business/permission rejections (UserError, ValidationError, AccessError, MissingError) ->
OdooBusinessError (not retried).
"""
import ipaddress
import json
import logging
import re
import socket
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from app.odoo.base import SAFE_ERROR_MESSAGES, ConnectionStatus, OdooLocation, OdooResult, OdooStock, OdooVariant
from app.odoo.errors import OdooBusinessError, OdooTransientError
from app.odoo.url_policy import Target, UrlNotAllowed, resolve_and_validate

log = logging.getLogger("app.odoo.live")

# [Assumption] Odoo internal locations are named the same as the application code (WH, SHOP1, SHOP2),
# and damaged stock is in a sub-location named "Damaged" (e.g. "SHOP1/Damaged").
WAREHOUSE_CODE = "WH"
DAMAGED_NAME = "Damaged"

# Odoo errors that mean a permanent business rejection (retrying does not help).
# Other names (database serialisation failure, lock timeout, etc.) are treated as temporary so as not to
# go straight to FAILED; retrying a genuine business condition only wastes a few attempts.
BUSINESS_ERROR_NAMES = {"UserError", "ValidationError", "AccessError", "MissingError"}
# HTTP "try again later" statuses other than 5xx.
RETRYABLE_HTTP = {408, 429}
# Only network disruptions are retried; a wrong login/database will not heal by retrying.
RETRYABLE_CODES = {"TIMEOUT", "UNREACHABLE"}

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_RETRIES = 2
RETRY_BASE_SECONDS = 0.2
CHECK_DEADLINE_SECONDS = 20.0  # total limit for one connection check (including retries)
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_ERROR_TEXT = 300


REFERENCE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")  # "[TS-BLK-L] " at the start of display_name


class LiveOdooClient:
    def __init__(
        self,
        url: str,
        db: str,
        api_key: str,
        transport=None,
        allow_private_hosts: bool = False,
        resolver: Callable = socket.getaddrinfo,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = MAX_RETRIES,
        check_deadline: float = CHECK_DEADLINE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._base_url = url.strip().rstrip("/")
        self._db, self._key = db, api_key
        # follow_redirects=False: a redirect must not skip the SSRF policy. trust_env=False: a proxy from the
        # environment is not used, because a proxy would resolve and connect by itself (the local check would be pointless).
        self._http = httpx.Client(transport=transport, timeout=TIMEOUT, follow_redirects=False, trust_env=False)
        self._allow_private, self._resolver, self._sleep, self._retries = allow_private_hosts, resolver, sleep, retries
        self._check_deadline, self._clock = check_deadline, clock
        self._deadline_at: Optional[float] = None  # only populated during check_connection

    # ---------------- security ----------------

    def _scrub(self, text: str) -> str:
        """Remove credentials from text originating from Odoo before it becomes an error message, and limit its length."""
        if self._key:
            text = re.sub(re.escape(self._key), "***", text, flags=re.IGNORECASE)
        return text[:MAX_ERROR_TEXT]

    def _resolve_target(self) -> Target:
        """Apply the SSRF policy on EVERY request (without cache) and return the destination IP address."""
        try:
            return resolve_and_validate(self._base_url, allow_private=self._allow_private, resolve=self._resolver)
        except UrlNotAllowed as exc:
            raise OdooTransientError(str(exc), code="URL_NOT_ALLOWED") from exc
        except (socket.gaierror, UnicodeError) as exc:
            raise OdooTransientError("Odoo host cannot be resolved", code="UNREACHABLE") from exc

    def _request_timeout(self) -> httpx.Timeout:
        """Timeout per attempt; during the connection check it is limited by the remaining total time (20-second limit)."""
        if self._deadline_at is None:
            return TIMEOUT
        remaining = self._deadline_at - self._clock()
        if remaining <= 0:
            raise OdooTransientError("Odoo did not respond in time", code="TIMEOUT")
        return httpx.Timeout(min(10.0, remaining), connect=min(5.0, remaining))

    @staticmethod
    def _pinned(target: Target, path: str) -> tuple[str, dict, dict]:
        """Validated IP-based URL, plus the Host header and SNI carrying the original host name."""
        ip_host = f"[{target.ip}]" if ":" in target.ip else target.ip
        default_port = 443 if target.scheme == "https" else 80
        name = f"[{target.host}]" if ":" in target.host else target.host
        headers = {"Host": name if target.port == default_port else f"{name}:{target.port}"}
        extensions: dict = {}
        if target.scheme == "https":
            try:
                ipaddress.ip_address(target.host)  # host is an IP address: there is no name for SNI
            except ValueError:
                extensions["sni_hostname"] = target.host  # the TLS certificate is still checked against the host name
        return f"{target.scheme}://{ip_host}:{target.port}{path}", headers, extensions

    @staticmethod
    def _read_limited(resp: httpx.Response) -> bytes:
        """Read the response body with a size limit, so that a malicious server cannot exhaust memory."""
        chunks, total = [], 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise OdooTransientError("Odoo response is too large", code="UNREACHABLE")
            chunks.append(chunk)
        return b"".join(chunks)

    # ---------------- JSON-2 transport ----------------

    def _raise_http_error(self, status: int, content: bytes):
        """Map an Odoo error response (4xx status) to an application error. The error body is cleaned of credentials."""
        try:
            info = json.loads(content)
        except ValueError:
            info = None
        if not isinstance(info, dict):
            if status == 404:  # Odoo answers with an HTML 404 if the database in the header does not exist
                raise OdooTransientError("database not found on the Odoo server", code="DATABASE_NOT_FOUND")
            raise OdooBusinessError(f"Odoo HTTP {status}")
        if status == 401:
            log.error("odoo.auth_failed check the Odoo URL, database and API key")
            raise OdooTransientError("Odoo rejected the API key", code="AUTH_FAILED")
        name = str(info.get("name", "")).rsplit(".", 1)[-1] or "RPCError"
        message = self._scrub(f"{name}: {info.get('message') or 'Odoo error'}")  # `debug` (traceback) is deliberately not used
        if name in BUSINESS_ERROR_NAMES or status == 403:
            raise OdooBusinessError(message)
        if name == "NotFound":
            raise OdooBusinessError(message)  # model or method does not exist: not a temporary disruption
        raise OdooTransientError(message, code="ODOO_ERROR")

    def _post_once(self, path: str, body: dict):
        target = self._resolve_target()  # SSRF policy + destination IP address, every time
        timeout = self._request_timeout()
        url, headers, extensions = self._pinned(target, urlparse(self._base_url).path.rstrip("/") + path)
        headers.update({"Authorization": f"bearer {self._key}", "X-Odoo-Database": self._db})
        try:
            with self._http.stream("POST", url, json=body, headers=headers, extensions=extensions, timeout=timeout) as resp:
                if resp.status_code >= 500 or resp.status_code in RETRYABLE_HTTP:
                    raise OdooTransientError(f"Odoo HTTP {resp.status_code}", code="UNREACHABLE")
                content = self._read_limited(resp)
                if resp.status_code >= 400:
                    self._raise_http_error(resp.status_code, content)
        except httpx.TimeoutException as exc:
            raise OdooTransientError("Odoo did not respond in time", code="TIMEOUT") from exc
        except httpx.HTTPError as exc:  # connection dropped, TLS failed, etc.
            raise OdooTransientError(f"Odoo unreachable ({type(exc).__name__})", code="UNREACHABLE") from exc
        try:
            return json.loads(content)  # also catches redirect responses (3xx) without a JSON body
        except ValueError as exc:
            raise OdooTransientError("Odoo returned a non-JSON response", code="UNREACHABLE") from exc

    def _post(self, path: str, body: dict, retries: int = 0):
        """One logical call with limited retry only for network disruptions (TIMEOUT/UNREACHABLE)."""
        for attempt in range(retries + 1):
            try:
                return self._post_once(path, body)
            except OdooTransientError as exc:
                if exc.code not in RETRYABLE_CODES or attempt == retries:
                    raise
                self._sleep(RETRY_BASE_SECONDS * 2**attempt)

    def _call(self, model: str, method: str, payload: dict, retries: int = 0):
        """Call an Odoo model method. `payload` = named arguments (ids, domain, fields, vals_list, context, ...)."""
        return self._post(f"/json/2/{model}/{method}", payload, retries=retries)

    def _search_read(self, model: str, domain: list, fields: list, limit: Optional[int] = None) -> list:
        payload = {"domain": domain, "fields": fields}
        if limit:
            payload["limit"] = limit
        return self._call(model, "search_read", payload, retries=self._retries)  # reads: may be repeated

    # ---------------- connection status ----------------

    def check_connection(self) -> ConnectionStatus:
        """Validate the connection directly to the configured database. Does not raise errors; a failure
        becomes a status with a safe code and fixed text (never raw text from Odoo)."""
        now = datetime.now(timezone.utc)
        version: Optional[str] = None
        self._deadline_at = self._clock() + self._check_deadline  # total limit, including all retries
        try:
            info = self._post("/web/webclient/version_info", {"jsonrpc": "2.0", "method": "call", "params": {}},
                              retries=self._retries)
            info = info.get("result") if isinstance(info, dict) else None
            if isinstance(info, dict) and info.get("server_version"):
                version = str(info["server_version"])[:40]  # text from an external server: length is limited
            # A single authenticated call proves the URL, database, and API key at once.
            who = self._call("res.users", "context_get", {}, retries=self._retries)
            if not isinstance(who, dict) or not who.get("uid"):
                raise OdooTransientError("Odoo did not identify the API key owner", code="AUTH_FAILED")
        except (OdooTransientError, OdooBusinessError) as exc:
            return self._failure(exc.code, version, now)
        except Exception:  # unexpected response shape
            return self._failure("UNKNOWN", version, now)
        finally:
            self._deadline_at = None
        return ConnectionStatus("live", True, self._db, version, now)

    def _failure(self, code: str, version: Optional[str], now: datetime) -> ConnectionStatus:
        code = code if code in SAFE_ERROR_MESSAGES else "UNKNOWN"
        log.warning("odoo.connection_check_failed code=%s", code)  # code only; without a message or raw response
        return ConnectionStatus("live", False, self._db, version, now, code, SAFE_ERROR_MESSAGES[code])

    # ---------------- read ----------------

    def _location_index(self) -> dict[int, tuple[str, bool, str]]:
        """odoo_id -> (application code, whether it is a damaged stock location, name)."""
        rows = self._search_read("stock.location", [["usage", "=", "internal"]], ["id", "name", "complete_name", "usage"])
        index = {}
        for r in rows:
            parts = r["complete_name"].split("/")
            damaged = parts[-1] == DAMAGED_NAME
            index[r["id"]] = (parts[0] if damaged else r["name"], damaged, r["name"])
        return index

    def list_locations(self) -> list[OdooLocation]:
        return [
            OdooLocation(oid, code, name, "WAREHOUSE" if code == WAREHOUSE_CODE else "STORE")
            for oid, (code, damaged, name) in self._location_index().items()
            if not damaged
        ]

    def list_variants(self) -> list[OdooVariant]:
        products = self._search_read("product.product", [], ["id", "product_tmpl_id", "display_name", "default_code"])
        # [Assumption] Low-stock threshold = reorder rule (stock.warehouse.orderpoint.product_min_qty).
        rules = self._search_read("stock.warehouse.orderpoint", [], ["product_id", "product_min_qty"])
        thresholds: dict[int, int] = {}
        for rule in rules:
            pid = rule["product_id"][0]
            thresholds[pid] = max(thresholds.get(pid, 0), int(rule["product_min_qty"]))
        variants = []
        for p in products:
            template_id, template_name = p["product_tmpl_id"]
            # Odoo prepends the internal code to the name: "[SKU] Name (Attribute)". Strip it before mapping.
            template_name = REFERENCE_PREFIX.sub("", template_name)
            display_name = REFERENCE_PREFIX.sub("", p["display_name"])
            variant_name = display_name.replace(template_name, "").strip(" ()") or "Default"
            variants.append(
                OdooVariant(p["id"], template_id, template_name, variant_name,
                            p["default_code"] or f"ODOO-{p['id']}", thresholds.get(p["id"], 0))
            )
        return variants

    def get_stock(self) -> list[OdooStock]:
        index = self._location_index()
        quants = self._search_read("stock.quant", [["location_id.usage", "=", "internal"]],
                                   ["product_id", "location_id", "quantity"])
        totals: dict[tuple[int, str], list[int]] = {}
        for q in quants:
            location = index.get(q["location_id"][0])
            if location is None:
                continue
            code, damaged, _ = location
            cell = totals.setdefault((q["product_id"][0], code), [0, 0])
            cell[1 if damaged else 0] += max(int(round(q["quantity"])), 0)
        return [OdooStock(pid, code, sellable, damaged) for (pid, code), (sellable, damaged) in totals.items()]

    # ---------------- write ----------------

    def _single_id(self, model: str, domain: list, what: str) -> int:
        rows = self._search_read(model, domain, ["id"], limit=1)
        if not rows:
            raise OdooBusinessError(f"Odoo has no {what}")
        return rows[0]["id"]

    def _location_id(self, code: str) -> int:
        return self._single_id("stock.location", [["name", "=", code], ["usage", "=", "internal"]], f"location '{code}'")

    def _existing_picking(self, reference: str) -> Optional[OdooResult]:
        """Duplicate check: does a document with origin = this reference already exist? Performed FIRST,
        before any location/type lookup, so that a resubmission does not create a second document."""
        found = self._search_read("stock.picking", [["origin", "=", reference]], ["id", "name", "state"], limit=1)
        if not found:
            return None
        picking = found[0]
        if picking.get("state") != "done":
            # A previous run was interrupted after create: complete the same document, do not create a new one.
            self._finish_picking(picking["id"])
        return OdooResult(reference, picking["name"], True)

    def _finish_picking(self, picking_id: int) -> str:
        """Confirm, allocate, and validate the picking, then ENSURE its status is `done` (return its name)."""
        self._call("stock.picking", "action_confirm", {"ids": [picking_id]})
        self._call("stock.picking", "action_assign", {"ids": [picking_id]})
        # button_validate with skip_immediate/skip_backorder completes the picking without a wizard (verified in Odoo 19).
        self._call("stock.picking", "button_validate", {"ids": [picking_id],
                   "context": {"skip_immediate": True, "skip_backorder": True}})
        # button_validate may return only a wizard; without this check we would report
        # success even though Odoo stock has not been deducted.
        rows = self._call("stock.picking", "read", {"ids": [picking_id], "fields": ["name", "state"]}, retries=self._retries)
        state = rows[0].get("state") if rows else None
        if state != "done":
            raise OdooBusinessError(f"Picking {picking_id} is not done after validation (state={state})")
        return rows[0]["name"]

    def _create_picking(
        self, reference: str, picking_type: str, src_id: int, dest_id: int, product_id: int, qty: int
    ) -> OdooResult:
        type_id = self._single_id("stock.picking.type", [["code", "=", picking_type]], f"'{picking_type}' picking type")
        # Odoo 19: stock.move has no "name" field; the reference is sufficient on picking.origin.
        move = {"product_id": product_id, "product_uom_qty": qty,
                "location_id": src_id, "location_dest_id": dest_id}
        picking_id = self._call("stock.picking", "create", {"vals_list": [{
            "picking_type_id": type_id, "location_id": src_id, "location_dest_id": dest_id,
            "origin": reference, "move_ids": [[0, 0, move]],
        }]})[0]
        name = self._finish_picking(picking_id)
        return OdooResult(reference, name, False)

    def post_sale(self, reference: str, odoo_variant_id: int, location_code: str, qty: int) -> OdooResult:
        existing = self._existing_picking(reference)
        if existing:
            return existing
        src = self._location_id(location_code)
        customers = self._single_id("stock.location", [["usage", "=", "customer"]], "customer location")
        return self._create_picking(reference, "outgoing", src, customers, odoo_variant_id, qty)

    def post_transfer(self, reference: str, odoo_variant_id: int, from_code: str, to_code: str, qty: int) -> OdooResult:
        existing = self._existing_picking(reference)
        if existing:
            return existing
        return self._create_picking(reference, "internal", self._location_id(from_code), self._location_id(to_code),
                                    odoo_variant_id, qty)
