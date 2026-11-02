# backend/agent/injection.py
# RULE 3: product text from the internet is untrusted. This module looks for text
# that tries to give orders to an AI. It is only ONE layer: the spending limit,
# signed approval and the single payment path still protect the money.

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ScanResult:
    flagged: bool
    reasons: tuple


# ---------- small building blocks for the patterns ----------

_OVERRIDE_VERBS = (
    r"(?:ignore|ignores|ignoring|disregard|disregards|disregarding|"
    r"forget|forgets|forgetting|override|overrides|overriding|"
    r"bypass|bypasses|bypassing|circumvent|circumvents|circumventing)"
)
_QUALIFIERS = r"(?:previous|prior|above|all|any|your|these|system|safety)"
_ORDER_WORDS = (
    r"(?:instructions?|rules?|prompts?|limits?|budget|restrictions?|"
    r"guardrails?|safeguards?)"
)
_ORDER_VERBS = (
    r"(?:ignore|disregard|forget|override|bypass|approve|authorize|buy|"
    r"purchase|rank|recommend|select|choose|you\s+(?:must|should|are)|"
    r"new\s+instructions|reveal|print|show|set|raise|increase|remove|"
    r"turn\s+off|exceed|obey|do\s+not|stop)"
)

_OVERRIDE = (
    r"\b" + _OVERRIDE_VERBS + r"\b(?:\s+\w+){0,4}?\s+\b" + _QUALIFIERS
    + r"\b(?:\s+\w+){0,2}?\s+\b" + _ORDER_WORDS + r"\b"
    + r"|\b" + _OVERRIDE_VERBS + r"\b(?:\s+\w+){0,2}?\s+\b"
    + r"(?:budget|spending\s+limit|guardrails?|safeguards?)\b"
    + r"|\b(?:do\s+not|don't|stop)\s+follow(?:ing)?\s+(?:the\s+)?"
    + r"(?:your|these|those|any|all|previous|prior|above)?\s*"
    + _ORDER_WORDS + r"\b"
)

_IMPERSONATION = (
    r"\bsystem\s+(?:notice|message|prompt|override|instructions?)\b"
    r"|\bnote\s+to\s+(?:the\s+)?ai\b"
    r"|\bimportant\s+notice\s+to\s+(?:the\s+)?assistant\b"
    r"|\battention\s+(?:ai|agents?|assistant)\b"
    r"|\bdear\s+(?:assistant|ai|agent)\b"
    r"|\bif\s+you\s+are\s+an?\s+ai\b"
    r"|\bas\s+an?\s+ai\s+(?:model|assistant|language\s+model)\b"
    r"|\byou\s+are\s+now\b"
    r"|\bnew\s+instructions?\b"
    r"|\bdeveloper\s+mode\b"
    r"|\bjailbreak\b"
    r"|<\|[^>\n]{1,20}\|>|\[/?INST\]|\[SYSTEM\]|#{2,}\s*system\b"
    r"|^[ \t]*(?:system|assistant)\s*:\s*(?=" + _ORDER_VERBS + r"\b)"
)

_PAYMENT_STEERING = (
    r"\b(?:approve|approves|approved|approving|authori[sz]e|authori[sz]es|"
    r"authori[sz]ed|authori[sz]ing)\b[^.\n]{0,40}\b"
    r"(?:payments?|purchases?|orders?)\b"
    r"|\b(?:buy|buys|buying|purchase|purchases|purchasing|complete|completes|"
    r"completing|place|places|placing|submit|submits|submitting|pay|pays|paying)"
    r"\b[^.\n]{0,50}?\bwithout\s+(?:asking|checking|confirmation|confirming|"
    r"consent|permission)\b"
    r"|\bskip\s+(?:the\s+)?(?:approval|confirmation|human\s+review|review|checks?)\b"
    r"|\bregardless\s+of\s+(?:the\s+)?(?:price|cost|budget)\b"
    r"|\bwhatever\s+(?:it|this|that|the\s+\w+)\s+costs?\b"
    r"|\bat\s+any\s+cost\b"
    r"|\b(?:rank|ranks|ranking|recommend|recommends|recommending|select|selects|"
    r"selecting|choose|chooses|choosing|pick|picks|picking)\s+"
    r"(?:this|it|these|that)\b"
    r"|\balways\s+(?:choose|pick|select|buy|approve)\b"
    r"|\b(?:you|the\s+assistant|ai)\s+(?:must|should|shall)\s+"
    r"(?:approve|obey|rank|recommend|select|choose|buy|purchase|pay)\b"
)

_BUDGET_TAMPER = (
    r"\b(?:raise|raises|raising|increase|increases|increasing|change|changes|"
    r"changing|set|sets|setting|exceed|exceeds|exceeding|remove|removes|"
    r"removing|lift|lifts|lifting|disable|disables|disabling|override|"
    r"overrides|overriding|boost|boosts|boosting)\b(?:\s+\w+){0,3}?\s+\b"
    r"(?:budget|spending\s+limit|spend\s+limit)\b"
    r"|\b(?:turn\s+off|turning\s+off|disable|disables|disabling|remove|removes|"
    r"removing|override|overrides|overriding|lift|lifts|lifting|bypass|bypasses|"
    r"bypassing)\b(?:\s+\w+){0,2}?\s+\b"
    r"(?:guardrails?|safeguards?|safety\s+(?:rules?|checks?|limits?))\b"
)

_PROMPT_LEAK = (
    r"\b(?:reveal|reveals|revealing|print|prints|printing|show|shows|showing|"
    r"repeat|repeats|repeating|disclose|discloses|disclosing|leak|leaks|leaking)"
    r"\b(?:\s+\w+){0,4}?\s+\b(?:your\s+)?(?:hidden\s+)?(?:system\s+)?"
    r"(?:instructions?|prompts?|rules)\b"
)

_HIDDEN_MARKUP = r"(?:<\s*script\b|<\s*iframe\b|<!--|javascript\s*:|data\s*:\s*text/html)"
_ENCODED_BLOB = r"[a-z0-9+/=]{120,}"

# The dict order is also the order of the reasons in a ScanResult.
PATTERNS = {
    "override": re.compile(_OVERRIDE, re.IGNORECASE),
    "impersonation": re.compile(_IMPERSONATION, re.IGNORECASE | re.MULTILINE),
    "payment_steering": re.compile(_PAYMENT_STEERING, re.IGNORECASE),
    "budget_tamper": re.compile(_BUDGET_TAMPER, re.IGNORECASE),
    "prompt_leak": re.compile(_PROMPT_LEAK, re.IGNORECASE),
    "hidden_markup": re.compile(_HIDDEN_MARKUP, re.IGNORECASE),
    "encoded_blob": re.compile(_ENCODED_BLOB, re.IGNORECASE),
}

_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e")
_DIRECTION = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
_OTHER_FORMAT = frozenset("\u200e\u200f\u061c")
_REMOVE_FORMAT = _ZERO_WIDTH | _DIRECTION | _OTHER_FORMAT


def _as_text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def normalise(text):
    """Make text comparable: NFKC, no invisibles, simple spaces, lowercase."""
    value = unicodedata.normalize("NFKC", _as_text(text))
    value = "".join(ch for ch in value if ch not in _REMOVE_FORMAT)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    return value.casefold()


def scan_text(text):
    """Scan any value safely and return the names of the patterns that matched."""
    raw = _as_text(text)
    cleaned = normalise(raw)
    reasons = [name for name, pattern in PATTERNS.items() if pattern.search(cleaned)]

    if any(ch in _DIRECTION for ch in raw):
        reasons.append("hidden_direction_marks")
    if sum(1 for ch in raw if ch in _ZERO_WIDTH) >= 3:
        reasons.append("invisible_characters")

    return ScanResult(flagged=bool(reasons), reasons=tuple(reasons))


def scan_product(product):
    """Scan every text field of a product, keeping the field name in each reason."""
    reasons = []
    for field in ("title", "description", "brand", "category"):
        result = scan_text(getattr(product, field, None))
        for reason in result.reasons:
            label = f"{field}:{reason}"
            if label not in reasons:
                reasons.append(label)

    features = getattr(product, "key_features", ()) or ()
    if isinstance(features, str):
        features = (features,)
    try:
        features = tuple(features)
    except TypeError:
        features = ()

    for feature in features:
        result = scan_text(feature)
        for reason in result.reasons:
            label = f"feature:{reason}"
            if label not in reasons:
                reasons.append(label)

    return ScanResult(flagged=bool(reasons), reasons=tuple(reasons))


def split_unsafe(products):
    """Return (safe, blocked); blocked entries are (product, reasons)."""
    safe = []
    blocked = []
    for product in products:
        result = scan_product(product)
        if result.flagged:
            blocked.append((product, result.reasons))
        else:
            safe.append(product)
    return safe, blocked


def sanitize_for_llm(text, max_chars=1000):
    """A clean, short copy of untrusted text for a model to read, never obey."""
    value = unicodedata.normalize("NFKC", _as_text(text))
    kept = []
    for ch in value:
        if ch.isspace():
            kept.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf") or ch in _REMOVE_FORMAT:
            continue
        else:
            kept.append(ch)

    value = re.sub(r"\s+", " ", "".join(kept)).strip()
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        limit = 1000
    return value[:max(limit, 0)].strip()
