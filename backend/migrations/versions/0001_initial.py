"""Initial application schema (design section 6.1).

All tables here are OWNED BY THE APPLICATION. Master data (products, variants, locations) and
authoritative stock are owned by Odoo; these tables are only a copy/projection plus operational records.
The `odoo_*` columns are mapping keys to Odoo records.

Business rules are enforced by the database through CHECK/UNIQUE, not only by code:
- stock must not be negative (CHECK >= 0): the last safeguard against overselling
- request_id is unique: prevents duplicate sales when a client repeats a request
- idempotency_key is unique: one outbox event per request

Enums are implemented as CHECK (col IN (...)), not as PostgreSQL enum types, so they are easy to change.
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# The drop order is the reverse of the create order because of foreign keys.
TABLES = [
    "integration_events",
    "replenishment_requests",
    "sales",
    "stock_movements",
    "stock_levels",
    "variants",
    "products",
    "locations",
]


def upgrade() -> None:
    # Stock locations: the central warehouse (WH) and the retail outlet stores (SHOP1, SHOP2) are separate.
    op.execute(
        """
        CREATE TABLE locations (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('WAREHOUSE', 'STORE')),
            odoo_location_id INTEGER
        )
        """
    )
    op.execute(
        """
        CREATE TABLE products (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            odoo_template_id INTEGER
        )
        """
    )
    # Low-stock threshold per variant (applies to locations of type STORE).
    op.execute(
        """
        CREATE TABLE variants (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products (id),
            sku TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            odoo_variant_id INTEGER,
            low_stock_threshold INTEGER NOT NULL DEFAULT 0 CHECK (low_stock_threshold >= 0)
        )
        """
    )
    # Stock projection per (variant, location). qty_damaged is kept separate from qty_sellable so
    # that damaged returned goods are never counted as sellable stock.
    op.execute(
        """
        CREATE TABLE stock_levels (
            variant_id INTEGER NOT NULL REFERENCES variants (id),
            location_id INTEGER NOT NULL REFERENCES locations (id),
            qty_sellable INTEGER NOT NULL DEFAULT 0 CHECK (qty_sellable >= 0),
            qty_damaged INTEGER NOT NULL DEFAULT 0 CHECK (qty_damaged >= 0),
            synced_at TIMESTAMPTZ,
            PRIMARY KEY (variant_id, location_id)
        )
        """
    )
    # Stock movement history (append-only), shown in the dashboard detail drawer.
    op.execute(
        """
        CREATE TABLE stock_movements (
            id BIGSERIAL PRIMARY KEY,
            variant_id INTEGER NOT NULL REFERENCES variants (id),
            location_id INTEGER NOT NULL REFERENCES locations (id),
            bucket TEXT NOT NULL CHECK (bucket IN ('sellable', 'damaged')),
            type TEXT NOT NULL CHECK (type IN ('SALE', 'RETURN_DAMAGED', 'REPLENISHMENT', 'SYNC_ADJUSTMENT')),
            qty_delta INTEGER NOT NULL,
            ref_type TEXT,
            ref_id BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_stock_movements_variant_created ON stock_movements (variant_id, created_at DESC)")
    # Sales. request_id UNIQUE is the idempotency gate; payload_hash distinguishes
    # "the same request repeated" from "the same key used for different content".
    op.execute(
        """
        CREATE TABLE sales (
            id BIGSERIAL PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            variant_id INTEGER NOT NULL REFERENCES variants (id),
            location_id INTEGER NOT NULL REFERENCES locations (id),
            qty INTEGER NOT NULL CHECK (qty > 0),
            channel TEXT NOT NULL CHECK (channel IN ('POS', 'ONLINE')),
            odoo_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (odoo_status IN ('PENDING', 'SYNCED', 'FAILED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Replenishment requests from a store to the central warehouse. payload_hash is added beyond
    # the designed table because its idempotency pattern is the same as for sales.
    op.execute(
        """
        CREATE TABLE replenishment_requests (
            id BIGSERIAL PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            store_location_id INTEGER NOT NULL REFERENCES locations (id),
            variant_id INTEGER NOT NULL REFERENCES variants (id),
            qty_requested INTEGER NOT NULL CHECK (qty_requested > 0),
            status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SUBMITTED', 'FAILED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Outbox: every sale/replenishment writes one event in the same transaction,
    # then the worker sends it to Odoo. idempotency_key becomes the reference in Odoo.
    op.execute(
        """
        CREATE TABLE integration_events (
            id BIGSERIAL PRIMARY KEY,
            event_type TEXT NOT NULL CHECK (event_type IN ('SALE', 'REPLENISHMENT')),
            aggregate_id BIGINT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PROCESSING', 'DONE', 'FAILED')),
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            processed_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_integration_events_due ON integration_events (status, next_attempt_at)")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
