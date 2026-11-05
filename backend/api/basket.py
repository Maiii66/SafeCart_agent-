# backend/api/basket.py
from fastapi import APIRouter, HTTPException

from backend.agent import explain, shopper
from backend.api import utils
from backend.api.schemas import (
    BasketItemResultSchema,
    BasketRequest,
    BasketResponse,
    BasketSelectionSchema,
    BasketSummarySchema,
    RankedProductSchema,
)
from backend.services import channel3_service

router = APIRouter()


def _product(choice):
    return RankedProductSchema(**utils.ranked_to_dict(choice.item))


def _summary(result):
    return BasketSummarySchema(
        total_price_cents=result.total_price_cents,
        total_score=result.total_score,
        chosen=[
            BasketSelectionSchema(category=choice.category, product=_product(choice))
            for choice in result.chosen
        ],
        unfulfilled=list(result.unfulfilled),
    )


@router.post("/api/basket", response_model=BasketResponse)
def basket(req: BasketRequest):
    try:
        outcome = shopper.search_basket(
            req.items, req.total_budget_cents, limit=req.limit
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except channel3_service.Channel3Error:
        raise HTTPException(status_code=502, detail="Product search is unavailable right now.")

    request_summary = "A basket with: " + ", ".join(req.items)
    explanation, explanation_source = explain.explain_basket(
        request_summary,
        outcome.optimal.chosen,
        outcome.total_cents,
        outcome.total_budget_cents,
    )

    return BasketResponse(
        source=outcome.source,
        note=outcome.note,
        total_budget_cents=outcome.total_budget_cents,
        total_cents=outcome.total_cents,
        remaining_cents=outcome.remaining_cents,
        items=[
            BasketItemResultSchema(
                query=item.query,
                product=_product_of(item.item),
                note=item.note,
            )
            for item in outcome.items
        ],
        optimal=_summary(outcome.optimal),
        greedy=_summary(outcome.greedy),
        score_gain=outcome.comparison.score_gain,
        price_difference_cents=outcome.comparison.price_difference_cents,
        explanation=explanation,
        explanation_source=explanation_source,
    )


def _product_of(item):
    if item is None:
        return None
    return RankedProductSchema(**utils.ranked_to_dict(item))
