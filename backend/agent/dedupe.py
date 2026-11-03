# backend/agent/dedupe.py
# RULE: the same product sold by several shops is one choice, not several.
# Two listings are the same product only when their titles are close AND their model
# numbers match exactly. The model-number check is what keeps "WF-1000XM4" and
# "WF-1000XM5" apart even though their titles are almost identical.

import re

SIMILARITY = 0.8
_FILLER = {"true", "the", "with", "for", "and", "new", "a", "an", "of", "by"}
_WORD = re.compile(r"[a-z0-9]+")


def title_tokens(title):
    """Lowercase word tokens, without the filler words that carry no product meaning."""
    if not isinstance(title, str):
        return set()
    return {word for word in _WORD.findall(title.lower()) if word not in _FILLER}


def _brand(product):
    return str(getattr(product, "brand", "") or "").strip()


def _model_numbers(tokens):
    return {token for token in tokens if any(ch.isdigit() for ch in token)}


def are_duplicates(first, second):
    """True only when both titles, brands and model numbers agree strongly enough."""
    first_tokens = title_tokens(first.title)
    second_tokens = title_tokens(second.title)
    if not first_tokens or not second_tokens:
        return False

    first_brand = _brand(first)
    second_brand = _brand(second)
    # An unknown brand matches anything; two known brands must agree.
    if first_brand and second_brand and first_brand.lower() != second_brand.lower():
        return False

    if _model_numbers(first_tokens) != _model_numbers(second_tokens):
        return False

    overlap = len(first_tokens & second_tokens) / len(first_tokens | second_tokens)
    return overlap >= SIMILARITY


def dedupe_candidates(candidates):
    """Group duplicates and keep one candidate per group.

    Returns (kept, dropped). Kept follows the order in which each group first
    appeared; dropped holds (dropped_candidate, kept_candidate) pairs.
    """
    groups = []
    for candidate in candidates:
        for group in groups:
            # Compare with the first member of the group, as the group is stable.
            if are_duplicates(candidate.product, group[0].product):
                group.append(candidate)
                break
        else:
            groups.append([candidate])

    kept, dropped = [], []
    for group in groups:
        # Cheapest offer wins; then more reviews; then the lower product id.
        best = min(group, key=lambda item: (
            item.offer.price_cents,
            -item.product.rating_count,
            item.product.id,
        ))
        kept.append(best)
        for member in group:
            if member is not best:
                dropped.append((member, best))
    return kept, dropped
