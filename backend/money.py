# backend/money.py
# Money is ALWAYS integer cents inside SafeCart. Floats never touch a price.

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class MoneyError(ValueError):
    """An amount was missing, malformed, negative, or absurdly large."""


_MAX_CENTS = 10**9          # $10 million: anything above is a typo or an attack
_DOLLARS = re.compile(r"^\d{1,9}(\.\d{1,2})?$")


def parse_dollars_to_cents(text):
    """'$1,200.50' -> 120050. Only plain non-negative amounts with at most 2 decimals pass."""
    if text is None:
        raise MoneyError("No amount was given.")
    cleaned = str(text).strip().replace("$", "").replace(",", "").replace(" ", "")
    if not _DOLLARS.match(cleaned):
        raise MoneyError(f"'{text}' is not a valid dollar amount.")
    return int((Decimal(cleaned) * 100).to_integral_value(ROUND_HALF_UP))


def price_to_cents(value):
    """Convert a price from an API (int, float or string) to cents without float drift."""
    if isinstance(value, bool):
        raise MoneyError("A price cannot be true/false.")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise MoneyError(f"'{value}' is not a valid price.") from None
    if not amount.is_finite() or amount < 0:
        raise MoneyError(f"'{value}' is not a valid price.")
    cents = int((amount * 100).to_integral_value(ROUND_HALF_UP))
    if cents > _MAX_CENTS:
        raise MoneyError("That price is unrealistically large.")
    return cents


def to_dollars_str(cents):
    """12345 -> '123.45' (the format PayPal expects)."""
    if cents < 0:
        raise MoneyError("A negative amount cannot be formatted.")
    return f"{cents // 100}.{cents % 100:02d}"


def format_usd(cents):
    """12345 -> '$123.45' for people to read."""
    return f"${cents // 100:,}.{cents % 100:02d}"


def cents_to_float(cents):
    """Only for sending a filter value to an API. Never used for arithmetic."""
    return cents / 100
