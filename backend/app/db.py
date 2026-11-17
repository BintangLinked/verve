"""PostgreSQL database connection (application database `database_verve`).

Purpose: provides the engine, the session factory, and the FastAPI dependency `get_session`.
The application uses direct SQL through `text()` in the repositories layer, not ORM models,
so that atomic statements (UPDATE ... WHERE qty >= n, ON CONFLICT) are clearly visible.

Relations: used by all routers (via Depends(get_session)), services, and the worker.
Tests replace `get_session` through dependency_overrides to use the test database.
"""
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

# pool_pre_ping: dropped connections are detected before use (useful after a PostgreSQL restart).
engine = create_engine(get_settings().database_url, pool_pre_ping=True)

# expire_on_commit=False: objects returned by a query remain readable after commit.
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """One session per request. The commit is performed explicitly by the service, not here."""
    with SessionLocal() as session:
        yield session
