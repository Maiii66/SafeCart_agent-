# backend/agent/parser.py
# A small rule-based parser for shopping requests. It never guesses a budget:
# if the text is ambiguous, the budget stays empty and a note explains why.

import re
from dataclasses import dataclass

from backend.money import MoneyError, format_usd, parse_dollars_to_cents


class ParseError(ValueError):
    """The request was empty, unreadable, or too long."""


@dataclass(frozen=True)
class ItemRequest:
    query: str
    budget_cents: int | None = None


@dataclass(frozen=True)
class ParsedRequest:
    items: tuple
    total_budget_cents: int | None = None
    notes: tuple = ()

    @property
    def needs_budget(self):
        # No total and no items means we still need a budget.
        if self.total_budget_cents is not None:
            return False
        if not self.items:
            return True
        return not all(item.budget_cents is not None for item in self.items)


# ---------- number words ----------

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_WORDS = set(_ONES) | set(_TENS) | {"hundred", "thousand", "and", "a"}


def words_to_number(text):
    """'twenty-five' -> 25; None if the words are not a clear number from 0 to 9999."""
    if not isinstance(text, str):
        return None
    cleaned = re.sub(r"\s+", " ", text.strip().lower().replace("-", " "))
    if not cleaned:
        return None
    tokens = cleaned.split(" ")
    if any(token not in _NUMBER_WORDS for token in tokens):
        return None

    total = 0
    current = 0
    saw_number = False
    for index, token in enumerate(tokens):
        if token == "a":
            # "a" is only a number word before hundred or thousand.
            if index + 1 < len(tokens) and tokens[index + 1] in ("hundred", "thousand"):
                saw_number = True
                continue
            return None
        if token == "and":
            # "and" only joins two number words.
            if index == 0 or index == len(tokens) - 1:
                return None
            continue
        if token in _ONES:
            current += _ONES[token]
            saw_number = True
        elif token in _TENS:
            current += _TENS[token]
            saw_number = True
        elif token == "hundred":
            if current == 0:
                if index > 0 and tokens[index - 1] == "a":
                    current = 1
                else:
                    return None
            current *= 100
            saw_number = True
        elif token == "thousand":
            if current == 0:
                if index > 0 and tokens[index - 1] == "a":
                    current = 1
                else:
                    return None
            total += current * 1000
            current = 0
            saw_number = True

    result = total + current
    if not saw_number or result < 0 or result > 9999:
        return None
    return result


# ---------- amounts ----------

# A number with optional thousands commas and at most two decimal places.
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_CURRENCY = r"(?:dollars?|bucks?|usd)"
_KEYWORDS = (
    r"(?:under|below|less\s+than|at\s+most|up\s+to|max(?:imum)?|within|"
    r"no\s+more\s+than|not\s+more\s+than|budget(?:\s+(?:of|is))?)"
)
_WORD_TOKEN = (
    r"(?:a|and|hundred|thousand|zero|one|two|three|four|five|six|seven|eight|"
    r"nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b"
)
_WORD_SEQUENCE = _WORD_TOKEN + r"(?:[\s-]+" + _WORD_TOKEN + r")*"

_AMOUNT_PATTERNS = (
    re.compile(r"\$\s*(?P<num>" + _NUM + r")", re.I),
    re.compile(r"\busd\s*(?P<num>" + _NUM + r")", re.I),
    re.compile(r"\b(?P<num>" + _NUM + r")\s*" + _CURRENCY + r"\b", re.I),
    re.compile(r"\b" + _KEYWORDS + r"\s+\$?\s*(?P<num>" + _NUM + r")\b", re.I),
    re.compile(r"\b" + _KEYWORDS + r"\s+(?P<words>" + _WORD_SEQUENCE + r")"
               r"(?:\s*" + _CURRENCY + r")?\b", re.I),
    re.compile(r"\b(?P<words>" + _WORD_SEQUENCE + r")\s+" + _CURRENCY + r"\b", re.I),
)

_FOREIGN_WORDS = (
    r"(?:rs|inr|eur|gbp|cad|aud|rupees?|euros?|pounds?|yen|canadian|australian)"
)
_FOREIGN_DETECT = re.compile(r"[₹€£¥]|\b" + _FOREIGN_WORDS + r"\b", re.I)
_FOREIGN_PATTERNS = (
    re.compile(r"[₹€£¥]\s*(?:" + _NUM + r")?", re.I),
    re.compile(r"\b" + _FOREIGN_WORDS + r"\b\.?\s*(?:" + _NUM + r")?", re.I),
    re.compile(r"(?:" + _NUM + r")\s*\b" + _FOREIGN_WORDS + r"\b", re.I),
)

_TOTAL_WORDS = re.compile(
    r"\b(?:in\s+total|altogether|overall|combined|in\s+all|all\s+together|total)\b",
    re.I,
)

_LEADING_FILLERS = re.compile(
    r"^(?:please|can\s+you|i\s+need|i\s+want\s+to\s+buy|i\s+want|i\s+would\s+like|"
    r"looking\s+for|find\s+me|find|get\s+me|buy|show\s+me|search\s+for|order|"
    r"purchase|a\s+pair\s+of|some|a|an|the|one|my)\b[\s,]*",
    re.I,
)
_TRAILING_FILLERS = re.compile(
    r"[\s,]*(?:around|about|for|at|of|under|below|max|within|budget|or|between|"
    r"please|thanks)\s*[.!?]*$",
    re.I,
)
_QUANTITY = re.compile(
    r"^(?:(?:10|[1-9])|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"(?:\s*[xX]\s+|\s+)",
    re.I,
)
_EDGE_PUNCTUATION = " \t\r\n,;:.!?&+"

_QUANTITY_NOTE = "Quantities are ignored: one of each item will be bought."
_ONLY_USD_NOTE = "Only US dollars are accepted, so I did not use that price."
_NO_BUDGET_NOTE = "I did not find a budget in what you said."
_NO_ITEMS_NOTE = "I could not tell what you want to buy."
_MULTI_BUDGET_NOTE = "More than one budget was mentioned, so I did not choose one."
_MIXED_BUDGET_NOTE = "Please give one total budget or a budget for each item."

# Temporary markers let us split safely without breaking "1,000" or "hundred and five".
_COMMA_MARK = "\ue000"
_AND_MARK = "\ue001"


def _amount_matches(segment):
    """All amount phrases in one segment, with their values and character spans."""
    found = []
    for pattern in _AMOUNT_PATTERNS:
        for match in pattern.finditer(segment):
            groups = match.groupdict()
            value = None
            if groups.get("num") is not None:
                try:
                    value = parse_dollars_to_cents(groups["num"])
                except MoneyError:
                    value = None
            elif groups.get("words") is not None:
                words = words_to_number(groups["words"])
                value = None if words is None else words * 100
            if value is not None:
                found.append((match.start(), match.end(), value))
    return found


def _foreign_spans(segment):
    spans = []
    for pattern in _FOREIGN_PATTERNS:
        spans.extend((match.start(), match.end()) for match in pattern.finditer(segment))
    return spans


def _merge_spans(spans):
    merged = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


def _remove_spans(text, spans):
    chars = list(text)
    for start, end in _merge_spans(spans):
        for index in range(start, min(end, len(chars))):
            chars[index] = " "
    return "".join(chars)


def _split_segments(text):
    # Protect commas inside numbers, then protect the "and" inside number words.
    protected = re.sub(r"(\d),(?=\d{3}\b)", r"\1" + _COMMA_MARK, text)
    protected = re.sub(
        r"\b(hundred|thousand)\s+and\s+(?=" + _WORD_TOKEN + r")",
        r"\1 " + _AND_MARK + " ",
        protected,
        flags=re.I,
    )
    parts = re.split(
        r"\s*(?:,|;|&|\bplus\b|\balso\b|\bthen\b|\band\b)\s*",
        protected,
        flags=re.I,
    )
    return [
        part.replace(_COMMA_MARK, ",").replace(_AND_MARK, "and").strip()
        for part in parts
        if part.strip()
    ]


def _clean_item_text(text):
    text = _TOTAL_WORDS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip().strip(_EDGE_PUNCTUATION)

    # Remove leading filler words, one layer at a time.
    while True:
        shorter = _LEADING_FILLERS.sub("", text, count=1)
        if shorter == text:
            break
        text = shorter.strip().strip(_EDGE_PUNCTUATION)

    # A leading quantity belongs to the note, not to the item name.
    had_quantity = False
    quantity_match = _QUANTITY.match(text)
    if quantity_match:
        had_quantity = True
        text = text[quantity_match.end():].strip().strip(_EDGE_PUNCTUATION)

    # Remove trailing filler words, also one layer at a time.
    while True:
        shorter = _TRAILING_FILLERS.sub("", text, count=1)
        if shorter == text:
            break
        text = shorter.strip().strip(_EDGE_PUNCTUATION)

    text = re.sub(r"\s+", " ", text).strip().strip(_EDGE_PUNCTUATION)
    return text, had_quantity


def _join_items(queries):
    if len(queries) == 1:
        return queries[0]
    return ", ".join(queries[:-1]) + " and " + queries[-1]


def _prepare_text(text):
    if not isinstance(text, str):
        raise ParseError("Please tell me what you want to buy.")
    if len(text) > 500:
        raise ParseError("That request is too long. Please keep it under 500 characters.")
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    if not cleaned.strip() and not text.strip():
        raise ParseError("Please tell me what you want to buy.")
    return cleaned


def parse_request(text):
    """Read one shopping request. Missing or unclear budgets are never guessed."""
    cleaned = _prepare_text(text)
    is_foreign = bool(_FOREIGN_DETECT.search(cleaned))
    has_total_word = bool(_TOTAL_WORDS.search(cleaned))

    rows = []
    all_values = set()
    any_quantity = False

    for segment in _split_segments(cleaned):
        amounts = _amount_matches(segment)
        spans = [(start, end) for start, end, _value in amounts]
        spans.extend(_foreign_spans(segment))
        item_text, had_quantity = _clean_item_text(_remove_spans(segment, spans))
        any_quantity = any_quantity or had_quantity

        values = {value for _start, _end, value in amounts if value > 0}
        all_values.update(values)
        if item_text:
            rows.append({"query": item_text, "values": values})

    notes = []
    if any_quantity:
        notes.append(_QUANTITY_NOTE)
    if is_foreign:
        notes.append(_ONLY_USD_NOTE)

    if not rows:
        notes.append(_NO_ITEMS_NOTE)
        if not is_foreign and not all_values:
            notes.append(_NO_BUDGET_NOTE)
        return ParsedRequest(items=(), total_budget_cents=None, notes=tuple(notes))

    if is_foreign:
        return ParsedRequest(items=tuple(ItemRequest(row["query"]) for row in rows),
                             total_budget_cents=None, notes=tuple(notes))

    if not all_values:
        notes.append(_NO_BUDGET_NOTE)
        return ParsedRequest(items=tuple(ItemRequest(row["query"]) for row in rows),
                             total_budget_cents=None, notes=tuple(notes))

    if len(rows) == 1:
        if len(all_values) == 1:
            budget = next(iter(all_values))
            item = ItemRequest(rows[0]["query"], budget)
            return ParsedRequest(items=(item,), total_budget_cents=None, notes=tuple(notes))
        notes.append(_MULTI_BUDGET_NOTE)
        return ParsedRequest(items=(ItemRequest(rows[0]["query"]),),
                             total_budget_cents=None, notes=tuple(notes))

    each_item_has_one = all(len(row["values"]) == 1 for row in rows)
    if each_item_has_one and not has_total_word:
        items = tuple(ItemRequest(row["query"], next(iter(row["values"]))) for row in rows)
        return ParsedRequest(items=items, total_budget_cents=None, notes=tuple(notes))

    if len(all_values) == 1:
        total = next(iter(all_values))
        items = tuple(ItemRequest(row["query"]) for row in rows)
        return ParsedRequest(items=items, total_budget_cents=total, notes=tuple(notes))

    notes.append(_MIXED_BUDGET_NOTE)
    items = tuple(ItemRequest(row["query"]) for row in rows)
    return ParsedRequest(items=items, total_budget_cents=None, notes=tuple(notes))


def readback_text(parsed):
    """A short sentence that repeats exactly what SafeCart understood."""
    queries = [item.query for item in parsed.items]
    if not queries:
        return "I did not hear what you want to buy."
    if parsed.total_budget_cents is not None:
        return (f"I heard: {_join_items(queries)}, "
                f"total budget {format_usd(parsed.total_budget_cents)}.")
    if len(queries) == 1 and parsed.items[0].budget_cents is not None:
        return (f"I heard: {queries[0]}, "
                f"maximum {format_usd(parsed.items[0].budget_cents)}.")
    if all(item.budget_cents is not None for item in parsed.items):
        parts = [f"{item.query}, maximum {format_usd(item.budget_cents)}"
                 for item in parsed.items]
        return f"I heard: {_join_items(parts)}."
    return (f"I heard: {_join_items(queries)}, but no budget yet. "
            "Please tell me the most you want to spend.")
