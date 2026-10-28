# backend/api/approvals.py
from fastapi import APIRouter, HTTPException

from backend.api.schemas import ApprovalRequest, ApprovalResponse
from backend.agent import approvals
from backend.models import Candidate, Product, Offer
from backend.services import catalog, catalog_demo

router = APIRouter()


def _resolve_candidate(product_id: str, source: str, preferred_domain: str = None) -> Candidate:
    # Try to resolve from the appropriate source
    if source == "demo" or source not in ("channel3",):
        product = catalog_demo.get_product(product_id)
        if not product:
            # Fallback to catalog lookup
            try:
                product = catalog.get_live_product(product_id, source) if source else catalog_demo.get_product(product_id)
            except Exception:
                product = None
        if not product:
            raise ValueError(f"Product {product_id} not found")
        # Pick offer - prefer matching domain if specified
        offer = None
        if preferred_domain:
            for o in product.offers:
                if o.domain == preferred_domain:
                    offer = o
                    break
        if offer is None and product.offers:
            offer = product.offers[0]
        if offer is None:
            raise ValueError(f"No offer for product {product_id}")
        return Candidate(product=product, offer=offer)
    else:
        # channel3 - try to get product via catalog
        try:
            product = catalog.get_live_product(product_id, source)
        except Exception:
            raise ValueError(f"Product {product_id} not found from {source}")
        if not product:
            raise ValueError(f"Product {product_id} not found")
        offer = None
        if preferred_domain:
            for o in product.offers:
                if o.domain == preferred_domain:
                    offer = o
                    break
        if offer is None and product.offers:
            offer = product.offers[0]
        if offer is None:
            raise ValueError(f"No offer for product {product_id}")
        return Candidate(product=product, offer=offer)


@router.post("/api/approvals", response_model=ApprovalResponse)
def create_approval(req: ApprovalRequest):
    try:
        candidates = []
        for c in req.candidates:
            cand = _resolve_candidate(c.product_id, req.source, c.domain)
            candidates.append(cand)
        res = approvals.create_approval(candidates, req.budget_cents, req.source, flags=req.flags)
        return ApprovalResponse(
            ok=res.ok,
            code=res.code,
            reason=res.reason,
            approval_id=res.approval_id if res.approval_id else None,
            payload=res.payload,
        )
    except ValueError as e:
        return ApprovalResponse(ok=False, code="BAD_REQUEST", reason=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create approval")
