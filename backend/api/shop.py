# backend/api/shop.py
from fastapi import APIRouter, HTTPException

from backend.api.schemas import ShopRequest, ShopResponse
from backend.api import utils
from backend.services import catalog

router = APIRouter()


@router.post("/api/shop", response_model=ShopResponse)
def shop(req: ShopRequest):
    try:
        result = catalog.search_products(req.query, max_price_cents=req.max_price_cents, limit=req.limit)
        return ShopResponse(products=utils.products_to_list(result.products), source=result.source, note=result.note)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Shop search failed")
