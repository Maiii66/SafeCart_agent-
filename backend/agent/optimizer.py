# backend/agent/optimizer.py
# Grouped 0/1 knapsack: at most one product per category, inside a budget in cents.

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class BasketCandidate:
    """A possible product for a category, with its price and value already known."""
    item: object
    price_cents: int
    score: float


@dataclass(frozen=True)
class BasketChoice:
    """One chosen product, keeping the category it came from."""
    category: str
    item: object
    price_cents: int
    score: float


@dataclass(frozen=True)
class BasketResult:
    """The best basket found, plus any categories that could not be filled."""
    chosen: tuple
    total_price_cents: int
    total_score: float
    unfulfilled: tuple


@dataclass(frozen=True)
class BasketComparison:
    """Optimal and greedy results side by side for a debug or display panel."""
    optimal: BasketResult
    greedy: BasketResult
    score_gain: float
    price_difference_cents: int


def _pairs(categories_candidates):
    """Accept a mapping, or a list of (category, candidates) pairs."""
    if isinstance(categories_candidates, Mapping):
        return list(categories_candidates.items())
    if isinstance(categories_candidates, (list, tuple)):
        pairs = []
        for entry in categories_candidates:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise TypeError("Expected (category, candidates) pairs.")
            pairs.append((entry[0], entry[1]))
        return pairs
    raise TypeError("Expected a mapping or a list of (category, candidates) pairs.")


def _candidate_values(option):
    """Read price and score from our dataclass, a dictionary, or a duck-typed object."""
    price = getattr(option, "price_cents", None)
    score = getattr(option, "score", None)

    if isinstance(option, Mapping):
        if price is None:
            price = option.get("price_cents")
        if score is None:
            score = option.get("score", option.get("value"))

    if price is None:
        offer = getattr(option, "offer", None)
        if offer is None:
            inner = getattr(option, "candidate", None)
            offer = getattr(inner, "offer", None)
        price = getattr(offer, "price_cents", None)

    return price, score


def _valid_values(option):
    """Return (price_cents, score) for a usable option, or None to skip it."""
    price, score = _candidate_values(option)
    if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
        return None
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    if score != score:
        return None
    return price, float(score)


def _normalise(categories_candidates, budget_cents):
    """Validate the budget and keep only usable options that fit on their own."""
    if isinstance(budget_cents, bool) or not isinstance(budget_cents, int):
        raise TypeError("budget_cents must be an integer number of cents.")
    if budget_cents < 0:
        raise ValueError("budget_cents cannot be negative.")

    categories = []
    seen = set()
    for category, candidates in _pairs(categories_candidates):
        if not isinstance(category, str):
            raise TypeError("Each category must be a string.")
        if category in seen:
            raise ValueError(f"Duplicate category: {category}")
        seen.add(category)

        if candidates is None:
            candidates = ()
        try:
            options = list(candidates)
        except TypeError:
            raise TypeError(f"Candidates for {category} must be a sequence.") from None

        usable = []
        for option in options:
            values = _valid_values(option)
            if values is None:
                continue
            price, score = values
            if price <= budget_cents:
                usable.append((option, price, score))
        categories.append((category, tuple(usable)))
    return categories


def _choice(category, option, price, score):
    return BasketChoice(
        category=category,
        item=getattr(option, "item", option),
        price_cents=price,
        score=score,
    )


def optimize_basket(categories_candidates, budget_cents):
    """Return the highest-scoring basket with at most one product per category."""
    categories = _normalise(categories_candidates, budget_cents)

    if not categories or budget_cents == 0:
        return BasketResult(
            chosen=(),
            total_price_cents=0,
            total_score=0.0,
            unfulfilled=tuple(name for name, _options in categories),
        )

    # states maps total spend -> (total score, chosen items).
    states = {0: (0.0, ())}
    for category, options in categories:
        if not options:
            continue

        # Keeping a copy of states is the "skip this category" choice.
        next_states = dict(states)
        for spent, (score, choices) in states.items():
            for option, price, value in options:
                new_spent = spent + price
                if new_spent > budget_cents:
                    continue
                new_score = score + value
                previous = next_states.get(new_spent)
                if previous is None or new_score > previous[0]:
                    next_states[new_spent] = (
                        new_score,
                        choices + (_choice(category, option, price, value),),
                    )
        states = next_states

    # Highest score first; on a tie, spend fewer cents.
    best_spent = min(states, key=lambda spent: (-states[spent][0], spent))
    best_score, best_choices = states[best_spent]
    used = {choice.category for choice in best_choices}
    unfulfilled = tuple(name for name, _options in categories if name not in used)

    return BasketResult(
        chosen=best_choices,
        total_price_cents=best_spent,
        total_score=best_score,
        unfulfilled=unfulfilled,
    )


def greedy_basket(categories_candidates, budget_cents):
    """A simple comparison baseline: take the best-scoring item that still fits."""
    categories = _normalise(categories_candidates, budget_cents)

    flattened = []
    for category, options in categories:
        for index, (option, price, score) in enumerate(options):
            flattened.append((category, index, option, price, score))

    flattened.sort(key=lambda row: (-row[4], row[3], row[0], row[1]))

    remaining = budget_cents
    used = set()
    chosen = []
    for category, _index, option, price, score in flattened:
        if category in used or price > remaining:
            continue
        used.add(category)
        remaining -= price
        chosen.append(_choice(category, option, price, score))

    unfulfilled = tuple(name for name, _options in categories if name not in used)
    return BasketResult(
        chosen=tuple(chosen),
        total_price_cents=budget_cents - remaining,
        total_score=sum(choice.score for choice in chosen),
        unfulfilled=unfulfilled,
    )


def compare_with_greedy(categories_candidates, budget_cents):
    """Run both algorithms so a screen can show why the optimal basket is better."""
    optimal = optimize_basket(categories_candidates, budget_cents)
    greedy = greedy_basket(categories_candidates, budget_cents)
    return BasketComparison(
        optimal=optimal,
        greedy=greedy,
        score_gain=round(optimal.total_score - greedy.total_score, 10),
        price_difference_cents=optimal.total_price_cents - greedy.total_price_cents,
    )
