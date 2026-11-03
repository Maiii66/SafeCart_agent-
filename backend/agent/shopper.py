# backend/agent/shopper.py
# The search pipeline: untrusted text is scanned, money rules are applied, duplicate
# listings are merged, and the rest is ranked. No payment can happen from this file.

import time
from dataclasses import dataclass

from backend import guardrails
from backend.agent import dedupe, injection, scoring
from backend.models import Candidate
from backend.services import catalog
from database import repository


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


def _is_positive_budget(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def search_and_rank(query, budget_cents, limit=10, now=None):
    """Search, enforce every check, merge duplicates, and rank what is left."""
    now = time.time() if now is None else now
    fetch = min(max(limit * 3, 10), 50)
    max_price = budget_cents if _is_positive_budget(budget_cents) else None
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

    filtered = guardrails.filter_within_budget(safe, budget_cents)
    kept, duplicates = dedupe.dedupe_candidates(list(filtered.kept))

    outliers = scoring.price_outlier_ids(kept)
    prior = scoring.prior_mean_of([item.product for item in kept])

    ranked = []
    for candidate in kept:
        is_outlier = candidate.product.id in outliers
        score, breakdown = scoring.score_candidate(
            candidate, query, budget_cents, prior, is_outlier=is_outlier
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
        f"{len(filtered.rejected)} over budget or unavailable, {len(duplicates)} duplicates"
    )
    repository.add_log("agent", "search", detail, None)

    return SearchOutcome(
        source=found.source,
        note=found.note,
        ranked=tuple(ranked[:limit]),
        blocked=tuple(blocked),
        rejected=tuple(filtered.rejected),
        duplicates=tuple(duplicates),
    )
