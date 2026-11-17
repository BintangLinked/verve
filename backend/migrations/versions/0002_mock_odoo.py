"""Schema `mock_odoo`: holds the state of the fake (mock) Odoo.

Why in PostgreSQL and not in memory: the API and the outbox worker are two separate
processes, and both must see the same "Odoo". This schema represents the Odoo database
and may only be accessed through MockOdooClient (app/odoo/mock.py), not by other application code.
Master data (locations, variants) is not stored here; it is constants in mock.py.
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA mock_odoo")
    # Stock per (variant, location) according to "Odoo".
    op.execute(
        """
        CREATE TABLE mock_odoo.stock (
            odoo_variant_id INTEGER NOT NULL,
            location_code TEXT NOT NULL,
            qty_sellable INTEGER NOT NULL CHECK (qty_sellable >= 0),
            qty_damaged INTEGER NOT NULL DEFAULT 0 CHECK (qty_damaged >= 0),
            PRIMARY KEY (odoo_variant_id, location_code)
        )
        """
    )
    # References already processed. UNIQUE on `reference` imitates deduplication:
    # an operation with the same reference is not processed twice.
    # (Named `refs` because `references` is an SQL keyword.)
    op.execute(
        """
        CREATE TABLE mock_odoo.refs (
            id SERIAL PRIMARY KEY,
            reference TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK (kind IN ('SALE', 'TRANSFER')),
            odoo_ref TEXT GENERATED ALWAYS AS ('MOCK/' || kind || '/' || lpad(id::text, 5, '0')) STORED
        )
        """
    )
    # Artificial failure queue: used to demonstrate retry and error recovery.
    op.execute(
        """
        CREATE TABLE mock_odoo.failures (
            id SERIAL PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('transient', 'business')),
            remaining INTEGER NOT NULL CHECK (remaining >= 0)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS mock_odoo CASCADE")
