# backend/agent/shopper.py
# The search pipeline: untrusted text is scanned, money rules are applied, duplicate
# listings are merged, and the rest is ranked. No payment can happen from this file.

import time
from dataclasses import dataclass

from backend import guardrails
from backend.agent import dedupe, guardian, injection, optimizer, policy, scoring
from backend.models import Candidate
from backend.services import catalog
from database import repository

SEARCH_TOTAL_BUDGET_SECONDS = 45


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    score: float
    breakdown: dict
    flags: tuple


@dataclass(frozen=True)
class SearchOutcome:
    source: str
    note: str
    ranked: tuple
    blocked: tuple
    rejected: tuple
    duplicates: tuple
    guardian_reviews: dict = None
    guardian_available: bool = True


@dataclass(frozen=True)
class BasketItemOutcome:
    query: str
    item: object
    note: str = ""


@dataclass(frozen=True)
class BasketOutcome:
    source: str
    note: str
    items: tuple
    optimal: object
    greedy: object
    comparison: object
    total_budget_cents: int
    total_cents: int
    remaining_cents: int
    guardian_reviews: dict = None
    guardian_available: bool = True


def _is_positive_budget(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def search_and_rank(query, budget_cents, limit=10, now=None, deadline=None):
    """Search, enforce every check, merge duplicates, and rank what is left."""
    now = time.time() if now is None else now
    fetch = min(max(limit * 3, 10), 50)
    active_policy = policy.get_policy()
    search_budget = policy.effective_search_budget(budget_cents, active_policy)
    max_price = search_budget if _is_positive_budget(search_budget) else None
    found = catalog.search_products(query, max_price_cents=max_price, limit=fetch)

    # Product text is untrusted. Blocked listings are logged with a clean title.
    safe, blocked = injection.split_unsafe(found.products)
    for product, reasons in blocked:
        title = injection.sanitize_for_llm(product.title, 80)
        repository.add_log(
            "guardrail",
            "blocked_injection",
            f"{title}: {', '.join(reasons)}",
            product.id,
        )

    filtered = guardrails.filter_within_budget(safe, search_budget)
    kept, duplicates = dedupe.dedupe_candidates(list(filtered.kept))
    kept, policy_rejected = policy.filter_candidates(kept, active_policy)

    outliers = scoring.price_outlier_ids(kept)
    prior = scoring.prior_mean_of([item.product for item in kept])
    rule_flags = {
        candidate.product.id: ("suspicious_price",)
        for candidate in kept
        if candidate.product.id in outliers
    }
    guardian_timeout_ms = None
    if deadline is not None:
        remaining = deadline - time.monotonic()
        guardian_timeout_ms = max(1, int(remaining * 1000))
    guardian_reviews, guardian_available = guardian.review(
        kept, rule_flags, timeout_ms=guardian_timeout_ms
    )

    ranked = []
    for candidate in kept:
        is_outlier = candidate.product.id in outliers
        score, breakdown = scoring.score_candidate(
            candidate, query, search_budget, prior, is_outlier=is_outlier
        )
        flags = ("suspicious_price",) if is_outlier else ()
        ranked.append(RankedCandidate(candidate, score, breakdown, flags))

    # Flagged products always go last, then the best score first.
    ranked.sort(key=lambda item: (
        bool(item.flags),
        -item.score,
        item.candidate.offer.price_cents,
        item.candidate.product.id,
    ))

    # Remember the flags for every ranked product, not just the shown ones.
    for item in ranked:
        repository.save_product_flags(
            item.candidate.product.id, found.source, list(item.flags), int(now)
        )

    query_text = injection.sanitize_for_llm(query, 60)
    detail = (
        f"'{query_text}': {len(ranked)} shown, {len(blocked)} blocked by the injection guard, "
        f"{len(filtered.rejected)} over budget or unavailable, "
        f"{len(policy_rejected)} rejected by your spending policy, {len(duplicates)} duplicates"
    )
    repository.add_log("agent", "search", detail, None)

    return SearchOutcome(
        source=found.source,
        note=found.note,
        ranked=tuple(ranked[:limit]),
        blocked=tuple(blocked),
        rejected=tuple(filtered.rejected) + tuple(policy_rejected),
        duplicates=tuple(duplicates),
        guardian_reviews=guardian_reviews,
        guardian_available=guardian_available,
    )


def _clean_queries(items):
    if isinstance(items, str):
        items = [items]
    try:
        raw_items = list(items)
    except TypeError:
        raise TypeError("items must be a sequence of search strings.") from None

    queries = []
    seen = set()
    for item in raw_items:
        if not isinstance(item, str):
            raise TypeError("Each basket item must be a string.")
        query = item.strip()
        if not query:
            continue
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        queries.append(query)

    if not queries:
        raise ValueError("At least one basket item is required.")
    if len(queries) > 5:
        raise ValueError("A basket can contain at most five items.")
    return queries


def search_basket(items, total_budget_cents, limit=10, now=None):
    """Search every item through the safe pipeline, then choose the best combination."""
    budget = guardrails.validate_budget_cents(total_budget_cents)
    if not budget.ok:
        raise ValueError(budget.reason)

    queries = _clean_queries(items)
    now = time.time() if now is None else now
    deadline = time.monotonic() + SEARCH_TOTAL_BUDGET_SECONDS
    outcomes = [
        search_and_rank(
            query,
            total_budget_cents,
            limit=limit,
            now=now,
            deadline=deadline,
        )
        for query in queries
    ]

    sources = {outcome.source for outcome in outcomes}
    mixed_sources = len(sources) != 1
    source = "" if mixed_sources else next(iter(sources))

    notes = []
    guardian_reviews = {}
    guardian_available = True
    for outcome in outcomes:
        if outcome.note and outcome.note not in notes:
            notes.append(outcome.note)
        guardian_reviews.update(outcome.guardian_reviews or {})
        guardian_available = guardian_available and outcome.guardian_available
    if mixed_sources:
        notes.append(
            "Items came from different catalog sources, so they cannot be bought together."
        )

    categories = []
    for query, outcome in zip(queries, outcomes):
        options = [
            optimizer.BasketCandidate(
                item=ranked,
                price_cents=ranked.candidate.offer.price_cents,
                score=ranked.score,
            )
            for ranked in outcome.ranked
        ]
        categories.append((query, options))

    comparison = optimizer.compare_with_greedy(categories, total_budget_cents)
    chosen = {choice.category: choice.item for choice in comparison.optimal.chosen}

    product_ids = [
        choice.item.candidate.product.id
        for choice in comparison.optimal.chosen
        if getattr(getattr(choice.item, "candidate", None), "product", None) is not None
    ]
    if len(product_ids) != len(set(product_ids)):
        source = ""
        notes.append(
            "The same product matched more than one item, so this basket cannot be bought together."
        )

    item_results = []
    for query, outcome in zip(queries, outcomes):
        item = chosen.get(query)
        item_note = ""
        if not outcome.ranked:
            item_note = f"No safe listings matched '{query}'."
        elif item is None and query in comparison.optimal.unfulfilled:
            item_note = f"'{query}' did not fit the total budget."
        item_results.append(BasketItemOutcome(query=query, item=item, note=item_note))

    if not any(outcome.ranked for outcome in outcomes):
        notes.append("No products matched that basket.")
    elif not comparison.optimal.chosen:
        notes.append("Nothing fits the total budget. Try fewer items or a higher budget.")

    total_cents = comparison.optimal.total_price_cents
    return BasketOutcome(
        source=source,
        note=" ".join(notes),
        items=tuple(item_results),
        optimal=comparison.optimal,
        greedy=comparison.greedy,
        comparison=comparison,
        total_budget_cents=total_budget_cents,
        total_cents=total_cents,
        remaining_cents=max(0, total_budget_cents - total_cents),
        guardian_reviews=guardian_reviews,
        guardian_available=guardian_available,
    )
