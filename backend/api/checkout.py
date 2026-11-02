# backend/api/checkout.py
from fastapi import APIRouter

from backend.api.schemas import CheckoutRequest, CheckoutResponse
from backend.agent import checkout

router = APIRouter()


@router.post("/api/checkout", response_model=CheckoutResponse)
def api_checkout(req: CheckoutRequest):
    res = checkout.execute_checkout(req.approval_id)
    return CheckoutResponse(
        ok=res.ok,
        code=res.code,
        reason=res.reason or "",
        order_id=res.order_id,
        approve_url=res.approve_url or "",
    )
