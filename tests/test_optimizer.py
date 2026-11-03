# tests/test_optimizer.py
# Acceptance tests for the grouped 0/1 knapsack basket optimizer.

import itertools
import random
from dataclasses import FrozenInstanceError

import pytest

from backend.agent.optimizer import (
    BasketCandidate,
    BasketChoice,
    BasketComparison,
    BasketResult,
    compare_with_greedy,
    greedy_basket,
    optimize_basket,
)


def candidate(name, price_cents, score):
    return BasketCandidate(item=name, price_cents=price_cents, score=score)


def brute_force_score(categories, budget_cents):
    names = list(categories)
    best_score = 0
    for picks in itertools.product(*[[None] + list(categories[name]) for name in names]):
        total_price = sum(0 if pick is None else pick.price_cents for pick in picks)
        if total_price > budget_cents:
            continue
        total_score = sum(0 if pick is None else pick.score for pick in picks)
        best_score = max(best_score, total_score)
    return best_score


def test_empty_categories_returns_an_empty_result():
    result = optimize_basket({}, 10000)
    assert result.chosen == ()
    assert result.total_price_cents == 0
    assert result.total_score == 0
    assert result.unfulfilled == ()


def test_empty_category_lists_are_unfulfilled():
    result = optimize_basket({"earbuds": [], "case": []}, 10000)
    assert result.chosen == ()
    assert result.total_price_cents == 0
    assert result.unfulfilled == ("earbuds", "case")


def test_zero_budget_cannot_buy_anything():
    categories = {
        "earbuds": [candidate("earbuds", 1, 1)],
        "case": [candidate("case", 1, 1)],
    }
    result = optimize_basket(categories, 0)
    assert result.chosen == ()
    assert result.total_price_cents == 0
    assert result.total_score == 0
    assert result.unfulfilled == ("earbuds", "case")


def test_negative_budget_is_rejected():
    with pytest.raises(ValueError):
        optimize_basket({}, -1)


def test_boolean_budget_is_rejected():
    with pytest.raises(TypeError):
        optimize_basket({}, True)


def test_optimal_beats_greedy_on_the_readme_basket():
    categories = {
        "headphones": [candidate("headphones", 6000, 9)],
        "charger": [candidate("charger", 5000, 7)],
        "case": [candidate("case", 5000, 7)],
    }

    optimal = optimize_basket(categories, 10000)
    greedy = greedy_basket(categories, 10000)
    comparison = compare_with_greedy(categories, 10000)

    assert optimal.total_score == 14
    assert optimal.total_price_cents == 10000
    assert {choice.category for choice in optimal.chosen} == {"charger", "case"}
    assert greedy.total_score == 9
    assert len(greedy.chosen) == 1
    assert greedy.chosen[0].category == "headphones"
    assert comparison.optimal == optimal
    assert comparison.greedy == greedy
    assert comparison.score_gain == 5
    assert isinstance(comparison, BasketComparison)


def test_exact_cent_budget_boundary():
    categories = {"earbuds": [candidate("earbuds", 5000, 4)]}

    exact = optimize_basket(categories, 5000)
    assert exact.total_price_cents == 5000
    assert len(exact.chosen) == 1

    one_cent_short = optimize_basket(categories, 4999)
    assert one_cent_short.chosen == ()
    assert one_cent_short.unfulfilled == ("earbuds",)


def test_exact_cent_combination():
    categories = {
        "earbuds": [candidate("earbuds", 3000, 1)],
        "case": [candidate("case", 7000, 2)],
    }
    result = optimize_basket(categories, 10000)
    assert result.total_price_cents == 10000
    assert result.total_score == 3
    assert {choice.category for choice in result.chosen} == {"earbuds", "case"}


def test_at_most_one_product_per_category():
    categories = {
        "audio": [
            candidate("cheap", 2000, 3),
            candidate("premium", 6000, 9),
        ],
        "case": [
            candidate("slim", 3000, 4),
            candidate("rugged", 5000, 6),
        ],
    }

    result = optimize_basket(categories, 9000)
    categories_used = [choice.category for choice in result.chosen]
    assert len(categories_used) == len(set(categories_used))
    assert {choice.item for choice in result.chosen} == {"premium", "slim"}
    assert result.total_score == 13


def test_invalid_options_are_skipped():
    categories = {
        "valid": [
            candidate("zero", 0, 100),
            candidate("negative", -5, 100),
            candidate("too_expensive", 20000, 100),
            candidate("good", 1000, 2),
        ],
        "empty_after_filter": [
            candidate("zero_only", 0, 100),
        ],
    }

    result = optimize_basket(categories, 5000)
    assert len(result.chosen) == 1
    assert result.chosen[0].item == "good"
    assert result.total_price_cents == 1000
    assert result.unfulfilled == ("empty_after_filter",)


def test_categories_that_do_not_fit_are_reported_in_order():
    categories = {
        "first": [candidate("first", 100, 1)],
        "second": [candidate("second", 100, 1)],
    }
    result = optimize_basket(categories, 100)
    assert len(result.chosen) == 1
    assert result.chosen[0].category == "first"
    assert result.unfulfilled == ("second",)


def test_duck_typed_candidates_are_accepted():
    class SimpleCandidate:
        def __init__(self, name, price_cents, score):
            self.name = name
            self.price_cents = price_cents
            self.score = score

    categories = {
        "one": [SimpleCandidate("one", 1000, 2)],
        "two": [SimpleCandidate("two", 2000, 5)],
    }
    result = optimize_basket(categories, 2000)
    assert result.total_score == 5
    assert result.chosen[0].item.name == "two"


def test_list_of_category_pairs_is_accepted():
    categories = [
        ("earbuds", [candidate("earbuds", 1000, 2)]),
        ("case", [candidate("case", 2000, 3)]),
    ]
    result = optimize_basket(categories, 3000)
    assert result.total_price_cents == 3000
    assert result.total_score == 5
    assert result.unfulfilled == ()


def test_duplicate_category_keys_are_rejected():
    categories = [
        ("earbuds", [candidate("one", 1000, 1)]),
        ("earbuds", [candidate("two", 1000, 2)]),
    ]
    with pytest.raises(ValueError):
        optimize_basket(categories, 5000)


def test_random_baskets_match_brute_force():
    generator = random.Random(20261103)

    for case_number in range(50):
        category_count = generator.randint(1, 4)
        categories = {}
        for category_index in range(category_count):
            name = f"category-{case_number}-{category_index}"
            option_count = generator.randint(1, 4)
            categories[name] = [
                candidate(
                    f"item-{case_number}-{category_index}-{option_index}",
                    generator.randint(1, 60),
                    generator.randint(0, 10),
                )
                for option_index in range(option_count)
            ]

        budget_cents = generator.randint(0, 120)
        result = optimize_basket(categories, budget_cents)
        expected_score = brute_force_score(categories, budget_cents)

        assert result.total_score == expected_score
        assert result.total_price_cents <= budget_cents
        used = [choice.category for choice in result.chosen]
        assert len(used) == len(set(used))
        assert set(used) | set(result.unfulfilled) == set(categories)


def test_optimal_value_is_never_below_greedy_value():
    generator = random.Random(77)
    for _ in range(30):
        categories = {
            "a": [candidate("a1", generator.randint(1, 50), generator.randint(0, 10))
                  for _ in range(3)],
            "b": [candidate("b1", generator.randint(1, 50), generator.randint(0, 10))
                  for _ in range(3)],
            "c": [candidate("c1", generator.randint(1, 50), generator.randint(0, 10))
                  for _ in range(3)],
        }
        budget = generator.randint(0, 100)
        optimal = optimize_basket(categories, budget)
        greedy = greedy_basket(categories, budget)
        assert optimal.total_score >= greedy.total_score


def test_results_are_frozen():
    result = optimize_basket({}, 0)
    with pytest.raises(FrozenInstanceError):
        result.total_score = 1

    choice = BasketChoice(category="x", item="x", price_cents=1, score=1)
    with pytest.raises(FrozenInstanceError):
        choice.price_cents = 2

    assert isinstance(result, BasketResult)
