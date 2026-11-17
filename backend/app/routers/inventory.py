"""Inventory endpoints: stock list, movement history, and sync from Odoo.

Purpose: HTTP layer only (parse parameters, call the service, shape the response). There is no
business logic here.

Relations: services/inventory.py (read), services/sync.py (sync); used by the frontend
(dashboard, detail drawer, refresh button); registered in app/main.py.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_session
from app.odoo.base import OdooClient
from app.odoo.factory import get_odoo
from app.schemas.inventory import InventoryResponse, Movement, StockStatus, SyncResult
from app.services import inventory as inventory_service
from app.services import sync as sync_service

router = APIRouter(prefix="/api/inventory", tags=["inventory"], dependencies=[Depends(require_admin)])


@router.get("", response_model=InventoryResponse)
def list_inventory(
    response: Response,
    store: Optional[str] = None,
    q: Optional[str] = None,
    status: Optional[StockStatus] = None,
    session: Session = Depends(get_session),
):
    # no-store: the browser/proxy must not keep stale stock (the answer to the dashboard cache problem).
    response.headers["Cache-Control"] = "no-store"
    return inventory_service.get_inventory(session, store, q, status)


@router.get("/{variant_id}/movements", response_model=list[Movement])
def list_movements(
    variant_id: int,
    limit: int = Query(20, ge=1, le=200),
    session: Session = Depends(get_session),
):
    return inventory_service.get_movements(session, variant_id, limit)


@router.post("/sync", response_model=SyncResult)
def sync_inventory(session: Session = Depends(get_session), odoo: OdooClient = Depends(get_odoo)):
    return sync_service.sync_from_odoo(session, odoo)
