"""GET /health endpoint: checks that the API is alive and the database is reachable.

Purpose: used by monitoring/deployment for liveness checks. Returns 503 with the
standard error shape if the database cannot be accessed.

Relations: uses get_session from app/db.py; registered in app/main.py.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session
from app.errors import AppError

router = APIRouter()


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    try:
        session.execute(text("select 1"))
    except SQLAlchemyError as exc:
        raise AppError("DB_UNAVAILABLE", "Database unreachable", 503) from exc
    return {"status": "ok"}
