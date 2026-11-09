# backend/api/shop.py
from fastapi import APIRouter, HTTPException

from backend import config
from backend.agent import explain, shopper
from backend.api import utils
from backend.api.schemas import (
    BlockedSchema,
    RankedProductSchema,
    RejectedSchema,
    ShopRequest,
    ShopResponse,
)
from backend.services import channel3_service

router = APIRouter()


@router.post("/api/shop", response_model=ShopResponse)
def shop(req: ShopRequest):
    budget = req.max_price_cents if req.max_price_cents is not None else config.MAX_BUDGET_CENTS
    try:
        outcome = shopper.search_and_rank(req.query, budget, limit=req.limit)
    except channel3_service.Channel3Error:
        raise HTTPException(status_code=502, detail="Product search is unavailable right now.")

    explanation, explanation_source = explain.explain(req.query, outcome.ranked)
    return ShopResponse(
        products=[RankedProductSchema(**utils.ranked_to_dict(item)) for item in outcome.ranked],
        source=outcome.source,
        note=outcome.note,
        blocked=[BlockedSchema(**utils.blocked_to_dict(product, reasons))
                 for product, reasons in outcome.blocked],
        rejected=[RejectedSchema(**utils.rejected_to_dict(rejection))
                  for rejection in outcome.rejected],
        duplicates=len(outcome.duplicates),
        explanation=explanation,
        explanation_source=explanation_source,
        guardian_reviews=[
            {"product_id": product_id, **review}
            for product_id, review in (outcome.guardian_reviews or {}).items()
        ],
        guardian_status="available" if outcome.guardian_available else "unavailable",
    )
