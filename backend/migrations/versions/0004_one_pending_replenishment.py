"""One PENDING replenishment request per (store, variant) pair.

Purpose: the rule "there must be no duplicate request still pending" is enforced by the database
(partial unique index), not only by a check in code, so two different requests that
arrive concurrently for the same pair cannot both succeed.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX ux_replenishment_one_pending "
        "ON replenishment_requests (store_location_id, variant_id) WHERE status = 'PENDING'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_replenishment_one_pending")
