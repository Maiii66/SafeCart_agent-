# backend/api/schemas.py
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class ShopRequest(BaseModel):
    query: str
    max_price_cents: Optional[int] = None
    limit: int = Field(default=10, ge=1, le=50)


class OfferSchema(BaseModel):
    domain: str
    price_cents: int
    currency: str
    availability: str
    condition: str
    url: str


class ProductSchema(BaseModel):
    id: str
    title: str
    description: str
    brand: str
    image_url: str
    category: str
    rating_value: Optional[float]
    rating_count: int
    key_features: List[str]
    offers: List[OfferSchema]


class ShopResponse(BaseModel):
    products: List[ProductSchema]
    source: str
    note: str = ""


class CandidateRequest(BaseModel):
    product_id: str
    domain: Optional[str] = None


class ApprovalRequest(BaseModel):
    candidates: List[CandidateRequest]
    budget_cents: int
    source: str = "demo"
    flags: Optional[Dict[str, Any]] = None


class ApprovalResponse(BaseModel):
    ok: bool
    code: str
    reason: str
    approval_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


class CheckoutRequest(BaseModel):
    approval_id: str


class CheckoutResponse(BaseModel):
    ok: bool
    code: str
    reason: str = ""
    order_id: Optional[int] = None
    approve_url: str = ""


class OrderSchema(BaseModel):
    id: int
    approval_id: str
    paypal_order_id: Optional[str]
    status: str
    total_cents: int
    created_at: int
    captured_at: Optional[int]
    capture_id: Optional[str]
    note: Optional[str]


class OrdersResponse(BaseModel):
    orders: List[OrderSchema]


class LogSchema(BaseModel):
    id: int
    actor: str
    action: str
    detail: str
    ref: Optional[str]
    ts: str


class LogsResponse(BaseModel):
    logs: List[LogSchema]
