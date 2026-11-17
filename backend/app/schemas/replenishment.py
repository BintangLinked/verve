"""Request/response shapes for the replenishment API (design section 9).

Purpose: input validation at the system boundary (whole-number quantity 1..1000, strict) and the JSON contract
for the replenishment panel on the dashboard.

Relations: used by routers/replenishment.py and services/replenishment.py.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ReplenishmentItem(BaseModel):
    """One shop/variant pair whose stock is below the threshold."""

    store_code: str
    variant_id: int
    sku: str
    variant_name: str
    available: int
    threshold: int
    status: Optional[Literal["OK", "LOW", "OUT"]]
    warehouse_available: int  # central warehouse stock, to show whether the request can be fulfilled
    suggested_quantity: int  # quantity needed to reach 2x the threshold
    pending_request: bool  # a PENDING request already exists for this pair


class ReplenishmentRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)  # idempotency key
    store_id: str  # destination shop code (SHOP1/SHOP2)
    variant_id: int = Field(strict=True)
    quantity: int = Field(strict=True, ge=1, le=1000)


class ReplenishmentResponse(BaseModel):
    id: int
    request_id: str
    store_id: str
    variant_id: int
    quantity: int
    status: str  # PENDING | SUBMITTED | FAILED
    replayed: bool
