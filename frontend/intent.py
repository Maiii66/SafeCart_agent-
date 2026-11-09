import re


def looks_like_spending_rule(text: str) -> bool:
    lowered = str(text or "").lower()
    return bool(
        re.search(
            r"\b(?:max(?:imum)?|limit|cap)\b.{0,40}"
            r"(?:\$|dollar|per item|per order|per day|orders?)",
            lowered,
        )
        or re.search(
            r"\btrusted\s+(?:stores?|merchants?|sellers?)\s+only\b",
            lowered,
        )
        or re.search(
            r"\b(?:block|avoid|exclude|never buy from)\b.{0,80}"
            r"(?:\.[\s,]|$)",
            lowered,
        )
    )
