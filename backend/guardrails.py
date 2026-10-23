# backend/guardrails.py
# RULE 1: the spending limit. Pure functions, no network, no AI.
#
# The budget is an integer number of cents. Nothing the AI writes, and nothing written in a
# product listing, can change a check in this file. Every function returns a Verdict that says
# WHY something was refused, so the action log can explain it in plain English.

from dataclasses import dataclass

from backend import config
from backend.models import Candidate, Offer, Product
from backend.money import format_usd


@dataclass(frozen=True)
class Verdict:
    ok: bool
    code: str       # 'OK' or one of the refusal codes below
    reason: str


@dataclass(frozen=True)
class Rejection:
    product_id: str
    title: str
    code: str
    reason: str


@dataclass(frozen=True)
class FilterResult:
    kept: tuple
    rejected: tuple


OK = "OK"
BAD_BUDGET = "BAD_BUDGET"
BAD_PRICE = "BAD_PRICE"
CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
OUT_OF_STOCK = "OUT_OF_STOCK"
NOT_NEW = "NOT_NEW"
OVER_BUDGET = "OVER_BUDGET"
NO_OFFER = "NO_OFFER"
OFFER_GONE = "OFFER_GONE"
PRICE_CHANGED = "PRICE_CHANGED"

# When every offer of a product fails, report the most useful reason first.
_REASON_PRIORITY = (OVER_BUDGET, OUT_OF_STOCK, NOT_NEW, CURRENCY_MISMATCH, BAD_PRICE)


def _yes(reason="ok"):
    return Verdict(True, OK, reason)


def _no(code, reason):
    return Verdict(False, code, reason)


def validate_budget_cents(budget_cents):
    """A budget must be a whole number of cents, above zero, and not above the configured maximum."""
    if not isinstance(budget_cents, int) or isinstance(budget_cents, bool):
        return _no(BAD_BUDGET, "The budget must be a whole number of cents.")
    if budget_cents <= 0:
        return _no(BAD_BUDGET, "The budget must be more than zero.")
    if budget_cents > config.MAX_BUDGET_CENTS:
        return _no(BAD_BUDGET,
                   f"The budget {format_usd(budget_cents)} is above the allowed maximum "
                   f"of {format_usd(config.MAX_BUDGET_CENTS)}.")
    return _yes()


def evaluate_offer(offer, budget_cents, shipping_cents=0):
    """Can the agent buy this exact offer within the budget? Total = price + shipping."""
    if offer.price_cents <= 0:
        return _no(BAD_PRICE, "The listed price is zero or invalid.")
    if offer.currency.upper() != config.CURRENCY:
        return _no(CURRENCY_MISMATCH,
                   f"Priced in {offer.currency}, but the agent only spends in {config.CURRENCY}.")
    if offer.availability != "InStock":
        return _no(OUT_OF_STOCK, f"Not in stock ({offer.availability or 'unknown'}).")
    if offer.condition.lower() != "new":
        return _no(NOT_NEW, f"Condition is '{offer.condition or 'not stated'}', not new.")
    total = offer.price_cents + shipping_cents
    if total > budget_cents:
        return _no(OVER_BUDGET,
                   f"{format_usd(total)} is over the {format_usd(budget_cents)} budget.")
    return _yes(f"{format_usd(total)} fits the {format_usd(budget_cents)} budget.")


def evaluate_product(product, budget_cents, shipping_cents=0):
    """Pick the cheapest offer that passes every check. Returns (Verdict, Offer or None)."""
    budget = validate_budget_cents(budget_cents)
    if not budget.ok:
        return budget, None
    if not product.offers:
        return _no(NO_OFFER, "This product has no offers."), None

    passing, failures = [], []
    for offer in product.offers:
        verdict = evaluate_offer(offer, budget_cents, shipping_cents)
        (passing if verdict.ok else failures).append((offer, verdict))

    if passing:
        offer, verdict = min(passing, key=lambda pair: pair[0].price_cents)
        return verdict, offer

    for code in _REASON_PRIORITY:
        for _, verdict in failures:
            if verdict.code == code:
                return verdict, None
    return failures[0][1], None


def filter_within_budget(products, budget_cents, shipping_cents=0):
    """Split products into those the agent may consider and those it must drop (with reasons)."""
    kept, rejected = [], []
    for product in products:
        verdict, offer = evaluate_product(product, budget_cents, shipping_cents)
        if verdict.ok:
            kept.append(Candidate(product=product, offer=offer))
        else:
            rejected.append(Rejection(product.id, product.title, verdict.code, verdict.reason))
    return FilterResult(tuple(kept), tuple(rejected))


def verify_live_offer(approved_offer, live_product, budget_cents, shipping_cents=0):
    """The last check before any payment. Compare what the person approved with the LIVE listing.

    Refuses if the offer vanished, if it no longer passes the checks (including the budget),
    or if the price moved at all: the approval was for an exact amount, so a changed price
    needs a fresh approval.
    """
    budget = validate_budget_cents(budget_cents)
    if not budget.ok:
        return budget
    live_offer = next((o for o in live_product.offers
                       if o.domain.lower() == approved_offer.domain.lower()), None)
    if live_offer is None:
        return _no(OFFER_GONE, f"{approved_offer.domain} no longer lists this product.")
    verdict = evaluate_offer(live_offer, budget_cents, shipping_cents)
    if not verdict.ok:
        return verdict
    if live_offer.price_cents != approved_offer.price_cents:
        return _no(PRICE_CHANGED,
                   f"The price changed from {format_usd(approved_offer.price_cents)} to "
                   f"{format_usd(live_offer.price_cents)} since you approved. Please review and approve again.")
    return _yes("Live price matches the approved price.")
