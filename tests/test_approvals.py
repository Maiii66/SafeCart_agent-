# tests/test_approvals.py
# Tests for backend/agent/approvals.py (safety rule 4: signed, one-time, expiring approvals).

import pytest

from backend import config
from backend.agent import approvals
from database import connection, repository

NOW = 1_000_000
TTL = 300


@pytest.fixture(autouse=True)
def fresh_database(tmp_path, monkeypatch):
    """Every test gets its own empty database and a valid signing secret."""
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SIGNING_SECRET", "a" * 64)
    connection.init_db()


def store_signed_approval(approval_id="appr-1", **changes):
    """Put a correctly signed approval straight into the database (no create_approval needed)."""
    payload = {
        "items": [{"product_id": "demo-bud-01", "title": "Nimbus Air Buds",
                   "domain": "demo-shop.example", "price_cents": 3999}],
        "total_cents": 3999, "budget_cents": 5000, "source": "demo", "nonce": "n1",
        "expires_at": NOW + TTL, "step_up_required": False, "step_up_reasons": [],
    }
    payload.update(changes)
    text = approvals._canonical(payload)
    repository.insert_approval(approval_id, text, approvals._sign(text), NOW, NOW + TTL)
    return payload


def test_good_approval_returns_verdict_and_payload():
    stored = store_signed_approval()
    verdict, payload = approvals.verify_approval("appr-1", now=NOW + 10)
    assert verdict.ok
    assert payload["total_cents"] == stored["total_cents"]


def test_missing_approval_still_returns_a_pair():
    verdict, payload = approvals.verify_approval("does-not-exist", now=NOW)
    assert not verdict.ok and verdict.code == "NO_SUCH_APPROVAL"
    assert payload is None


def test_tampered_payload_is_refused_as_a_pair():
    store_signed_approval()
    conn = connection.get_connection()
    conn.execute("UPDATE approvals SET payload = replace(payload, '3999', '1') WHERE id = 'appr-1'")
    conn.commit()
    conn.close()
    verdict, payload = approvals.verify_approval("appr-1", now=NOW + 10)
    assert verdict.code == "BAD_SIGNATURE"
    assert payload is None


def test_expired_approval_is_refused_as_a_pair():
    store_signed_approval()
    verdict, payload = approvals.verify_approval("appr-1", now=NOW + TTL + 1)
    assert verdict.code == "EXPIRED"
    assert payload is None


def test_used_approval_is_refused_as_a_pair():
    store_signed_approval()
    assert repository.mark_approval_used("appr-1", NOW + 5)
    verdict, payload = approvals.verify_approval("appr-1", now=NOW + 10)
    assert verdict.code == "ALREADY_USED"
    assert payload is None

    

# =====================================================================
# Task 3: create_approval with REAL products
# =====================================================================

from backend.models import Candidate, Offer
from backend.services import catalog_demo


@pytest.fixture(autouse=True)
def pinned_settings(monkeypatch):
    """Pin the settings these tests depend on, so a different .env cannot change the results."""
    monkeypatch.setattr(config, "STEP_UP_THRESHOLD_CENTS", 2500)
    monkeypatch.setattr(config, "DAILY_CAP_CENTS", 10000)
    monkeypatch.setattr(config, "APPROVAL_TTL_SECONDS", 300)
    monkeypatch.setattr(config, "TRUSTED_MERCHANTS", ("demo-shop.example", "budget-store.example"))


def candidate(product_id="demo-bud-01", offer_index=0):
    """A real Candidate: a real Product from the demo catalog plus one of its real Offers."""
    product = catalog_demo.get_product(product_id)
    return Candidate(product=product, offer=product.offers[offer_index])


def test_create_approval_works_with_a_real_candidate():
    result = approvals.create_approval([candidate()], 5000, "demo", now=NOW)
    assert result.ok, result.reason
    item = result.payload["items"][0]
    assert item == {"product_id": "demo-bud-01", "title": "Nimbus Air Buds",
                    "domain": "demo-shop.example", "price_cents": 3999}
    assert result.payload["total_cents"] == 3999
    assert result.payload["budget_cents"] == 5000
    assert result.payload["source"] == "demo"
    assert result.payload["expires_at"] == NOW + 300


def test_a_new_approval_verifies_and_is_logged():
    result = approvals.create_approval([candidate()], 5000, "demo", now=NOW)
    verdict, payload = approvals.verify_approval(result.approval_id, now=NOW + 10)
    assert verdict.ok and payload["total_cents"] == 3999
    assert any(row["action"] == "approval_created" for row in repository.list_logs())


def test_basket_total_is_the_sum_of_the_items():
    result = approvals.create_approval([candidate("demo-bud-01"), candidate("demo-chg-01")],
                                       10000, "demo", now=NOW)
    assert result.ok, result.reason
    assert result.payload["total_cents"] == 3999 + 1599


def test_each_item_fits_but_the_sum_is_over_budget():
    result = approvals.create_approval([candidate("demo-bud-01"), candidate("demo-chg-01")],
                                       5000, "demo", now=NOW)
    assert not result.ok and result.code == "OVER_BUDGET"


def test_duplicate_products_are_refused():
    result = approvals.create_approval([candidate(), candidate()], 10000, "demo", now=NOW)
    assert result.code == "DUPLICATE_PRODUCT"


def test_zero_or_too_many_items_are_refused():
    assert approvals.create_approval([], 5000, "demo", now=NOW).code == "BAD_REQUEST"
    eleven = [candidate()] * 11
    assert approvals.create_approval(eleven, 50000, "demo", now=NOW).code == "BAD_REQUEST"


def test_an_item_that_fails_the_spending_rule_is_refused():
    out_of_stock = candidate("demo-bud-06")
    assert approvals.create_approval([out_of_stock], 5000, "demo", now=NOW).code == "OUT_OF_STOCK"


def test_a_bad_budget_is_refused():
    assert approvals.create_approval([candidate()], 0, "demo", now=NOW).code == "BAD_BUDGET"


def test_a_missing_or_short_signing_secret_is_refused(monkeypatch):
    monkeypatch.setattr(config, "SIGNING_SECRET", "")
    assert approvals.create_approval([candidate()], 5000, "demo", now=NOW).code == "NO_SIGNING_SECRET"
    monkeypatch.setattr(config, "SIGNING_SECRET", "too-short")
    assert approvals.create_approval([candidate()], 5000, "demo", now=NOW).code == "NO_SIGNING_SECRET"


def test_two_approvals_never_share_an_id_or_nonce():
    first = approvals.create_approval([candidate()], 5000, "demo", now=NOW)
    second = approvals.create_approval([candidate()], 5000, "demo", now=NOW)
    assert first.approval_id != second.approval_id
    assert first.payload["nonce"] != second.payload["nonce"]


def test_small_trusted_purchase_needs_no_step_up():
    result = approvals.create_approval([candidate("demo-bud-04")], 5000, "demo", now=NOW)   # $14.99
    assert result.ok and result.payload["step_up_required"] is False


def test_a_purchase_above_the_threshold_needs_step_up():
    result = approvals.create_approval([candidate("demo-bud-01")], 5000, "demo", now=NOW)   # $39.99
    assert result.payload["step_up_required"] is True
    assert "total_above_threshold" in result.payload["step_up_reasons"]


def test_a_suspicious_price_flag_needs_step_up():
    flags = {"demo-bud-04": ("suspicious_price",)}
    result = approvals.create_approval([candidate("demo-bud-04")], 5000, "demo", flags=flags, now=NOW)
    assert result.payload["step_up_required"] is True
    assert "suspicious_price" in result.payload["step_up_reasons"]


def test_an_untrusted_merchant_needs_step_up():
    product = catalog_demo.get_product("demo-bud-04")
    shady = Offer("evilbestbuy.com", 1000, "USD", "InStock", "new", "https://evilbestbuy.com/x")
    result = approvals.create_approval([Candidate(product, shady)], 5000, "demo", now=NOW)
    assert result.payload["step_up_required"] is True
    assert "untrusted_merchant" in result.payload["step_up_reasons"]