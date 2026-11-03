# tests/test_scoring.py
import ast
from dataclasses import fields
from pathlib import Path

import pytest

from backend.agent import scoring
from backend.models import Candidate, Offer, Product

SOURCE = Path(scoring.__file__).read_text(encoding="utf-8")


def product(
    pid="p1",
    title="Wave-80 True Wireless Earbuds",
    brand="",
    category="",
    description="Bluetooth 5.2 noise cancelling",
    rating=4.0,
    count=100,
    features=(),
):
    return Product(
        id=pid,
        title=title,
        description=description,
        brand=brand,
        image_url="",
        category=category,
        rating_value=rating,
        rating_count=count,
        key_features=tuple(features),
        offers=(),
    )


def offer(price_cents, domain="shop.example"):
    return Offer(
        domain=domain,
        price_cents=price_cents,
        currency="USD",
        availability="InStock",
        condition="new",
        url="",
    )


def candidate(pid="p1", price_cents=2500, **kwargs):
    return Candidate(product=product(pid=pid, **kwargs), offer=offer(price_cents))


def candidates_for(prices):
    return [candidate(pid=f"p{index}", price_cents=price) for index, price in enumerate(prices)]


# ---------------- bayesian_average ----------------

def test_more_reviews_beat_a_slightly_higher_average():
    strong = scoring.bayesian_average(4.85, 813, 4.0)
    weak = scoring.bayesian_average(4.8, 17, 4.0)
    assert strong > weak


def test_no_rating_gives_exactly_the_prior():
    assert scoring.bayesian_average(None, 100, 4.0) == 4.0


@pytest.mark.parametrize("count", [None, 0, -5])
def test_no_reviews_gives_exactly_the_prior(count):
    assert scoring.bayesian_average(4.5, count, 4.0) == 4.0


def test_many_reviews_are_about_the_rating():
    assert abs(scoring.bayesian_average(4.8, 100000, 4.0) - 4.8) < 0.01


def test_one_review_is_about_the_prior():
    assert abs(scoring.bayesian_average(5.0, 1, 4.0) - 4.0) < 0.1


# ---------------- prior_mean_of ----------------

def test_prior_mean_uses_only_rated_products():
    products = [
        product("a", rating=5.0, count=10),
        product("b", rating=3.0, count=20),
        product("c", rating=None, count=50),
        product("d", rating=4.0, count=0),
    ]
    assert scoring.prior_mean_of(products) == pytest.approx(4.0)


def test_prior_mean_defaults_when_nothing_is_rated():
    assert scoring.prior_mean_of([product("a", rating=None, count=0)]) == scoring.DEFAULT_PRIOR_MEAN
    assert scoring.prior_mean_of([]) == scoring.DEFAULT_PRIOR_MEAN


# ---------------- price_outlier_ids ----------------

def _outlier_ids(prices):
    return scoring.price_outlier_ids(candidates_for(prices))


def test_only_the_suspiciously_cheap_product_is_flagged():
    assert _outlier_ids([3999, 2450, 1499, 399]) == {"p3"}


def test_normal_price_ranges_flag_nothing():
    assert _outlier_ids([1499, 1999, 2499, 2999, 3499]) == set()


def test_four_equal_prices_flag_nothing():
    assert _outlier_ids([2000, 2000, 2000, 2000]) == set()


def test_fewer_than_three_candidates_flag_nothing():
    assert _outlier_ids([100, 5000]) == set()
    assert _outlier_ids([]) == set()


def test_two_expensive_and_one_tiny_price_flags_the_tiny_one():
    assert _outlier_ids([3000, 3000, 100]) == {"p2"}


def test_exactly_40_percent_is_allowed_but_399_is_flagged():
    assert _outlier_ids([1000, 1000, 1000, 400]) == set()
    assert _outlier_ids([1000, 1000, 1000, 399]) == {"p3"}


def test_mad_rule_flags_a_moderate_outlier():
    assert _outlier_ids([5000, 5010, 5020, 5030, 5040, 3000]) == {"p5"}


# ---------------- relevance ----------------

def test_relevance_matches_the_title():
    item = product()
    assert scoring.relevance("wireless earbuds", item) == 1.0


def test_relevance_matches_the_description_too():
    item = product()
    assert scoring.relevance("wireless earbuds bluetooth", item) == 1.0


def test_relevance_is_a_fraction_of_the_wanted_words():
    assert scoring.relevance("wireless charger", product()) == 0.5


def test_relevance_is_zero_for_unrelated_words():
    assert scoring.relevance("laptop", product()) == 0.0


def test_relevance_is_case_insensitive_and_handles_plurals():
    assert scoring.relevance("EARBUD", product()) == 1.0


def test_relevance_ignores_stopwords():
    assert scoring.relevance("the best cheap earbuds for me", product()) == 1.0


@pytest.mark.parametrize("query", ["", "   ", "the best for me", "a an of"])
def test_empty_or_all_stopword_queries_score_zero(query):
    assert scoring.relevance(query, product()) == 0.0


# ---------------- score_candidate ----------------

def test_weights_add_to_one():
    total = scoring.WEIGHT_RELEVANCE + scoring.WEIGHT_RATING + scoring.WEIGHT_VALUE
    assert total == pytest.approx(1.0)


@pytest.mark.parametrize(
    "item,budget",
    [
        (candidate(rating=5.0, count=1000000, price_cents=0), 5000),
        (candidate(rating=None, count=0, price_cents=100000), 5000),
        (candidate(rating=1.0, count=1, price_cents=5000), 5000),
        (candidate(price_cents=1000), 0),
    ],
)
def test_scores_stay_between_zero_and_one(item, budget):
    score, _ = scoring.score_candidate(item, "wireless earbuds", budget, 4.0)
    assert 0.0 <= score <= 1.0


def test_better_rating_scores_higher():
    better = candidate("a", rating=4.8, count=500)
    worse = candidate("b", rating=3.5, count=500)
    assert scoring.score_candidate(better, "wireless earbuds", 5000, 4.0)[0] > \
        scoring.score_candidate(worse, "wireless earbuds", 5000, 4.0)[0]


def test_lower_price_scores_higher():
    cheaper = candidate("a", price_cents=1500)
    pricier = candidate("b", price_cents=3000)
    assert scoring.score_candidate(cheaper, "wireless earbuds", 5000, 4.0)[0] > \
        scoring.score_candidate(pricier, "wireless earbuds", 5000, 4.0)[0]


def test_matching_title_scores_higher():
    matching = candidate("a", title="Wave-80 True Wireless Earbuds")
    unrelated = candidate("b", title="Pulse Mini Charger")
    assert scoring.score_candidate(matching, "wireless earbuds", 5000, 4.0)[0] > \
        scoring.score_candidate(unrelated, "wireless earbuds", 5000, 4.0)[0]


def test_breakdown_has_six_keys_and_rebuilds_the_score():
    score, breakdown = scoring.score_candidate(
        candidate(price_cents=1500), "wireless earbuds", 5000, 4.0
    )
    assert set(breakdown) == {
        "relevance",
        "rating_average",
        "rating_part",
        "value_part",
        "value_capped",
        "weights",
    }
    weights = breakdown["weights"]
    rebuilt = (
        breakdown["relevance"] * weights["relevance"]
        + breakdown["rating_part"] * weights["rating"]
        + breakdown["value_part"] * weights["value"]
    )
    assert abs(rebuilt - score) <= 0.002


def test_value_part_for_1500_on_a_5000_budget_is_07():
    _, breakdown = scoring.score_candidate(
        candidate(price_cents=1500), "wireless earbuds", 5000, 4.0
    )
    assert breakdown["value_part"] == 0.7


def test_outlier_price_is_capped_true_and_scores_lower():
    flagged_score, flagged = scoring.score_candidate(
        candidate(price_cents=399), "wireless earbuds", 5000, 4.0, is_outlier=True
    )
    plain_score, plain = scoring.score_candidate(
        candidate(price_cents=399), "wireless earbuds", 5000, 4.0, is_outlier=False
    )
    assert flagged["value_capped"] is True
    assert flagged["value_part"] == 0.5
    assert flagged_score < plain_score
    assert plain["value_capped"] is False


def test_price_below_the_cap_is_not_marked_capped():
    _, breakdown = scoring.score_candidate(
        candidate(price_cents=3000), "wireless earbuds", 5000, 4.0, is_outlier=True
    )
    assert breakdown["value_part"] == 0.4
    assert breakdown["value_capped"] is False


def test_score_is_deterministic():
    item = candidate(price_cents=1999)
    assert scoring.score_candidate(item, "wireless earbuds", 5000, 4.0) == \
        scoring.score_candidate(item, "wireless earbuds", 5000, 4.0)


def test_scoring_source_uses_integer_division_for_value():
    assert "// budget_cents" in SOURCE


# ---------------- no marketplace payout data in the models ----------------

def test_offer_and_product_have_no_payout_field_or_name():
    def names_in(tree):
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.alias):
                names.add(node.name)
        return names

    assert "commission" not in names_in(ast.parse(SOURCE))
    assert "commission" not in {field.name for field in fields(Offer)}
    assert "commission" not in {field.name for field in fields(Product)}
