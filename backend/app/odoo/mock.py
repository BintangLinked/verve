"""MockOdooClient: a fake Odoo for development and tests.

Purpose: imitates the Odoo behaviour that matters to the integration without an Odoo instance:
- stock decreases on sale and moves on transfer
- the same reference is not processed twice (deduplication, imitating the `origin` lookup in Odoo)
- business rejection when stock is insufficient
- temporary/business failures that can be injected through `fail_next` for retry demonstrations

State is stored in the PostgreSQL schema `mock_odoo` (migration 0002) so that the API and the worker, which
run as separate processes, see the same Odoo.

Relations: implements the OdooClient contract (base.py); created by factory.py.
"""
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import Engine, text

from app.odoo.base import ConnectionStatus, OdooLocation, OdooResult, OdooStock, OdooVariant
from app.odoo.errors import OdooBusinessError, OdooTransientError

# Sample data from the brief (design section 5). Initial damaged stock is 0.
SAMPLE_DATA = {
    "locations": [
        OdooLocation(1, "WH", "Main Warehouse", "WAREHOUSE"),
        OdooLocation(2, "SHOP1", "Store 1", "STORE"),
        OdooLocation(3, "SHOP2", "Store 2", "STORE"),
    ],
    # (variant, sellable stock per location)
    "variants": [
        (OdooVariant(101, 11, "Cotton T-Shirt", "Black / L", "TS-BLK-L", 5), {"WH": 40, "SHOP1": 8, "SHOP2": 6}),
        (OdooVariant(102, 11, "Cotton T-Shirt", "Black / M", "TS-BLK-M", 5), {"WH": 25, "SHOP1": 10, "SHOP2": 7}),
        (OdooVariant(103, 11, "Cotton T-Shirt", "White / L", "TS-WHT-L", 5), {"WH": 20, "SHOP1": 6, "SHOP2": 4}),
        (OdooVariant(104, 12, "Hydrating Face Serum", "50 ml", "SERUM-50", 6), {"WH": 50, "SHOP1": 20, "SHOP2": 8}),
        (OdooVariant(105, 13, "Eco-Friendly Tote Bag", "Default", "TOTE-ECO", 5), {"WH": 40, "SHOP1": 15, "SHOP2": 9}),
    ],
}


class MockOdooClient:
    def __init__(self, engine: Engine, seed: dict = SAMPLE_DATA):
        self._engine = engine
        self._seed = seed
        self._seed_stock_if_empty()

    # ---- mock state management ----

    def _seed_stock_if_empty(self) -> None:
        with self._engine.begin() as conn:
            # Light lock so that two processes starting at the same time do not seed twice.
            conn.execute(text("LOCK TABLE mock_odoo.stock IN EXCLUSIVE MODE"))
            if conn.execute(text("SELECT 1 FROM mock_odoo.stock LIMIT 1")).first():
                return
            for variant, by_location in self._seed["variants"]:
                for code, qty in by_location.items():
                    conn.execute(
                        text("INSERT INTO mock_odoo.stock (odoo_variant_id, location_code, qty_sellable) VALUES (:v, :l, :q)"),
                        {"v": variant.odoo_variant_id, "l": code, "q": qty},
                    )

    def reset(self) -> None:
        """Return the mock to the sample data (stock, references, and failures cleared)."""
        with self._engine.begin() as conn:
            conn.execute(text("TRUNCATE mock_odoo.stock, mock_odoo.refs, mock_odoo.failures RESTART IDENTITY"))
        self._seed_stock_if_empty()

    def fail_next(self, kind: Literal["transient", "business"], count: int = 1) -> None:
        """Make the next `count` write calls fail. Can be called from a Python shell."""
        with self._engine.begin() as conn:
            conn.execute(text("INSERT INTO mock_odoo.failures (kind, remaining) VALUES (:k, :c)"), {"k": kind, "c": count})

    def _raise_injected_failure(self) -> None:
        """Consume one artificial failure (if any) in its own transaction and then raise its error.

        Performed before the operation transaction so that the decremented count remains saved
        and the operation reference is not consumed by the failed call.
        """
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE mock_odoo.failures SET remaining = remaining - 1 "
                    "WHERE id = (SELECT id FROM mock_odoo.failures WHERE remaining > 0 "
                    "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING kind"
                )
            ).first()
        if row is None:
            return
        if row.kind == "transient":
            raise OdooTransientError("mock: injected transient failure")
        raise OdooBusinessError("mock: injected business failure")

    # ---- connection status ----

    def check_connection(self) -> ConnectionStatus:
        """The mock is always connected (no network); its database is the `mock_odoo` schema."""
        return ConnectionStatus("mock", True, "mock", "mock", datetime.now(timezone.utc))

    # ---- read ----

    def list_locations(self) -> list[OdooLocation]:
        return list(self._seed["locations"])

    def list_variants(self) -> list[OdooVariant]:
        return [variant for variant, _ in self._seed["variants"]]

    def get_stock(self) -> list[OdooStock]:
        # Stock reads (used by sync) can also be made to fail through fail_next.
        self._raise_injected_failure()
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT odoo_variant_id, location_code, qty_sellable, qty_damaged "
                    "FROM mock_odoo.stock ORDER BY odoo_variant_id, location_code"
                )
            ).all()
        return [OdooStock(r.odoo_variant_id, r.location_code, r.qty_sellable, r.qty_damaged) for r in rows]

    # ---- write ----

    @staticmethod
    def _claim_reference(conn, reference: str, kind: str) -> tuple[str, bool]:
        """Deduplication gate. Returns (odoo_ref, duplicate)."""
        row = conn.execute(
            text(
                "INSERT INTO mock_odoo.refs (reference, kind) VALUES (:r, :k) "
                "ON CONFLICT (reference) DO NOTHING RETURNING odoo_ref"
            ),
            {"r": reference, "k": kind},
        ).first()
        if row is not None:
            return row.odoo_ref, False
        existing = conn.execute(text("SELECT odoo_ref FROM mock_odoo.refs WHERE reference = :r"), {"r": reference}).scalar_one()
        return existing, True

    @staticmethod
    def _decrement(conn, odoo_variant_id: int, location_code: str, qty: int) -> None:
        """Reduce stock atomically; raise OdooBusinessError if insufficient or unknown."""
        updated = conn.execute(
            text(
                "UPDATE mock_odoo.stock SET qty_sellable = qty_sellable - :q "
                "WHERE odoo_variant_id = :v AND location_code = :l AND qty_sellable >= :q RETURNING qty_sellable"
            ),
            {"q": qty, "v": odoo_variant_id, "l": location_code},
        ).first()
        if updated is None:
            exists = conn.execute(
                text("SELECT 1 FROM mock_odoo.stock WHERE odoo_variant_id = :v AND location_code = :l"),
                {"v": odoo_variant_id, "l": location_code},
            ).first()
            raise OdooBusinessError("insufficient stock in Odoo" if exists else "unknown product or location in Odoo")

    def post_sale(self, reference: str, odoo_variant_id: int, location_code: str, qty: int) -> OdooResult:
        self._raise_injected_failure()
        # One transaction: if stock is insufficient, the reference record is rolled back as well.
        with self._engine.begin() as conn:
            odoo_ref, duplicate = self._claim_reference(conn, reference, "SALE")
            if not duplicate:
                self._decrement(conn, odoo_variant_id, location_code, qty)
        return OdooResult(reference, odoo_ref, duplicate)

    def post_transfer(self, reference: str, odoo_variant_id: int, from_code: str, to_code: str, qty: int) -> OdooResult:
        self._raise_injected_failure()
        with self._engine.begin() as conn:
            odoo_ref, duplicate = self._claim_reference(conn, reference, "TRANSFER")
            if not duplicate:
                self._decrement(conn, odoo_variant_id, from_code, qty)
                conn.execute(
                    text(
                        "INSERT INTO mock_odoo.stock (odoo_variant_id, location_code, qty_sellable) VALUES (:v, :l, :q) "
                        "ON CONFLICT (odoo_variant_id, location_code) "
                        "DO UPDATE SET qty_sellable = mock_odoo.stock.qty_sellable + :q"
                    ),
                    {"v": odoo_variant_id, "l": to_code, "q": qty},
                )
        return OdooResult(reference, odoo_ref, duplicate)
