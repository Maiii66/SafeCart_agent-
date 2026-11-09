# backend/agent/policy_parser.py
# Gemini may suggest a spending policy, but Python decides what is accepted.
# A simple rule-based parser always runs as the fallback. Ambiguous text is refused,
# never guessed, because a guessed limit could be looser than the person asked for.

import re

from backend.agent import injection, policy as policy_rules
from backend.money import MoneyError, parse_dollars_to_cents, price_to_cents
from backend.services import llm

MAX_TEXT_CHARS = 500
MAX_BLOCKED_MERCHANTS = policy_rules.MAX_BLOCKED_MERCHANTS

_AI_FIELDS = {
    "max_item_price": "max_item_cents",
    "max_order_total": "max_order_cents",
    "max_daily_spend": "max_daily_spend_cents",
    "max_orders_per_hour": "max_orders_per_hour",
    "max_orders_per_day": "max_orders_per_day",
    "trusted_only": "trusted_only",
    "blocked_merchants": "blocked_merchants",
}

_AMOUNT = re.compile(r"\$\s*(\d[\d,]*(?:\.\d{1,2})?)")
_DOMAIN = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b")
_PER_ITEM = re.compile(r"\b(?:per|each|a|an)\s+(?:item|product)\b")
_EACH = re.compile(r"\beach\b(?!\s+(?:day|hour|week|month|year)\b)|\bapiece\b")
_ITEM_WORD = re.compile(r"\b(?:item|product)\b")
_ORDER_WORD = re.compile(r"\b(?:total|order|purchase|basket|checkout)\b")
_DAY_WORD = re.compile(r"\b(?:daily|per\s+day|a\s+day|each\s+day|/\s*day)\b")
_ORDERS_PER_DAY = re.compile(
    r"\b(\d{1,3})\s*orders?\s*(?:(?:per|a|an|each|/)\s*day|daily)\b"
)
_ORDERS_PER_HOUR = re.compile(
    r"\b(\d{1,3})\s*orders?\s*(?:(?:per|an|a|each|/)\s*hour|hourly)\b"
)
_TRUSTED_ONLY = re.compile(
    r"\btrusted\s+(?:stores?|merchants?|sellers?|shops?)\s+only\b"
    r"|\bonly\s+(?:buy\s+from\s+)?trusted\s+(?:stores?|merchants?|sellers?|shops?)\b"
)
_BLOCK_PREFIX = re.compile(
    r"(?:don'?t|do\s+not|never)\s+(?:buy|shop|order)\s+from\s+(.+)"
    r"|\b(?:block|blocked|avoid|exclude)\b(?:\s+(?:merchants?|stores?|sellers?))?\s*[:\-]?\s*(.+)"
    r"|\bno\s+(?:purchases?|orders?)\s+from\s+(.+)"
)


class PolicyParseError(ValueError):
    """The text was empty, unsafe, or too ambiguous to turn into a policy."""

    def __init__(self, code, message):
        self.code = str(code or "BAD_REQUEST")
        super().__init__(message)


def _cents_from_text(text):
    try:
        return parse_dollars_to_cents(text)
    except MoneyError:
        return None


def _nearest(pattern, before, after):
    """Distance from the amount to the closest keyword match, or None."""
    distances = []
    for match in pattern.finditer(after):
        distances.append(match.start())
    for match in pattern.finditer(before):
        distances.append(len(before) - match.end())
    return min(distances) if distances else None


def _classify_amount(before, after):
    before, after = before[-30:], after[:30]
    candidates = []
    distance = _nearest(_PER_ITEM, before, after)
    if distance is not None:
        candidates.append((distance - 1, "max_item_cents"))  # "per item" is explicit
    distance = _nearest(_EACH, before, after)
    if distance is not None:
        candidates.append((distance - 1, "max_item_cents"))
    distance = _nearest(_ITEM_WORD, before, after)
    if distance is not None:
        candidates.append((distance, "max_item_cents"))
    distance = _nearest(_DAY_WORD, before, after)
    if distance is not None:
        candidates.append((distance, "max_daily_spend_cents"))
    distance = _nearest(_ORDER_WORD, before, after)
    if distance is not None:
        candidates.append((distance, "max_order_cents"))
    return min(candidates)[1] if candidates else None


def _clause(text):
    """Keep only the part of the context that belongs to the same clause as the amount."""
    return re.split(r"\b(?:and|but|then)\b|[,;\n]", text)


def _rule_based(text):
    """A conservative fallback. Returns a raw policy dict, or None when anything is ambiguous."""
    raw = {}
    matches = list(_AMOUNT.finditer(text))
    previous_end = 0

    for index, match in enumerate(matches):
        amount = _cents_from_text(match.group(1))
        if amount is None:
            return None

        before = _clause(text[previous_end:match.start()])[-1]
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        after = _clause(text[match.end():next_start])[0]
        previous_end = match.end()

        field = _classify_amount(before, after)
        if field is None:
            return None
        existing = raw.get(field)
        raw[field] = amount if existing is None else min(existing, amount)

    day_match = _ORDERS_PER_DAY.search(text)
    if day_match:
        raw["max_orders_per_day"] = int(day_match.group(1))
    hour_match = _ORDERS_PER_HOUR.search(text)
    if hour_match:
        raw["max_orders_per_hour"] = int(hour_match.group(1))

    if _TRUSTED_ONLY.search(text):
        raw["trusted_only"] = True

    blocked = []
    for match in _BLOCK_PREFIX.finditer(text):
        clause = next((group for group in match.groups() if group), "")
        for domain in _DOMAIN.findall(clause):
            domain = policy_rules.normalise_domain(domain)
            if domain and domain not in blocked:
                blocked.append(domain)
    if blocked:
        raw["blocked_merchants"] = blocked

    return raw or None


def _valid_price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        cents = price_to_cents(value)
    except (MoneyError, TypeError, ValueError):
        return None
    return cents if cents > 0 else None


def _valid_count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _from_ai(data):
    """Validate a Gemini dict against the strict schema. Returns (policy, notices) or None."""
    if not isinstance(data, dict):
        return None
    if any(key not in _AI_FIELDS for key in data):
        return None
    if not any(key in _AI_FIELDS for key in data):
        return None

    raw = {}
    for key, value in data.items():
        field = _AI_FIELDS[key]
        if key in ("max_item_price", "max_order_total", "max_daily_spend"):
            cents = _valid_price(value)
            if cents is None:
                return None
            raw[field] = cents
        elif key in ("max_orders_per_hour", "max_orders_per_day"):
            count = _valid_count(value)
            if count is None:
                return None
            raw[field] = count
        elif key == "trusted_only":
            if not isinstance(value, bool):
                return None
            raw[field] = value
        elif key == "blocked_merchants":
            if not isinstance(value, list) or len(value) > MAX_BLOCKED_MERCHANTS:
                return None
            if any(not isinstance(item, str) for item in value):
                return None
            raw[field] = list(value)

    try:
        return policy_rules.clamp_policy(raw)
    except policy_rules.PolicyError:
        return None


def propose_policy(text):
    """Turn plain-English rules into a clamped proposal. Nothing is saved here."""
    if not isinstance(text, str):
        raise PolicyParseError("BAD_REQUEST", "Spending rules must be text.")
    cleaned = text.strip()
    if not cleaned:
        raise PolicyParseError("BAD_REQUEST", "Type your spending rules first.")
    if len(cleaned) > MAX_TEXT_CHARS:
        raise PolicyParseError("BAD_REQUEST", f"Spending rules must be at most {MAX_TEXT_CHARS} characters.")

    scan = injection.scan_text(cleaned)
    if scan.flagged:
        raise PolicyParseError(
            "INJECTION_BLOCKED",
            "That text did not pass the safety check. Please rephrase your spending rules.",
        )

    if llm.available():
        suggestion = _from_ai(llm.parse_policy(cleaned))
        if suggestion is not None:
            proposed, notices = suggestion
            if policy_rules.has_rules(proposed):
                return {"policy": proposed, "clamped": notices, "source": "gemini"}

    raw = _rule_based(injection.normalise(cleaned))
    if raw is None:
        raise PolicyParseError(
            "POLICY_AMBIGUOUS",
            "I could not turn those rules into a clear policy. Say, for example, "
            "'max $50 per item, trusted stores only, max 3 orders a day'.",
        )

    try:
        proposed, notices = policy_rules.clamp_policy(raw)
    except policy_rules.PolicyError as error:
        raise PolicyParseError("BAD_POLICY", str(error)) from None

    if not policy_rules.has_rules(proposed):
        raise PolicyParseError(
            "POLICY_AMBIGUOUS",
            "I could not turn those rules into a clear policy.",
        )
    return {"policy": proposed, "clamped": notices, "source": "rules"}
