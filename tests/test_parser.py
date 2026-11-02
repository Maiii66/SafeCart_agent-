# tests/test_parser.py
# Acceptance tests for the rule-based shopping request parser.

from dataclasses import FrozenInstanceError

import pytest

from backend.agent.parser import (
    ItemRequest,
    ParseError,
    ParsedRequest,
    parse_request,
    readback_text,
    words_to_number,
)


NUMBER_WORDS = [
    ("zero", 0), ("one", 1), ("fifteen", 15), ("fifty", 50), ("twenty five", 25),
    ("twenty-five", 25), ("ninety nine", 99), ("a hundred", 100), ("one hundred", 100),
    ("one hundred and twenty five", 125), ("one hundred twenty five", 125),
    ("two hundred", 200), ("a thousand", 1000), ("two thousand five hundred", 2500),
    ("  FIFTY  ", 50), ("Twenty-Five", 25),
]

UNCLEAR = ["", "   ", "fish", "fifty cents", "and", "a", "ten thousand",
           "one hundred apples", None]

SINGLE = [
    ("wireless earbuds under $50", "wireless earbuds", 5000),
    ("find me wireless earbuds under fifty dollars", "wireless earbuds", 5000),
    ("earbuds under 50", "earbuds", 5000),
    ("earbuds below $30", "earbuds", 3000),
    ("earbuds less than 30 dollars", "earbuds", 3000),
    ("earbuds at most $30", "earbuds", 3000),
    ("earbuds up to $30", "earbuds", 3000),
    ("earbuds within $30", "earbuds", 3000),
    ("earbuds no more than 30 bucks", "earbuds", 3000),
    ("I need a phone charger, max $20", "phone charger", 2000),
    ("phone charger maximum twenty dollars", "phone charger", 2000),
    ("a gift for my dad, budget of $80", "gift for my dad", 8000),
    ("a gift, budget is eighty dollars", "gift", 8000),
    ("usb-c cable 15 bucks", "usb-c cable", 1500),
    ("usb-c cable 15 usd", "usb-c cable", 1500),
    ("usb-c cable USD 15", "usb-c cable", 1500),
    ("laptop under $1,200.50", "laptop", 120050),
    ("laptop under 1,000 dollars", "laptop", 100000),
    ("earbuds under $49.99", "earbuds", 4999),
    ("earbuds under $ 50", "earbuds", 5000),
    ("earbuds under twenty five dollars", "earbuds", 2500),
    ("earbuds under a hundred", "earbuds", 10000),
    ("headphones max one hundred and twenty five dollars", "headphones", 12500),
    ("$50 earbuds", "earbuds", 5000),
    ("earbuds, $50", "earbuds", 5000),
    ("earbuds for $50", "earbuds", 5000),
    ("earbuds around $50", "earbuds", 5000),
    ("wireless earbuds under $50 please", "wireless earbuds", 5000),
    ("Find me a pair of running shoes, below sixty dollars", "running shoes", 6000),
    ("I want to buy a phone case for under $15.", "phone case", 1500),
    ("EARBUDS UNDER $50", "EARBUDS", 5000),
    ("Wave-80 earbuds below $30", "Wave-80 earbuds", 3000),
    ("p30i earbuds under $50", "p30i earbuds", 5000),
    ("4k tv under $400", "4k tv", 40000),
    ("earbuds under $50 and under $50", "earbuds", 5000),
]

NO_BUDGET = [
    "earbuds", "wireless earbuds", "earbuds and a case", "earbuds 50",
    "iphone 15 case", "earbuds, a case", "something nice", "earbuds under",
    "earbuds budget", "50",
]

ZERO = ["earbuds under $0", "earbuds under 0 dollars", "earbuds under zero dollars"]

TWO_AMOUNTS = [
    "earbuds under 50 or 30 dollars",
    "earbuds under $50 or $60",
    "earbuds between $30 and $60",
]

FOREIGN = [
    "earbuds under rs 500",
    "earbuds under \u20b9500",
    "earbuds under 500 rupees",
    "earbuds under \u20ac40",
    "earbuds under 40 euros",
    "earbuds under \u00a330",
    "earbuds under 30 pounds",
    "earbuds under 500 INR",
    "earbuds under $50 or 4000 yen",
]

QUANTITY = [
    ("two chargers under $30", "chargers"),
    ("2 chargers under $30", "chargers"),
    ("3x earbuds under $30", "earbuds"),
    ("3 x earbuds under $30", "earbuds"),
    ("2 xbox controllers under $30", "xbox controllers"),
]

READBACK = [
    ("wireless earbuds under $50", "I heard: wireless earbuds, maximum $50.00."),
    ("earbuds under fifteen dollars", "I heard: earbuds, maximum $15.00."),
    ("earbuds under fifty dollars", "I heard: earbuds, maximum $50.00."),
    ("laptop under $1,200.50", "I heard: laptop, maximum $1,200.50."),
    ("earbuds, a phone charger and a case, $100 total",
     "I heard: earbuds, phone charger and case, total budget $100.00."),
    ("earbuds and a case under $100",
     "I heard: earbuds and case, total budget $100.00."),
    ("earbuds under 40 and a charger under 20",
     "I heard: earbuds, maximum $40.00 and charger, maximum $20.00."),
    ("earbuds", "I heard: earbuds, but no budget yet. Please tell me the most you want to spend."),
    ("under $50", "I did not hear what you want to buy."),
]


@pytest.mark.parametrize("text, expected", NUMBER_WORDS)
def test_words_to_number(text, expected):
    assert words_to_number(text) == expected


@pytest.mark.parametrize("text", UNCLEAR)
def test_words_to_number_is_none_when_unclear(text):
    assert words_to_number(text) is None


@pytest.mark.parametrize("text, query, cents", SINGLE)
def test_single_item_gets_its_one_budget(text, query, cents):
    parsed = parse_request(text)
    assert parsed.total_budget_cents is None
    assert parsed.needs_budget is False
    assert len(parsed.items) == 1
    assert parsed.items[0].query == query
    assert parsed.items[0].budget_cents == cents


def test_basket_with_total_at_the_end():
    parsed = parse_request("earbuds, a phone charger and a case, $100 total")
    assert [item.query for item in parsed.items] == ["earbuds", "phone charger", "case"]
    assert all(item.budget_cents is None for item in parsed.items)
    assert parsed.total_budget_cents == 10000
    assert parsed.needs_budget is False


@pytest.mark.parametrize("text", [
    "earbuds and a case under $100",
    "earbuds, a case, $100 in total",
    "earbuds plus a case, one hundred dollars altogether",
    "earbuds & a case max $100 overall",
])
def test_one_amount_becomes_the_total_budget(text):
    parsed = parse_request(text)
    assert [item.query for item in parsed.items] == ["earbuds", "case"]
    assert all(item.budget_cents is None for item in parsed.items)
    assert parsed.total_budget_cents == 10000
    assert parsed.needs_budget is False


def test_each_item_can_have_its_own_budget():
    parsed = parse_request("earbuds under 40 and a charger under 20")
    assert [(item.query, item.budget_cents) for item in parsed.items] == [
        ("earbuds", 4000), ("charger", 2000)]
    assert parsed.total_budget_cents is None
    assert parsed.needs_budget is False


def test_each_item_can_have_its_own_budget_with_gifts():
    parsed = parse_request("gift ideas under $25 and a card under $5")
    assert [(item.query, item.budget_cents) for item in parsed.items] == [
        ("gift ideas", 2500), ("card", 500)]
    assert parsed.total_budget_cents is None
    assert parsed.needs_budget is False


def test_per_item_amounts_plus_a_total_is_ambiguous():
    parsed = parse_request("earbuds $30 and charger $20, $60 total")
    assert parsed.total_budget_cents is None
    assert all(item.budget_cents is None for item in parsed.items)
    assert parsed.needs_budget is True
    assert any("total budget or a budget for each" in note for note in parsed.notes)


@pytest.mark.parametrize("text", NO_BUDGET)
def test_no_budget_is_never_guessed(text):
    parsed = parse_request(text)
    assert parsed.needs_budget is True
    assert parsed.total_budget_cents is None
    assert all(item.budget_cents is None for item in parsed.items)


def test_missing_budget_has_a_helpful_note():
    parsed = parse_request("earbuds")
    assert any("did not find a budget" in note for note in parsed.notes)


@pytest.mark.parametrize("text", ZERO)
def test_zero_is_not_a_budget(text):
    parsed = parse_request(text)
    assert parsed.needs_budget is True
    assert parsed.total_budget_cents is None
    assert all(item.budget_cents is None for item in parsed.items)


@pytest.mark.parametrize("text", TWO_AMOUNTS)
def test_two_amounts_are_ambiguous(text):
    parsed = parse_request(text)
    assert parsed.needs_budget is True
    assert parsed.total_budget_cents is None
    assert parsed.items[0].budget_cents is None
    assert any("More than one budget" in note for note in parsed.notes)
    assert not parsed.items[0].query.endswith(("or", "between"))


@pytest.mark.parametrize("text", FOREIGN)
def test_foreign_currency_is_not_treated_as_usd(text):
    parsed = parse_request(text)
    assert parsed.needs_budget is True
    assert parsed.total_budget_cents is None
    assert all(item.budget_cents is None for item in parsed.items)
    assert any("Only US dollars" in note for note in parsed.notes)


def test_rs_inside_a_normal_word_is_not_currency():
    parsed = parse_request("chargers under $30")
    assert parsed.items[0].query == "chargers"
    assert parsed.items[0].budget_cents == 3000


def test_runners_are_not_rupees():
    parsed = parse_request("earbuds under $30 for runners")
    assert parsed.items[0].query == "earbuds for runners"
    assert parsed.items[0].budget_cents == 3000
    assert not any("Only US dollars" in note for note in parsed.notes)


@pytest.mark.parametrize("text, query", QUANTITY)
def test_leading_quantity_is_removed_and_noted(text, query):
    parsed = parse_request(text)
    assert parsed.items[0].query == query
    assert parsed.items[0].budget_cents == 3000
    assert any(note == "Quantities are ignored: one of each item will be bought."
               for note in parsed.notes)


def test_4k_is_not_a_quantity():
    parsed = parse_request("4k tv under $30")
    assert parsed.items[0].query == "4k tv"
    assert parsed.notes == ()


def test_amount_with_no_item():
    parsed = parse_request("under $50")
    assert parsed.items == ()
    assert parsed.needs_budget is True
    assert any("could not tell what you want" in note for note in parsed.notes)


def test_control_characters_become_spaces():
    parsed = parse_request("earbuds\x00 under\x07 $50")
    assert parsed.items[0].query == "earbuds"
    assert parsed.items[0].budget_cents == 5000


def test_only_control_characters_give_no_items():
    assert parse_request("\x00\x01").items == ()


@pytest.mark.parametrize("text", ["", "   ", "\n\t ", None])
def test_empty_requests_are_rejected(text):
    with pytest.raises(ParseError):
        parse_request(text)


def test_too_long_request_is_rejected():
    with pytest.raises(ParseError, match="too long"):
        parse_request("x" * 501)


def test_exactly_five_hundred_characters_is_allowed():
    text = "earbuds under $50 " + "a" * 482
    assert len(text) == 500
    parsed = parse_request(text)
    assert parsed.items[0].query.startswith("earbuds")


def test_results_are_immutable():
    item = ItemRequest(query="earbuds", budget_cents=5000)
    parsed = ParsedRequest(items=(item,))
    with pytest.raises(FrozenInstanceError):
        parsed.total_budget_cents = 1
    with pytest.raises(FrozenInstanceError):
        item.budget_cents = 1


def test_number_words_in_a_budget():
    assert parse_request("earbuds under fifteen").items[0].budget_cents == 1500
    assert parse_request("earbuds under fifty").items[0].budget_cents == 5000


@pytest.mark.parametrize("text, expected", READBACK)
def test_readback_text(text, expected):
    assert readback_text(parse_request(text)) == expected
