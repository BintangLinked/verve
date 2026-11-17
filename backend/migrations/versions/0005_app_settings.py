"""The app_settings table: settings changed through the application (Odoo configuration, admin password hash).

Purpose: store simple key/value pairs in PostgreSQL. The Odoo API key is stored ENCRYPTED
(see app/services/app_settings.py); the value column never contains the API key in its original form.
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE app_settings ("
        "key text PRIMARY KEY, value text NOT NULL, updated_at timestamptz NOT NULL DEFAULT clock_timestamp())"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_settings")
