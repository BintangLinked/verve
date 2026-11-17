"""Unique index on the Odoo mapping keys.

Purpose: sync uses `INSERT ... ON CONFLICT (odoo_*_id)` to upsert master data.
Without a unique index, two syncs running concurrently (startup + refresh button) could
create duplicate products/variants.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX ux_products_odoo_template ON products (odoo_template_id)")
    op.execute("CREATE UNIQUE INDEX ux_variants_odoo_variant ON variants (odoo_variant_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_variants_odoo_variant")
    op.execute("DROP INDEX IF EXISTS ux_products_odoo_template")
