"""Replenishment endpoints: needs list and request creation.

Purpose: HTTP layer only. GET /api/replenishment shows items below the threshold;
POST /api/replenishment/request creates a request (201) or replies again to the same request_id (200).

Relations: calls services/replenishment.py; used by the replenishment panel in the frontend (Task 8);
registered in app/main.py.
"""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_session
from app.schemas.replenishment import ReplenishmentItem, ReplenishmentRequest, ReplenishmentResponse
from app.services import replenishment as service

router = APIRouter(prefix="/api/replenishment", tags=["replenishment"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[ReplenishmentItem])
def list_replenishment(response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return service.list_replenishment(session)


@router.post("/request", response_model=ReplenishmentResponse, status_code=201)
def create_request(body: ReplenishmentRequest, response: Response, session: Session = Depends(get_session)):
    result, created = service.create_request(session, body)
    if not created:
        response.status_code = 200
    return result
