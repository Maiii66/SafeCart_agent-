"""Live check of Channel3 + the budget rule, using your real key from .env.

    python -m scripts.smoke_channel3 "wireless earbuds" 50
"""
import sys

from backend import config, guardrails
from backend.money import format_usd, parse_dollars_to_cents
from backend.services import channel3_service


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "wireless earbuds"
    budget_text = sys.argv[2] if len(sys.argv) > 2 else "50"
    budget = parse_dollars_to_cents(budget_text)
    print(config.describe())
    print(f'Searching Channel3 for "{query}" with a {format_usd(budget)} budget...\n')
    try:
        products = channel3_service.search(query, max_price_cents=budget, limit=10)
    except channel3_service.Channel3Error as error:
        print("FAILED:", error)
        return 1
    result = guardrails.filter_within_budget(products, budget)
    print(f"Channel3 returned {len(products)} products. Kept {len(result.kept)}, rejected {len(result.rejected)}.\n")
    for c in result.kept:
        rating = f"{c.product.rating_value} ({c.product.rating_count})" if c.product.rating_value else "no rating"
        print(f"  KEEP   {format_usd(c.offer.price_cents):>9}  {c.product.title[:50]:<50}  {rating}  @ {c.offer.domain}")
    for r in result.rejected:
        print(f"  DROP   {r.code:<18} {r.title[:50]}  ({r.reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
