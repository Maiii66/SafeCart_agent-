"""Try the SafeCart search pipeline from the command line.

Usage: python -m scripts.try_search [--demo] 'wireless earbuds under $50'
Uses a temporary database, so earlier runs cannot affect the stored risk flags.
"""

import os
import shutil
import sys
import tempfile

from backend import config
from backend.agent import injection, parser, shopper
from backend.money import format_usd
from database import connection

USAGE = "Usage: python -m scripts.try_search [--demo] 'what to buy [under $X]'"


def main(argv=None, say=print):
    args = list(sys.argv[1:] if argv is None else argv)

    if "--demo" in args:
        args.remove("--demo")
        config.USE_DEMO_CATALOG = True

    text = " ".join(args).strip()
    if not text:
        say(USAGE)
        return 1

    try:
        parsed = parser.parse_request(text)
    except parser.ParseError as error:
        say(f"Not understood: {error}")
        return 1

    say(parser.readback_text(parsed))
    for note in parsed.notes:
        say(f"note: {note}")
    if parsed.needs_budget:
        say("A budget is needed before searching. Nothing was searched.")
        return 1

    original_db_path = connection.DB_PATH
    temp_dir = tempfile.mkdtemp(prefix="safecart_search_")
    try:
        connection.DB_PATH = os.path.join(temp_dir, "try_search.db")
        connection.init_db()

        for item in parsed.items:
            budget = item.budget_cents
            if budget is None:
                budget = parsed.total_budget_cents
                if len(parsed.items) > 1:
                    say("(The basket optimiser is not built yet: "
                        f"'{item.query}' is searched with the full total budget.)")

            outcome = shopper.search_and_rank(item.query, budget, limit=10)
            say("")
            say(f"Results for '{item.query}' with a {format_usd(budget)} budget  "
                f"(source: {outcome.source})")
            if outcome.note:
                say(f"note: {outcome.note}")

            if outcome.ranked:
                say("#  score  price  rating  merchant  title")
                for index, ranked in enumerate(outcome.ranked, start=1):
                    product = ranked.candidate.product
                    offer = ranked.candidate.offer
                    rating = "-" if product.rating_value is None else f"{product.rating_value:.1f}"
                    title = injection.sanitize_for_llm(product.title, 120)
                    flag_text = f" [{', '.join(ranked.flags)}]" if ranked.flags else ""
                    say(f"{index}  {ranked.score:.3f}  {format_usd(offer.price_cents)}  "
                        f"{rating}  {offer.domain}  {title}{flag_text}")

            for product, reasons in outcome.blocked:
                title = injection.sanitize_for_llm(product.title, 120)
                say(f"BLOCKED by the injection guard: {title}  ({', '.join(reasons)})")

            for rejection in outcome.rejected:
                title = injection.sanitize_for_llm(rejection.title, 120)
                say(f"REJECTED {rejection.code}: {title}  ({rejection.reason})")

            for dropped, _kept in outcome.duplicates:
                title = injection.sanitize_for_llm(dropped.product.title, 120)
                say(f"DUPLICATE dropped: {title} "
                    f"({format_usd(dropped.offer.price_cents)}), kept the cheaper copy")

            if not outcome.ranked:
                say("(no products passed every check)")
    finally:
        connection.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
