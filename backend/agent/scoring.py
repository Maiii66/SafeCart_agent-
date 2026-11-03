# backend/agent/scoring.py
# RULE: rank products with simple, explainable maths. Nothing here spends money.
# Money stays in integer cents. Floats are used only for statistics and scores.

import re
import statistics

WEIGHT_RELEVANCE = 0.35
WEIGHT_RATING = 0.40
WEIGHT_VALUE = 0.25
DEFAULT_PRIOR_MEAN = 4.0
PRIOR_WEIGHT = 50
OUTLIER_FRACTION_PERCENT = 40
OUTLIER_Z = 3.5
MIN_FOR_OUTLIERS = 3
OUTLIER_VALUE_CAP = 0.5

_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "of", "in", "to",
    "me", "my", "best", "good", "cheap", "new",
}
_WORD = re.compile(r"[a-z0-9]+")


def bayesian_average(rating, count, prior_mean, prior_weight=PRIOR_WEIGHT):
    """A rating pulled towards the prior when it has few reviews."""
    if rating is None or count is None or count <= 0:
        return prior_mean
    return (count * rating + prior_weight * prior_mean) / (count + prior_weight)


def prior_mean_of(products):
    """The mean rating of the products that actually have reviews."""
    ratings = [
        product.rating_value
        for product in products
        if product.rating_value is not None and (product.rating_count or 0) > 0
    ]
    if not ratings:
        return DEFAULT_PRIOR_MEAN
    return sum(ratings) / len(ratings)


def price_outlier_ids(candidates):
    """Product ids whose price is far below the rest of the candidates.

    This compares prices with each other and calculates no amount of money, so
    floats are fine here.
    """
    if len(candidates) < MIN_FOR_OUTLIERS:
        return set()

    prices = [candidate.offer.price_cents for candidate in candidates]
    median = statistics.median(prices)
    mad = statistics.median([abs(price - median) for price in prices])

    flagged = set()
    for candidate in candidates:
        price = candidate.offer.price_cents
        # Below 40 percent of the median is suspicious on its own.
        if price * 100 < median * OUTLIER_FRACTION_PERCENT:
            flagged.add(candidate.product.id)
        elif mad > 0 and 0.6745 * (price - median) / mad < -OUTLIER_Z:
            flagged.add(candidate.product.id)
    return flagged


def _words(text):
    """Lowercase letter/digit words, without stopwords and with a simple singular form."""
    if not isinstance(text, str):
        return set()
    words = set()
    for word in _WORD.findall(text.lower()):
        if word in _STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        words.add(word)
    return words


def relevance(query, product):
    """How much of what the person asked for appears in the product's own text."""
    wanted = _words(query)
    if not wanted:
        return 0.0

    features = " ".join(product.key_features or ())
    text = " ".join([
        product.title or "",
        product.brand or "",
        product.category or "",
        product.description or "",
        features,
    ])
    found = wanted & _words(text)
    return len(found) / len(wanted)


def _clamp(value, low, high):
    return max(low, min(high, value))


def score_candidate(candidate, query, budget_cents, prior_mean, is_outlier=False):
    """Return (score, breakdown). The breakdown explains every part of the score."""
    product, offer = candidate.product, candidate.offer

    rating_average = bayesian_average(
        product.rating_value, product.rating_count, prior_mean
    )
    rating_part = _clamp((rating_average - 1) / 4, 0.0, 1.0)

    # Integer division keeps the price comparison exact in cents.
    if budget_cents <= 0:
        value_permille = 0
    else:
        value_permille = (budget_cents - offer.price_cents) * 1000 // budget_cents
    value_part = _clamp(value_permille, 0, 1000) / 1000
    value_capped = False
    if is_outlier and value_part > OUTLIER_VALUE_CAP:
        value_part = OUTLIER_VALUE_CAP
        value_capped = True

    relevance_part = relevance(query, product)
    score = round(
        WEIGHT_RELEVANCE * relevance_part
        + WEIGHT_RATING * rating_part
        + WEIGHT_VALUE * value_part,
        4,
    )
    breakdown = {
        "relevance": round(relevance_part, 3),
        "rating_average": round(rating_average, 3),
        "rating_part": round(rating_part, 3),
        "value_part": round(value_part, 3),
        "value_capped": value_capped,
        "weights": {
            "relevance": round(WEIGHT_RELEVANCE, 3),
            "rating": round(WEIGHT_RATING, 3),
            "value": round(WEIGHT_VALUE, 3),
        },
    }
    return score, breakdown
