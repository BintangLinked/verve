"""Request/response shapes for POST /api/sales (design sections 7.1 and 9).

Purpose: input validation at the system boundary (trust boundary). The quantity must be a whole number
1..1000 (strict: 1.5, "abc", and true are rejected); a POS sale must specify the shop.

Relations: used by routers/sales.py and services/sales.py.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class SaleRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)  # idempotency key from the client/POS
    variant_id: int = Field(strict=True)
    quantity: int = Field(strict=True, ge=1, le=1000)
    channel: Literal["POS", "ONLINE"]
    store_id: Optional[str] = None  # shop code (SHOP1/SHOP2); ignored for the ONLINE channel

    @model_validator(mode="after")
    def _pos_requires_store(self):
        if self.channel == "POS" and not self.store_id:
            raise ValueError("store_id is required for POS sales")
        return self


class SaleResponse(BaseModel):
    id: int
    request_id: str
    variant_id: int
    location_code: str  # the location actually deducted (shop, or WH for online)
    quantity: int
    channel: str
    remaining: int  # sellable stock at that location after the sale (on replay: the current stock)
    odoo_status: str  # PENDING | SYNCED | FAILED
    replayed: bool  # true if this is a repeat answer for a request_id that has already succeeded
