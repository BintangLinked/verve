"""Odoo adapter contract (version v1).

Purpose: a single interface (`OdooClient`) used by the whole application, with two
implementations: `MockOdooClient` (mock.py) and `LiveOdooClient` (live.py). Other code
never knows which is active, so moving to a real Odoo only changes ODOO_MODE.

The contract carries a version number (CONTRACT_VERSION) so that a change to the field mapping becomes
an explicit new version, not a silent change.

Relations: used by services/sync.py (read master data and stock), services/outbox.py
(send sales and transfers), and selected by factory.py.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

CONTRACT_VERSION = "v1"


@dataclass(frozen=True)
class OdooLocation:
    """Stock location in Odoo (central warehouse or a shop outlet warehouse)."""

    odoo_id: int
    code: str  # code used by the application API: WH, SHOP1, SHOP2
    name: str
    type: str  # WAREHOUSE | STORE


@dataclass(frozen=True)
class OdooVariant:
    """Product variant with its low-stock threshold."""

    odoo_variant_id: int
    odoo_template_id: int
    product_name: str
    variant_name: str
    sku: str
    low_stock_threshold: int


@dataclass(frozen=True)
class OdooStock:
    """Stock of one variant at one location; damaged stock is kept separate from sellable stock."""

    odoo_variant_id: int
    location_code: str
    qty_sellable: int
    qty_damaged: int


@dataclass(frozen=True)
class OdooResult:
    """Result of a write operation. duplicate=True means the same reference has already been processed."""

    reference: str  # application idempotency key (request_id)
    odoo_ref: str  # document number in Odoo (e.g. picking number)
    duplicate: bool


# Fixed text per error code. The connection status ONLY uses this text, never raw text from Odoo,
# so that no internal detail or secret can leak through the status API.
SAFE_ERROR_MESSAGES = {
    "NOT_CONFIGURED": "Odoo connection settings are incomplete.",
    "URL_NOT_ALLOWED": "The configured Odoo URL is not allowed.",
    "UNREACHABLE": "Odoo could not be reached.",
    "TIMEOUT": "Odoo did not respond in time.",
    "DATABASE_NOT_FOUND": "The configured database was not found on the Odoo server.",
    "AUTH_FAILED": "Odoo rejected the configured login or API key.",
    "UNKNOWN": "The connection check failed unexpectedly.",
}


@dataclass(frozen=True)
class ConnectionStatus:
    """Result of one connection check. checked_at None = never checked."""

    mode: str  # "mock" | "live"
    connected: bool
    database: Optional[str]
    server_version: Optional[str]
    checked_at: Optional[datetime]
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class OdooClient(Protocol):
    def check_connection(self) -> ConnectionStatus:
        """Check the connection to the configured Odoo database. Does not raise errors: a failure
        is returned as a status with a safe error code."""
        ...

    def list_locations(self) -> list[OdooLocation]: ...

    def list_variants(self) -> list[OdooVariant]: ...

    def get_stock(self) -> list[OdooStock]: ...

    def post_sale(self, reference: str, odoo_variant_id: int, location_code: str, qty: int) -> OdooResult:
        """Reduce stock in Odoo. The same reference must not deduct stock twice."""
        ...

    def post_transfer(
        self, reference: str, odoo_variant_id: int, from_code: str, to_code: str, qty: int
    ) -> OdooResult:
        """Move stock between locations (replenishment central warehouse -> shop)."""
        ...
