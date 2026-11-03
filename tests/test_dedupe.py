# tests/test_dedupe.py
import pytest

from backend.agent import dedupe
from backend.models import Candidate, Offer, Product


def product(pid, title, brand="", rating_count=0, rating_value=None):
    return Product(
        id=pid,
        title=title,
        description="",
        brand=brand,
        image_url="",
        category="",
        rating_value=rating_value,
        rating_count=rating_count,
        key_features=(),
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


def candidate(pid, title, price_cents, brand="", rating_count=0):
    return Candidate(
        product=product(pid, title, brand, rating_count=rating_count),
        offer=offer(price_cents),
    )


# ---------------- title_tokens ----------------

def test_title_tokens_drops_filler_and_punctuation():
    assert dedupe.title_tokens("Wave-80 True Wireless Earbuds") == {
        "wave", "80", "wireless", "earbuds",
    }


def test_title_tokens_ignores_case_and_filler_words():
    assert dedupe.title_tokens("THE New Wireless FOR an A of BY with AND") == {
        "wireless",
    }


@pytest.mark.parametrize("value", [None, "", "   ", "!!! --- ???"])
def test_title_tokens_empty_inputs(value):
    assert dedupe.title_tokens(value) == set()


# ---------------- are_duplicates: same product ----------------

@pytest.mark.parametrize(
    "first,second",
    [
        ("Wave-80 True Wireless Earbuds", "Wave-80 True Wireless Earbuds"),
        ("wireless earbuds", "Wireless Earbuds"),
        ("Wireless Earbuds", "True Wireless Earbuds"),
        ("Philips True Wireless Earbuds", "Philips Wireless Earbuds"),
        ("Soundcore P30i Wireless Earbuds", "Wireless Earbuds Soundcore P30i"),
    ],
)
def test_same_product_is_a_duplicate(first, second):
    assert dedupe.are_duplicates(product("a", first), product("b", second))


# ---------------- are_duplicates: different products ----------------

@pytest.mark.parametrize(
    "first,second",
    [
        ("Soundcore P30i Wireless Earbuds", "Soundcore P40i Wireless Earbuds"),
        ("Wave-70 True Wireless Earbuds", "Wave-80 True Wireless Earbuds"),
        ("Galaxy Buds2 Pro", "Galaxy Buds2"),
        ("Wireless Earbuds", "Wireless Charger"),
        ("USB-C Cable 3ft", "USB-C Cable 6ft"),
        ("Wireless Earbuds Case", "Wireless Earbuds"),
        (
            "Sony WF-1000XM4 Industry Leading Noise Canceling Truly Wireless Bluetooth Earbuds",
            "Sony WF-1000XM5 Industry Leading Noise Canceling Truly Wireless Bluetooth Earbuds",
        ),
    ],
)
def test_different_products_are_not_duplicates(first, second):
    assert not dedupe.are_duplicates(product("a", first), product("b", second))


def test_model_number_rule_is_what_keeps_the_long_pair_apart():
    first = "Sony WF-1000XM4 Industry Leading Noise Canceling Truly Wireless Bluetooth Earbuds"
    second = "Sony WF-1000XM5 Industry Leading Noise Canceling Truly Wireless Bluetooth Earbuds"
    tokens_a = dedupe.title_tokens(first)
    tokens_b = dedupe.title_tokens(second)
    overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    assert overlap >= dedupe.SIMILARITY
    assert not dedupe.are_duplicates(product("a", first), product("b", second))


# ---------------- brands ----------------

def test_different_known_brands_never_merge():
    assert not dedupe.are_duplicates(
        product("a", "Wireless Earbuds", brand="Sony"),
        product("b", "Wireless Earbuds", brand="JVC"),
    )


def test_brand_match_ignores_case_and_spaces():
    assert dedupe.are_duplicates(
        product("a", "Wireless Earbuds", brand="Sony"),
        product("b", "Wireless Earbuds", brand="  sony "),
    )


def test_unknown_brand_matches_anything():
    assert dedupe.are_duplicates(
        product("a", "Wireless Earbuds", brand="Sony"),
        product("b", "Wireless Earbuds", brand=""),
    )


def test_empty_titles_are_never_duplicates():
    assert not dedupe.are_duplicates(product("a", ""), product("b", ""))
    assert not dedupe.are_duplicates(product("a", "!!!"), product("b", "Wireless Earbuds"))


# ---------------- dedupe_candidates ----------------

def test_cheapest_copy_is_kept_and_dropped_is_reported():
    expensive = candidate("a", "Wireless Earbuds", 3000)
    cheap = candidate("b", "Wireless Earbuds", 2500)
    kept, dropped = dedupe.dedupe_candidates([expensive, cheap])
    assert kept == [cheap]
    assert dropped == [(expensive, cheap)]


def test_tie_on_price_keeps_more_reviews():
    few = candidate("a", "Wireless Earbuds", 2999, rating_count=10)
    many = candidate("b", "Wireless Earbuds", 2999, rating_count=500)
    kept, dropped = dedupe.dedupe_candidates([few, many])
    assert kept == [many]
    assert dropped == [(few, many)]


def test_tie_on_price_and_reviews_keeps_lower_id():
    high = candidate("b", "Wireless Earbuds", 2999, rating_count=10)
    low = candidate("a", "Wireless Earbuds", 2999, rating_count=10)
    kept, dropped = dedupe.dedupe_candidates([high, low])
    assert kept == [low]
    assert dropped == [(high, low)]


def test_group_of_three_keeps_the_cheapest_and_group_order():
    first = candidate("a1", "Wireless Earbuds", 300)
    other = candidate("b1", "Wireless Charger", 500)
    middle = candidate("a2", "Wireless Earbuds", 200)
    last = candidate("a3", "Wireless Earbuds", 250)
    other_cheap = candidate("b2", "Wireless Charger", 400)
    kept, dropped = dedupe.dedupe_candidates([first, other, middle, last, other_cheap])
    assert kept == [middle, other_cheap]
    assert {d.product.id for d, _ in dropped} == {"a1", "a3", "b1"}
    assert all(k is middle or k is other_cheap for _, k in dropped)


def test_nothing_to_merge_returns_input_untouched():
    items = [
        candidate("a", "Wireless Earbuds", 1000),
        candidate("b", "Wireless Charger", 2000),
    ]
    kept, dropped = dedupe.dedupe_candidates(items)
    assert kept == items
    assert dropped == []


def test_empty_list_returns_two_empty_lists():
    assert dedupe.dedupe_candidates([]) == ([], [])


def test_real_world_list_keeps_four_and_drops_two():
    items = [
        candidate("w1", "Wave-80 True Wireless Earbuds", 2999, brand="Wave", rating_count=10),
        candidate("w2", "Wave-80 True Wireless Earbuds", 2999, brand="Wave", rating_count=20),
        candidate("e1", "wireless earbuds", 1899),
        candidate("e2", "Wireless Earbuds", 1799),
        candidate("p30", "Soundcore P30i Wireless Earbuds", 2499, brand="Soundcore"),
        candidate("p40", "P40i Smart Anc True-wireless Earbuds", 4799, brand="Soundcore"),
    ]
    kept, dropped = dedupe.dedupe_candidates(items)
    assert len(kept) == 4
    assert len(dropped) == 2
    assert {c.product.id for c in kept} == {"w2", "e2", "p30", "p40"}
