import pytest

from backend.money import (MoneyError, format_usd, parse_dollars_to_cents,
                           price_to_cents, to_dollars_str)


@pytest.mark.parametrize("text,cents", [("50", 5000), ("$50", 5000), ("$1,200.50", 120050),
                                        ("49.9", 4990), ("0.99", 99), (" 7 ", 700)])
def test_parse_valid(text, cents):
    assert parse_dollars_to_cents(text) == cents


@pytest.mark.parametrize("bad", ["", "abc", "-5", "5.999", "1e3", "NaN", "50 dollars", None, "$", "9" * 12])
def test_parse_invalid(bad):
    with pytest.raises(MoneyError):
        parse_dollars_to_cents(bad)


def test_price_to_cents_has_no_float_drift():
    assert price_to_cents(79.99) == 7999
    assert price_to_cents(0.1 + 0.2) == 30
    assert price_to_cents(110) == 11000
    assert price_to_cents("19.99") == 1999


@pytest.mark.parametrize("bad", [-1, "x", None, True, float("nan"), float("inf"), 10**10])
def test_price_to_cents_rejects_nonsense(bad):
    with pytest.raises(MoneyError):
        price_to_cents(bad)


def test_formatting():
    assert to_dollars_str(12345) == "123.45"
    assert to_dollars_str(5) == "0.05"
    assert format_usd(123456) == "$1,234.56"
    with pytest.raises(MoneyError):
        to_dollars_str(-1)
