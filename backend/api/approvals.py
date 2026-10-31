# backend/api/approvals.py
from fastapi import APIRouter

from backend.api.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    StepUpRequest,
    VerdictResponse,
)
from backend.agent import approvals, risk_flags, step_up
from backend.models import Candidate
from backend.services import catalog, catalog_demo

router = APIRouter()


def _find_product(product_id, source):
    if source == "channel3":
        try:
            return catalog.get_live_product(product_id, source)
        except catalog.LivePriceUnavailable:
            return None
    return catalog_demo.get_product(product_id)


def _resolve_candidate(candidate, source):
    product = _find_product(candidate.product_id, source)
    if product is None:
        raise ValueError(f"Product {candidate.product_id} not found from {source}")

    offer = None
    if candidate.domain:
        for found in product.offers:
            if found.domain == candidate.domain:
                offer = found
                break
        if offer is None:
            raise ValueError(
                f"Product {candidate.product_id} has no offer from {candidate.domain}"
            )
    elif product.offers:
        offer = product.offers[0]

    if offer is None:
        raise ValueError(f"No offer for product {candidate.product_id}")
    return Candidate(product=product, offer=offer)


@router.post("/api/approvals", response_model=ApprovalResponse)
def create_approval(req: ApprovalRequest):
    try:
        candidates = [_resolve_candidate(candidate, req.source) for candidate in req.candidates]
    except ValueError as error:
        return ApprovalResponse(ok=False, code="BAD_REQUEST", reason=str(error))

    flags = risk_flags.compute_flags(candidates, req.source)
    res = approvals.create_approval(
        candidates, req.budget_cents, req.source, flags=flags
    )
    return ApprovalResponse(
        ok=res.ok,
        code=res.code,
        reason=res.reason,
        approval_id=res.approval_id if res.approval_id else None,
        payload=res.payload,
    )


@router.post(
    "/api/approvals/{approval_id}/step-up", response_model=VerdictResponse
)
def confirm_step_up(approval_id: str, req: StepUpRequest):
    verdict = step_up.confirm_step_up(approval_id, req.typed_text)
    return VerdictResponse(ok=verdict.ok, code=verdict.code, reason=verdict.reason)
