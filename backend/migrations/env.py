"""Alembic environment: determines which database is migrated.

Purpose: `alembic upgrade head` uses DATABASE_URL from app/config.py; tests can
override the URL through the `sqlalchemy.url` config option (see tests/conftest.py).

Relations: reads the files in migrations/versions/. There is no ORM model, so there is no autogenerate.
"""
from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings

config = context.config


def run_migrations_online() -> None:
    # The URL from the config option (used by tests) takes precedence over DATABASE_URL.
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
