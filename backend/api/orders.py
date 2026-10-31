# backend/api/orders.py
from fastapi import APIRouter, HTTPException, Query

from backend.api.schemas import CheckoutResponse, OrdersResponse, OrderSchema
from backend.agent import checkout
from database import repository

router = APIRouter()


@router.get("/api/orders", response_model=OrdersResponse)
def list_orders(limit: int = Query(200, ge=1, le=500)):
    orders = repository.list_orders(limit=limit)
    return OrdersResponse(orders=[OrderSchema(**order) for order in orders])


@router.get("/api/orders/{order_id}", response_model=OrderSchema)
def get_order(order_id: int):
    order = repository.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderSchema(**order)


@router.post("/api/orders/{order_id}/complete", response_model=CheckoutResponse)
def complete_order(order_id: int):
    result = checkout.complete_order(order_id)
    return CheckoutResponse(
        ok=result.ok,
        code=result.code,
        reason=result.reason,
        order_id=result.order_id,
    )


@router.post("/api/orders/{order_id}/cancel", response_model=CheckoutResponse)
def cancel_order(order_id: int):
    result = checkout.cancel_order(order_id)
    return CheckoutResponse(
        ok=result.ok,
        code=result.code,
        reason=result.reason,
        order_id=result.order_id,
    )
