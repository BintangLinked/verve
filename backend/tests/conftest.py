"""Shared fixtures for the entire backend test suite.

Purpose: every test runs against a REAL PostgreSQL database (`TEST_DATABASE_URL`),
not a mock, because the behaviour under test (constraints, row locks, ON CONFLICT) can
only be proven on PostgreSQL.

Relations:
- `engine`  : runs the Alembic migrations (migrations/) once per test session.
- `session` : used by tests to prepare/read data; tables are emptied after each test.
- `client`  : FastAPI TestClient whose `get_session` is pointed at the test database.
"""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.auth import LoginThrottle, SessionRevocations, get_revocations, get_throttle, hash_password
from app.config import Settings, get_settings
from app.db import get_session
from app.main import create_app
from app.odoo.factory import get_odoo, get_odoo_or_none
from app.odoo.mock import MockOdooClient
from app.services.connection import ConnectionMonitor, get_monitor
from app.services.sync import sync_from_odoo

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Admin account used only for tests. The hash is computed once per test session because scrypt is deliberately slow.
ADMIN_USER = "admin"
ADMIN_PASSWORD = "correct-horse-battery"
_ADMIN_HASH = hash_password(ADMIN_PASSWORD)


def _alembic_config(url: str) -> Config:
    """Build the Alembic configuration pointed at the test database."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    # '%' must be escaped because configparser uses it for interpolation.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def engine():
    """Engine for the test database; the schema is recreated from the migrations (downgrade, then upgrade)."""
    url = get_settings().test_database_url
    assert url, "TEST_DATABASE_URL belum diisi di backend/.env"
    eng = create_engine(url, pool_pre_ping=True)
    cfg = _alembic_config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine):
    """Session factory for the test database (also used by the concurrency tests: one session per thread)."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def session(engine, session_factory):
    """Session for seeding/checking data. After each test, all data tables are emptied."""
    with session_factory() as s:
        yield s
        s.rollback()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "select quote_ident(schemaname) || '.' || quote_ident(tablename) "
                "from pg_tables where schemaname in ('public', 'mock_odoo') "
                "and tablename <> 'alembic_version'"
            )
        ).scalars().all()
        if rows:
            conn.execute(text("TRUNCATE " + ", ".join(rows) + " RESTART IDENTITY CASCADE"))


@pytest.fixture()
def odoo(engine, session):
    """MockOdooClient on the test database. `session` is requested as well so that the mock_odoo
    schema is emptied after each test (mock state lives in the database, so all instances share state)."""
    return MockOdooClient(engine)


@pytest.fixture()
def test_settings():
    """Settings for tests: admin configured, mock mode. Tests may use `.model_copy(update=...)`."""
    base = get_settings()
    return Settings(
        database_url=base.database_url,
        test_database_url=base.test_database_url,
        odoo_mode="mock",
        odoo_allow_private_hosts=False,  # do not inherit the value from backend/.env
        admin_username=ADMIN_USER,
        admin_password_hash=_ADMIN_HASH,
        session_secret="s" * 40,
        settings_encryption_key=Fernet.generate_key().decode(),
    )


@pytest.fixture()
def client(session_factory, odoo, test_settings):
    """TestClient with the `get_session`, `get_odoo`, settings and login throttle dependencies
    pointed at the test environment (a new throttle per test so failed attempts do not leak between tests)."""
    app = create_app()

    def _test_session():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _test_session
    app.dependency_overrides[get_odoo] = lambda: odoo
    app.dependency_overrides[get_odoo_or_none] = lambda: odoo
    monitor = ConnectionMonitor("mock")  # new connection status per test
    app.dependency_overrides[get_monitor] = lambda: monitor
    app.dependency_overrides[get_settings] = lambda: test_settings
    throttle = LoginThrottle()
    app.dependency_overrides[get_throttle] = lambda: throttle
    revocations = SessionRevocations()  # list of revoked sessions: new per test
    app.dependency_overrides[get_revocations] = lambda: revocations
    return TestClient(app)


@pytest.fixture()
def admin_client(client):
    """TestClient already logged in as admin (session cookie stored in the client)."""
    resp = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return client


@pytest.fixture()
def seeded(session, odoo):
    """Populate the local projection from the Odoo mock (sample data) through the sync service."""
    sync_from_odoo(session, odoo)


@pytest.fixture()
def vid(session):
    """Helper function: SKU -> local variant_id."""
    return lambda sku: session.execute(text("select id from variants where sku = :s"), {"s": sku}).scalar_one()


@pytest.fixture()
def qty(session):
    """Helper function: local sellable stock (SKU, location code)."""
    return lambda sku, loc: session.execute(
        text(
            "select sl.qty_sellable from stock_levels sl join variants v on v.id = sl.variant_id "
            "join locations l on l.id = sl.location_id where v.sku = :s and l.code = :l"
        ),
        {"s": sku, "l": loc},
    ).scalar_one()


@pytest.fixture()
def count(session):
    """Helper function: row count of a table (table name supplied by the test itself, not user input)."""
    return lambda table, where="true": session.execute(text(f"select count(*) from {table} where {where}")).scalar_one()


@pytest.fixture()
def basic_ids(session: Session) -> dict:
    """Minimal data (1 location, 1 product, 1 variant) for the schema constraint tests."""
    loc = session.execute(
        text("insert into locations (code, name, type) values ('SHOP1', 'Store 1', 'STORE') returning id")
    ).scalar_one()
    prod = session.execute(text("insert into products (name) values ('Cotton T-Shirt') returning id")).scalar_one()
    var = session.execute(
        text(
            "insert into variants (product_id, sku, name, low_stock_threshold) "
            "values (:p, 'TS-BLK-L', 'Black / L', 5) returning id"
        ),
        {"p": prod},
    ).scalar_one()
    session.commit()
    return {"location_id": loc, "variant_id": var}
