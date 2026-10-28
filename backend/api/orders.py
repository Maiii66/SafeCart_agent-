# backend/api/orders.py
from fastapi import APIRouter, HTTPException

from backend.api.schemas import OrdersResponse, OrderSchema
from database import repository

router = APIRouter()


@router.get("/api/orders", response_model=OrdersResponse)
def list_orders(limit: int = 200):
    try:
        orders = repository.list_orders(limit=limit)
        return OrdersResponse(orders=[OrderSchema(**o) for o in orders])
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to list orders")


@router.get("/api/orders/{order_id}")
def get_order(order_id: int):
    try:
        order = repository.get_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        return order
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get order")
