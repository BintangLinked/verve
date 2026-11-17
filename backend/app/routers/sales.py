"""POST /api/sales endpoint.

Purpose: HTTP layer for recording sales. 201 if a new sale is created, 200 if the
same request_id is repeated with the same content (replay). Business errors are raised by the service
as AppError (404, 409, 422).

Relations: calls services/sales.py; used by POS/online store and the "record sale" form
in the frontend (Task 8); registered in app/main.py.
"""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_session
from app.schemas.sales import SaleRequest, SaleResponse
from app.services import sales as sales_service

router = APIRouter(prefix="/api/sales", tags=["sales"], dependencies=[Depends(require_admin)])


@router.post("", response_model=SaleResponse, status_code=201)
def create_sale(body: SaleRequest, response: Response, session: Session = Depends(get_session)):
    result, created = sales_service.record_sale(session, body)
    if not created:
        response.status_code = 200
    return result
