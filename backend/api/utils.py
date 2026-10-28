# backend/api/utils.py
from typing import Any, Dict, List

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
        "key_features": list(p.key_features) if hasattr(p.key_features, "__iter__") and not isinstance(p.key_features, str) else list(p.key_features),
        "offers": [offer_to_dict(o) for o in p.offers],
    }


def products_to_list(products) -> List[Dict[str, Any]]:
    return [product_to_dict(p) for p in products]
