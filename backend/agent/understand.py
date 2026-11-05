# backend/agent/understand.py
# Gemini may suggest how to read a request, but Python decides what is accepted.
# The rule-based parser always runs first and remains the fallback.

from backend import config
from backend.agent import injection, parser
from backend.money import MoneyError, price_to_cents
from backend.services import llm

MAX_ITEMS = 5
MAX_NAME_CHARS = 80
MAX_PREFERENCES = 5
MAX_PREFERENCE_CHARS = 60

BLOCKED_MESSAGE = "That request did not pass the safety check. Please rephrase it."
_UNCONFIRMED_BUDGET_NOTE = "I could not confirm that budget in your words, so I did not use it."


def _budget_cents(value):
    """Return validated cents for a Gemini dollar amount, or None if it is not allowed."""
    if value is None or isinstance(value, bool):
        return None
    try:
        cents = price_to_cents(value)
    except (MoneyError, TypeError, ValueError):
        return None
    if cents <= 0 or cents > config.MAX_BUDGET_CENTS:
        return None
    return cents


def _validated(data):
    """Validate every field Gemini returned. One bad field rejects the whole suggestion."""
    if not isinstance(data, dict):
        return None

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_ITEMS:
        return None

    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str):
            return None
        name = injection.sanitize_for_llm(name, MAX_NAME_CHARS)
        if not name or injection.scan_text(name).flagged:
            return None

        item_budget = None
        if raw.get("max_price") is not None:
            item_budget = _budget_cents(raw.get("max_price"))
            if item_budget is None:
                return None
        items.append({"name": name, "budget_cents": item_budget})

    total_budget = None
    if data.get("total_budget") is not None:
        total_budget = _budget_cents(data.get("total_budget"))
        if total_budget is None:
            return None

    preferences = []
    raw_preferences = data.get("preferences")
    if raw_preferences is not None:
        if not isinstance(raw_preferences, list) or len(raw_preferences) > MAX_PREFERENCES:
            return None
        for raw in raw_preferences:
            if not isinstance(raw, str):
                return None
            preference = injection.sanitize_for_llm(raw, MAX_PREFERENCE_CHARS)
            if not preference or injection.scan_text(preference).flagged:
                return None
            preferences.append(preference)

    return {
        "items": items,
        "total_budget_cents": total_budget,
        "preferences": preferences,
    }


def _rule_budget(parsed):
    """The single budget the rules actually found, or None when it is absent or ambiguous."""
    if parsed.total_budget_cents is not None:
        return parsed.total_budget_cents
    if len(parsed.items) == 1 and parsed.items[0].budget_cents is not None:
        return parsed.items[0].budget_cents
    if parsed.items and all(item.budget_cents is not None for item in parsed.items):
        budgets = {item.budget_cents for item in parsed.items}
        if len(budgets) == 1:
            return next(iter(budgets))
    return None


def _notes_without_item_guesses(notes):
    return [
        note for note in notes
        if note not in (parser._NO_ITEMS_NOTE, parser._NO_BUDGET_NOTE)
    ]


def _merge(baseline, suggestion):
    # A confident single-item parse is left exactly as it was.
    if len(baseline.items) == 1:
        return baseline

    rule_budget = _rule_budget(baseline)
    gemini_budget = suggestion.get("total_budget_cents")
    notes = list(baseline.notes)

    if gemini_budget is not None and rule_budget is None:
        # Gemini may not invent a budget the rule-based parser did not find.
        gemini_budget = None
        if _UNCONFIRMED_BUDGET_NOTE not in notes:
            notes.append(_UNCONFIRMED_BUDGET_NOTE)
    elif gemini_budget is not None and rule_budget is not None:
        # The user's own words win. Gemini may not replace a parsed budget.
        gemini_budget = rule_budget

    if baseline.items:
        # Multi-item requests keep the rule-based item names. Gemini only fills gaps.
        return parser.ParsedRequest(
            items=baseline.items,
            total_budget_cents=baseline.total_budget_cents,
            notes=tuple(dict.fromkeys(notes)),
        )

    items = []
    for item in suggestion["items"]:
        item_budget = item["budget_cents"]
        if item_budget is not None and (
            rule_budget is None or item_budget != rule_budget
        ):
            item_budget = None
        items.append(parser.ItemRequest(item["name"], item_budget))

    notes = _notes_without_item_guesses(notes)
    total_budget = gemini_budget if gemini_budget is not None else baseline.total_budget_cents
    if total_budget is None and any(item.budget_cents is None for item in items):
        notes.append(parser._NO_BUDGET_NOTE)
    return parser.ParsedRequest(
        items=tuple(items),
        total_budget_cents=total_budget,
        notes=tuple(dict.fromkeys(notes)),
    )


def parse_request(text):
    """Scan raw text, parse with rules, then optionally merge a validated Gemini suggestion."""
    scan = injection.scan_text(text)
    if scan.flagged:
        raise parser.ParseError(BLOCKED_MESSAGE)

    baseline = parser.parse_request(text)
    if not llm.available():
        return baseline

    suggestion = _validated(llm.parse_request(text))
    if suggestion is None:
        return baseline
    return _merge(baseline, suggestion)
