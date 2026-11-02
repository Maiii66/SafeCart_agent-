# scripts/try_parse.py
# Quick manual check of the parser.

import sys

from backend.agent import parser
from backend.money import format_usd


def main(argv=None, say=print):
    if argv is None:
        argv = sys.argv[1:]
    examples = [
        "wireless earbuds under fifty dollars",
        "earbuds, a phone charger and a case, $100 total",
        "earbuds under 40 and a charger under 20",
        "earbuds under rs 500",
        "two chargers under $30",
        "earbuds",
        "under $50",
    ]

    texts = list(argv) if argv else examples
    for text in texts:
        say(f'You typed: "{text}"')
        try:
            parsed = parser.parse_request(text)
        except parser.ParseError as error:
            say(f"Not understood: {error}")
            return 1
        say(parser.readback_text(parsed))
        if not parsed.items:
            say("(none)")
        else:
            for item in parsed.items:
                if item.budget_cents is None:
                    say(f"- {item.query}: (no budget)")
                else:
                    say(f"- {item.query}: {format_usd(item.budget_cents)}")
        if parsed.total_budget_cents is not None:
            say(f"total budget: {format_usd(parsed.total_budget_cents)}")
        say(f"needs a budget: {parsed.needs_budget}")
        for note in parsed.notes:
            say(f"note: {note}")
        say("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
