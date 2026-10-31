# backend/api/schemas.py
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(BaseModel):
    status: str = "ok"


class ShopRequest(StrictRequest):
    query: str = Field(min_length=1, max_length=200)
    max_price_cents: Optional[int] = Field(default=None, ge=0, le=10_000_000)
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


class CandidateRequest(StrictRequest):
    product_id: str = Field(min_length=1, max_length=200)
    domain: Optional[str] = Field(default=None, max_length=255)


class ApprovalRequest(StrictRequest):
    candidates: List[CandidateRequest]
    budget_cents: int
    source: Literal["demo", "channel3"] = "demo"


class ApprovalResponse(BaseModel):
    ok: bool
    code: str
    reason: str
    approval_id: Optional[str] = None
    payload: Optional[dict] = None


class StepUpRequest(StrictRequest):
    typed_text: str = Field(min_length=1, max_length=40)


class VerdictResponse(BaseModel):
    ok: bool
    code: str
    reason: str = ""


class CheckoutRequest(StrictRequest):
    approval_id: str = Field(min_length=1, max_length=200)


class CheckoutResponse(BaseModel):
    ok: bool
    code: str
    reason: str = ""
    order_id: Optional[int] = None
    approve_url: str = ""


class OrderSchema(BaseModel):
    id: int
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


class StatusResponse(BaseModel):
    paused: bool
    sandbox: bool
    paypal_env: str
    paypal_enabled: bool
    channel3_enabled: bool
    gemini_enabled: bool
    currency: str
    max_budget_cents: int
    daily_cap_cents: int
    spent_last_24h_cents: int
    remaining_daily_cap_cents: int
    max_orders_per_hour: int
    step_up_threshold_cents: int
