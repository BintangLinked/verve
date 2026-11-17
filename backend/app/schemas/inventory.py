"""Request/response shapes for the inventory API (design section 9).

Purpose: a stable JSON contract for the frontend; Pydantic also validates and
documents it automatically at /docs.

Relations: used by routers/inventory.py and services/inventory.py, and
mirrored as TypeScript types in frontend/lib/types.ts (Task 7).
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

# Stock status per shop. The central warehouse has no threshold so its status is null.
StockStatus = Literal["OK", "LOW", "OUT"]


class StockCell(BaseModel):
    location_code: str
    available: int  # sellable stock (qty_sellable)
    damaged: int  # damaged stock, never sold
    status: Optional[StockStatus]


class InventoryItem(BaseModel):
    variant_id: int
    sku: str
    product_name: str
    variant_name: str
    threshold: int
    stock: list[StockCell]


class InventoryResponse(BaseModel):
    as_of: datetime  # time this data was read; the frontend displays it as "last updated"
    items: list[InventoryItem]


class Movement(BaseModel):
    id: int
    location_code: str
    bucket: str
    type: str
    qty_delta: int
    ref_type: Optional[str]
    ref_id: Optional[int]
    created_at: datetime


class SyncResult(BaseModel):
    variants: int
    locations: int
    adjustments: int  # number of stock_movements rows of type SYNC_ADJUSTMENT that were written
