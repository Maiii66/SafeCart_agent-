# backend/api/utils.py
from typing import Any, Dict, List

from backend.agent import injection
from backend.models import Offer, Product


def offer_to_dict(o: Offer) -> Dict[str, Any]:
    return {
        "domain": o.domain,
        "price_cents": o.price_cents,
        "currency": o.currency,
        "availability": o.availability,
        "condition": o.condition,
        "url": o.url,
    }


def product_to_dict(p: Product) -> Dict[str, Any]:
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "brand": p.brand,
        "image_url": p.image_url,
        "category": p.category,
        "rating_value": p.rating_value,
        "rating_count": p.rating_count,
        "key_features": list(p.key_features),
        "offers": [offer_to_dict(o) for o in p.offers],
    }


def products_to_list(products) -> List[Dict[str, Any]]:
    return [product_to_dict(p) for p in products]


def ranked_to_dict(item) -> Dict[str, Any]:
    """The product plus the ranking that explains its place in the list."""
    data = product_to_dict(item.candidate.product)
    data["title"] = injection.sanitize_for_llm(data["title"], 120)
    data["ranking"] = {
        "score": item.score,
        "breakdown": item.breakdown,
        "flags": list(item.flags),
        "offer": offer_to_dict(item.candidate.offer),
    }
    return data


def blocked_to_dict(product, reasons) -> Dict[str, Any]:
    return {
        "id": product.id,
        "title": injection.sanitize_for_llm(product.title, 120),
        "reasons": list(reasons),
    }


def rejected_to_dict(rejection) -> Dict[str, Any]:
    return {
        "id": rejection.product_id,
        "title": injection.sanitize_for_llm(rejection.title, 120),
        "code": rejection.code,
        "reason": rejection.reason,
    }
