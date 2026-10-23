import random

import pytest

from backend import config, guardrails as g
from backend.models import Offer, Product


def offer(cents=3999, domain="shop.example", currency="USD", availability="InStock", condition="new"):
    return Offer(domain, cents, currency, availability, condition, "https://x")


def product(*offers, pid="p1", title="Thing"):
    return Product(pid, title, "", "B", "", "C", None, 0, (), tuple(offers))


# ---------- budget validation ----------

@pytest.mark.parametrize("bad", [0, -5, 50001, 12.5, "5000", None, True])
def test_bad_budgets_are_refused(bad):
    assert g.validate_budget_cents(bad).code == g.BAD_BUDGET


def test_good_budget():
    assert g.validate_budget_cents(5000).ok and g.validate_budget_cents(50000).ok


# ---------- single offer ----------

def test_price_equal_to_budget_is_allowed_one_cent_over_is_not():
    assert g.evaluate_offer(offer(5000), 5000).ok
    assert g.evaluate_offer(offer(5001), 5000).code == g.OVER_BUDGET


def test_shipping_counts_toward_the_budget():
    assert g.evaluate_offer(offer(4500), 5000, shipping_cents=500).ok
    assert g.evaluate_offer(offer(4500), 5000, shipping_cents=501).code == g.OVER_BUDGET


def test_each_refusal_reason():
    assert g.evaluate_offer(offer(0), 5000).code == g.BAD_PRICE
    assert g.evaluate_offer(offer(-5), 5000).code == g.BAD_PRICE
    assert g.evaluate_offer(offer(1000, currency="EUR"), 5000).code == g.CURRENCY_MISMATCH
    assert g.evaluate_offer(offer(1000, currency="usd"), 5000).ok
    assert g.evaluate_offer(offer(1000, availability="OutOfStock"), 5000).code == g.OUT_OF_STOCK
    assert g.evaluate_offer(offer(1000, availability=""), 5000).code == g.OUT_OF_STOCK
    assert g.evaluate_offer(offer(1000, condition="used"), 5000).code == g.NOT_NEW
    assert g.evaluate_offer(offer(1000, condition=""), 5000).code == g.NOT_NEW


def test_reasons_mention_real_numbers():
    verdict = g.evaluate_offer(offer(6000), 5000)
    assert "$60.00" in verdict.reason and "$50.00" in verdict.reason


# ---------- product with several offers ----------

def test_cheapest_passing_offer_is_chosen():
    verdict, chosen = g.evaluate_product(
        product(offer(4300, "a"), offer(3999, "b"), offer(900, "c", availability="OutOfStock")), 5000)
    assert verdict.ok and chosen.domain == "b"


def test_product_with_only_over_budget_offers_reports_over_budget():
    verdict, chosen = g.evaluate_product(
        product(offer(9000, "a"), offer(800, "b", availability="OutOfStock")), 5000)
    assert chosen is None and verdict.code == g.OVER_BUDGET


def test_product_without_offers():
    assert g.evaluate_product(product(), 5000)[0].code == g.NO_OFFER


def test_invalid_budget_rejects_everything():
    result = g.filter_within_budget([product(offer(100))], 0)
    assert not result.kept and result.rejected[0].code == g.BAD_BUDGET


def test_filter_splits_kept_and_rejected():
    products = [product(offer(3000), pid="ok"), product(offer(9000), pid="pricey"),
                product(offer(1000, availability="OutOfStock"), pid="oos")]
    result = g.filter_within_budget(products, 5000)
    assert [c.product.id for c in result.kept] == ["ok"] and result.kept[0].offer.price_cents == 3000
    assert {r.product_id: r.code for r in result.rejected} == {"pricey": g.OVER_BUDGET, "oos": g.OUT_OF_STOCK}


def test_property_nothing_accepted_ever_exceeds_the_budget():
    rng = random.Random(1234)
    for _ in range(2000):
        budget = rng.randint(1, 50000)
        shipping = rng.choice([0, 0, rng.randint(0, 2000)])
        offers = [offer(rng.randint(-100, 80000), f"d{i}", rng.choice(["USD", "USD", "EUR"]),
                        rng.choice(["InStock", "InStock", "OutOfStock"]), rng.choice(["new", "new", "used"]))
                  for i in range(rng.randint(0, 4))]
        verdict, chosen = g.evaluate_product(product(*offers), budget, shipping)
        if verdict.ok:
            assert chosen.price_cents + shipping <= budget
            assert chosen.price_cents > 0 and chosen.currency.upper() == "USD"
            assert chosen.availability == "InStock" and chosen.condition == "new"
        else:
            assert chosen is None


# ---------- the last check before paying ----------

def test_live_price_unchanged_is_ok():
    approved = offer(3999)
    assert g.verify_live_offer(approved, product(offer(3999)), 5000).ok


def test_price_rise_within_budget_still_needs_fresh_approval():
    v = g.verify_live_offer(offer(3999), product(offer(4200)), 5000)
    assert v.code == g.PRICE_CHANGED and "$39.99" in v.reason and "$42.00" in v.reason


def test_price_rise_over_budget_is_over_budget():
    assert g.verify_live_offer(offer(3999), product(offer(5100)), 5000).code == g.OVER_BUDGET


def test_price_drop_also_needs_fresh_approval():
    assert g.verify_live_offer(offer(3999), product(offer(3000)), 5000).code == g.PRICE_CHANGED


def test_offer_gone_and_out_of_stock():
    assert g.verify_live_offer(offer(3999, "a"), product(offer(3999, "other")), 5000).code == g.OFFER_GONE
    assert g.verify_live_offer(offer(3999), product(offer(3999, availability="OutOfStock")), 5000).code == g.OUT_OF_STOCK


def test_domain_match_ignores_case():
    assert g.verify_live_offer(offer(3999, "Shop.Example"), product(offer(3999, "shop.example")), 5000).ok


def test_bad_budget_blocks_payment_check():
    assert g.verify_live_offer(offer(3999), product(offer(3999)), 0).code == g.BAD_BUDGET
