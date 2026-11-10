# backend/agent/explain.py
# Explanations are suggestions too. A small template always works, and a Gemini
# sentence is used only if it passes validation. Nothing here can spend money.

import time

from backend.agent import injection
from backend.money import format_usd
from backend.services import llm

MAX_EXPLANATION_CHARS = 400


def _title(value):
    return injection.sanitize_for_llm(value, 120) or "Untitled listing"


def _domain(value):
    return injection.sanitize_for_llm(value, 120) or "Unknown merchant"


def _percent(value, fallback=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:
        return fallback
    return max(0.0, min(1.0, number))


def _ranked_pick(item):
    candidate = getattr(item, "candidate", None)
    product = getattr(candidate, "product", None)
    offer = getattr(candidate, "offer", None)
    if product is None or offer is None:
        return None
    return {
        "title": _title(product.title),
        "merchant": _domain(offer.domain),
        "price_cents": offer.price_cents,
        "score": getattr(item, "score", 0.0),
        "rating": getattr(product, "rating_value", None),
        "rating_count": getattr(product, "rating_count", 0),
        "breakdown": getattr(item, "breakdown", {}) or {},
        "flags": list(getattr(item, "flags", ()) or ()),
    }


def _choice_pick(choice):
    ranked = getattr(choice, "item", None)
    data = _ranked_pick(ranked)
    if data is None:
        return None
    data["price_cents"] = getattr(choice, "price_cents", data["price_cents"])
    data["score"] = getattr(choice, "score", data["score"])
    return data


def _score_words(pick):
    breakdown = pick.get("breakdown") or {}
    relevance = _percent(breakdown.get("relevance"))
    rating = _percent(breakdown.get("rating_part"))
    value = _percent(breakdown.get("value_part"))
    return (
        f"relevance {relevance:.0%}, rating {rating:.0%}, and value {value:.0%}"
    )


def template_explanation(request_summary, ranked):
    picks = [pick for pick in (_ranked_pick(item) for item in ranked or ()) if pick]
    if not picks:
        return "No safe listings matched that request and budget."

    top = picks[0]
    text = (
        f"{top['title']} from {top['merchant']} is the best match at "
        f"{format_usd(top['price_cents'])}. It scored on {_score_words(top)}."
    )
    if len(picks) > 1:
        second = picks[1]
        text += (
            f" Next is {second['title']} from {second['merchant']} at "
            f"{format_usd(second['price_cents'])}."
        )
    if top.get("flags"):
        text += " One price looked unusual, so it was treated with extra care."
    return text[:MAX_EXPLANATION_CHARS]


def template_basket_explanation(choices, total_cents, budget_cents):
    picks = [pick for pick in (_choice_pick(choice) for choice in choices or ()) if pick]
    if not picks:
        return "No safe basket fit that total budget."

    parts = [
        f"{pick['title']} at {format_usd(pick['price_cents'])}"
        for pick in picks
    ]
    return (
        "Best basket: " + ", ".join(parts) + f". Total {format_usd(total_cents)} "
        f"of a {format_usd(budget_cents)} budget."
    )[:MAX_EXPLANATION_CHARS]


def _validate_ai_text(text):
    if not isinstance(text, str):
        return None
    cleaned = injection.sanitize_for_llm(text, MAX_EXPLANATION_CHARS)
    if len(cleaned) < 8:
        return None
    if injection.scan_text(cleaned).flagged:
        return None
    return cleaned


def _finish(request_summary, picks, template, deadline=None):
    if not picks or not llm.available():
        return template, "template"
    if deadline is not None and deadline - time.monotonic() <= 8:
        return template, "template"
    ai_text = _validate_ai_text(llm.explain_picks(request_summary, picks))
    if ai_text is None:
        return template, "template"
    return ai_text, "gemini"


def explain(request_summary, ranked, deadline=None):
    """Return (text, source) for a ranked result list."""
    picks = [pick for pick in (_ranked_pick(item) for item in ranked or ()) if pick][:3]
    template = template_explanation(request_summary, ranked)
    return _finish(request_summary, picks, template, deadline)


def explain_basket(request_summary, choices, total_cents, budget_cents, deadline=None):
    """Return (text, source) for an optimal basket."""
    picks = [pick for pick in (_choice_pick(choice) for choice in choices or ()) if pick][:5]
    template = template_basket_explanation(choices, total_cents, budget_cents)
    return _finish(request_summary, picks, template, deadline)
