"""AI-only product risk review.

Guardian can add caution to a search result, but it never removes a server rule
flag and never participates in approval or payment decisions.
"""

import hashlib
import json

from backend.agent import injection
from backend.services import llm

MAX_PRODUCTS = 10
MAX_REASONS = 3
MAX_REASON_CHARS = 160
MAX_SUMMARY_CHARS = 240
_LEVELS = {"low": 0, "medium": 1, "high": 2}
_CACHE = {}


def _level(value):
    return value if value in _LEVELS else None


def _rule_level(flags):
    return "medium" if flags else "low"


def _listing(product, offer):
    fields = {
        "id": str(product.id),
        "title": injection.sanitize_for_llm(product.title, 160),
        "description": injection.sanitize_for_llm(product.description, 500),
        "brand": injection.sanitize_for_llm(product.brand, 100),
        "category": injection.sanitize_for_llm(product.category, 100),
        "features": [
            injection.sanitize_for_llm(feature, 100)
            for feature in (product.key_features or ())[:10]
        ],
        "merchant": injection.sanitize_for_llm(offer.domain, 120),
        "price_cents": offer.price_cents,
    }
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return fields, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _clean_text(value, limit):
    if not isinstance(value, str):
        return None
    text = injection.sanitize_for_llm(value, limit).strip()
    if not text or injection.scan_text(text).flagged:
        return None
    return text


def _validate(data, products_by_id):
    if (
        not isinstance(data, dict)
        or set(data) != {"reviews"}
        or not isinstance(data.get("reviews"), list)
    ):
        return None
    reviews = {}
    for item in data["reviews"]:
        if not isinstance(item, dict) or set(item) != {
            "product_id", "risk_level", "reasons", "summary"
        }:
            return None
        product_id = item.get("product_id")
        level = _level(item.get("risk_level"))
        reasons = item.get("reasons")
        summary = _clean_text(item.get("summary"), MAX_SUMMARY_CHARS)
        if (
            not isinstance(product_id, str)
            or product_id not in products_by_id
            or level is None
            or not isinstance(reasons, list)
            or len(reasons) > MAX_REASONS
            or summary is None
        ):
            return None
        cleaned_reasons = []
        for reason in reasons:
            cleaned = _clean_text(reason, MAX_REASON_CHARS)
            if cleaned is None:
                return None
            cleaned_reasons.append(cleaned)
        if product_id in reviews:
            return None
        reviews[product_id] = {
            "risk_level": level,
            "reasons": cleaned_reasons,
            "summary": summary,
        }
    if set(reviews) != set(products_by_id):
        return None
    return reviews


def review(candidates, rule_flags=None):
    """Review candidates once, returning (reviews, ai_available).

    A failed or invalid Gemini response returns rule-only reviews. The exception
    boundary is intentional: a safety review must never break product search.
    """
    rule_flags = rule_flags or {}
    candidates = list(candidates or ())[:MAX_PRODUCTS]
    prepared = {}
    cached = {}
    uncached = []
    for candidate in candidates:
        listing, text_hash = _listing(candidate.product, candidate.offer)
        product_id = candidate.product.id
        prepared[product_id] = (listing, text_hash)
        cached_item = _CACHE.get((product_id, text_hash))
        if cached_item is None:
            uncached.append(listing)
        else:
            cached[product_id] = cached_item

    ai_available = True
    if uncached:
        try:
            parsed = _validate(llm.review_guardian(uncached), {item["id"]: item for item in uncached})
        except Exception:
            parsed = None
        if parsed is None:
            ai_available = False
        else:
            for product_id, item in parsed.items():
                _CACHE[(product_id, prepared[product_id][1])] = item
                cached[product_id] = item

    reviews = {}
    for candidate in candidates:
        product_id = candidate.product.id
        rule_level = _rule_level(rule_flags.get(product_id, ()))
        ai_item = cached.get(product_id)
        if ai_item is None:
            reviews[product_id] = {
                "risk_level": rule_level,
                "reasons": [],
                "summary": "AI review unavailable; rule checks apply.",
            }
            continue
        final_level = (
            ai_item["risk_level"]
            if _LEVELS[ai_item["risk_level"]] >= _LEVELS[rule_level]
            else rule_level
        )
        reviews[product_id] = {
            "risk_level": final_level,
            "reasons": list(ai_item["reasons"]),
            "summary": ai_item["summary"],
        }
    return reviews, ai_available


def clear_cache():
    _CACHE.clear()
