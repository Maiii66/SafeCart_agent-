# tests/test_step_up.py
# Tests for backend/agent/step_up.py (safety rule 6: a typed second confirmation for bigger or riskier orders).

import pytest

from backend import config
from backend.agent import approvals, step_up
from backend.models import Candidate
from backend.services import catalog_demo
from database import connection, repository

NOW = 1_000_000


@pytest.fixture(autouse=True)
def settings_and_database(tmp_path, monkeypatch):
    """A fresh database and pinned settings, so a different .env cannot change the results."""
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SIGNING_SECRET", "a" * 64)
    monkeypatch.setattr(config, "STEP_UP_THRESHOLD_CENTS", 2500)
    monkeypatch.setattr(config, "DAILY_CAP_CENTS", 10000)
    monkeypatch.setattr(config, "APPROVAL_TTL_SECONDS", 300)
    monkeypatch.setattr(config, "TRUSTED_MERCHANTS", ("demo-shop.example", "budget-store.example"))
    connection.init_db()


def item(domain="demo-shop.example", product_id="p1"):
    return {"product_id": product_id, "title": "Thing", "domain": domain, "price_cents": 1000}


def reasons(total=1000, remaining=10000, items=None, flags=None):
    return step_up.step_up_reasons(items or [item()], total, remaining, flags)


def approval_needing_step_up():
    """A real approval for the $39.99 Nimbus Air Buds. It is above the $25 threshold."""
    product = catalog_demo.get_product("demo-bud-01")
    result = approvals.create_approval([Candidate(product, product.offers[0])], 5000, "demo", now=NOW)
    assert result.ok and result.payload["step_up_required"] is True
    return result.approval_id


def confirmed_column(approval_id):
    return repository.get_approval(approval_id)["step_up_confirmed"]


# ---------- when is step-up needed? ----------

def test_small_trusted_purchase_needs_nothing():
    assert reasons(total=1000) == []


def test_threshold_boundary_exactly_at_is_fine_one_cent_over_is_not():
    assert reasons(total=2500) == []
    assert reasons(total=2501) == ["total_above_threshold"]


def test_suspicious_price_flag_triggers_step_up():
    flags = {"p1": ("suspicious_price",)}
    assert reasons(flags=flags) == ["suspicious_price"]


def test_a_flag_on_a_different_product_does_not_count():
    assert reasons(flags={"other": ("suspicious_price",)}) == []


@pytest.mark.parametrize("domain", ["evilbestbuy.com", "demo-shop.example.evil.com", "evildemo-shop.example",
                                    "", None, "unknown.example"])
def test_untrusted_merchants_trigger_step_up(domain):
    assert "untrusted_merchant" in reasons(items=[item(domain)])


@pytest.mark.parametrize("domain", ["demo-shop.example", "www.demo-shop.example", "shop.demo-shop.example",
                                    "DEMO-SHOP.EXAMPLE", " budget-store.example "])
def test_trusted_merchants_do_not(domain):
    assert "untrusted_merchant" not in reasons(items=[item(domain)])


def test_one_untrusted_merchant_in_a_basket_is_enough():
    basket = [item("demo-shop.example", "a"), item("evilbestbuy.com", "b")]
    assert "untrusted_merchant" in reasons(items=basket)


def test_more_than_half_of_the_remaining_cap_triggers_step_up():
    assert "over_half_remaining_cap" not in reasons(total=5000, remaining=10000)   # exactly half
    assert "over_half_remaining_cap" in reasons(total=5001, remaining=10000)       # one cent over half


def test_an_exhausted_cap_triggers_step_up():
    assert "over_half_remaining_cap" in reasons(total=100, remaining=0)


def test_reasons_are_not_repeated():
    basket = [item("evil1.com", "a"), item("evil2.com", "b")]
    assert reasons(items=basket).count("untrusted_merchant") == 1


# ---------- the typed confirmation ----------

@pytest.mark.parametrize("typed", ["CONFIRM", "confirm", "  Confirm  ", "39.99", "$39.99", " $39.99 "])
def test_correct_confirmations_are_accepted(typed):
    approval_id = approval_needing_step_up()
    verdict = step_up.confirm_step_up(approval_id, typed, now=NOW + 5)
    assert verdict.ok
    assert confirmed_column(approval_id) == 1


@pytest.mark.parametrize("typed", ["39.98", "40", "3999", "yes", "ok", "", "   ", None, "CONFIRM IT", "$", "-39.99", "39.99.1"])
def test_wrong_confirmations_are_refused(typed):
    approval_id = approval_needing_step_up()
    verdict = step_up.confirm_step_up(approval_id, typed, now=NOW + 5)
    assert not verdict.ok and verdict.code == "STEP_UP_REFUSED"
    assert confirmed_column(approval_id) == 0


def test_confirming_a_missing_expired_or_used_approval_is_refused():
    assert step_up.confirm_step_up("nope", "CONFIRM", now=NOW).code == "NO_SUCH_APPROVAL"

    expired = approval_needing_step_up()
    assert step_up.confirm_step_up(expired, "CONFIRM", now=NOW + 301).code == "EXPIRED"
    assert confirmed_column(expired) == 0

    used = approval_needing_step_up()
    repository.mark_approval_used(used, NOW + 1)
    assert step_up.confirm_step_up(used, "CONFIRM", now=NOW + 2).code == "ALREADY_USED"


def test_confirmations_and_refusals_are_logged():
    approval_id = approval_needing_step_up()
    step_up.confirm_step_up(approval_id, "nope", now=NOW + 1)
    step_up.confirm_step_up(approval_id, "CONFIRM", now=NOW + 2)
    actions = [row["action"] for row in repository.list_logs()]
    assert "step_up_refused" in actions and "step_up_confirmed" in actions


# ---------- attacks: the requirement cannot be switched off ----------

def test_changing_the_confirmed_column_does_not_change_the_signed_requirement():
    approval_id = approval_needing_step_up()
    repository.set_step_up_confirmed(approval_id, 1)          # an attacker flips the column by hand
    verdict, payload = approvals.verify_approval(approval_id, now=NOW + 5)
    assert verdict.ok and payload["step_up_required"] is True  # the signed requirement is untouched


def test_editing_the_requirement_inside_the_stored_payload_breaks_the_signature():
    approval_id = approval_needing_step_up()
    conn = connection.get_connection()
    conn.execute("UPDATE approvals SET payload = replace(payload, '\"step_up_required\":true', "
                 "'\"step_up_required\":false') WHERE id = ?", (approval_id,))
    conn.commit()
    conn.close()
    verdict, payload = approvals.verify_approval(approval_id, now=NOW + 5)
    assert verdict.code == "BAD_SIGNATURE" and payload is None


def test_a_tampered_approval_cannot_be_confirmed():
    approval_id = approval_needing_step_up()
    conn = connection.get_connection()
    conn.execute("UPDATE approvals SET payload = replace(payload, '3999', '1') WHERE id = ?", (approval_id,))
    conn.commit()
    conn.close()
    assert step_up.confirm_step_up(approval_id, "CONFIRM", now=NOW + 5).code == "BAD_SIGNATURE"
    assert confirmed_column(approval_id) == 0