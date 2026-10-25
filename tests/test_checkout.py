# tests/test_checkout.py
# Tests for backend/agent/checkout.py: the ONLY code path that can start or finish a payment.
# PayPal is always faked, so these tests never touch the network.

import dataclasses

import pytest

from backend import config
from backend.agent import approvals, checkout, limits, orders, step_up
from backend.models import Candidate
from backend.services import catalog, catalog_demo, paypal_service
from database import connection, repository
from tests.conftest import FakeResponse

NOW = 1_000_000


# ---------- test setup ----------

class FakePayPal:
    """Stands in for paypal_service. Records every call so tests can prove what was (not) sent."""

    def __init__(self):
        self.create_calls = []
        self.capture_calls = []
        self.create_error = None
        self.capture_error = None
        self.capture_status = "COMPLETED"
        self.capture_amount = None            # None means "exactly the order total"
        self.approval_was_used_when_called = None
        self._last_total = 0

    def create_order(self, items, idempotency_key, reference_id="safecart-order"):
        approval = repository.get_approval(idempotency_key)
        self.approval_was_used_when_called = bool(approval and approval["used_at"] is not None)
        self.create_calls.append({"items": list(items), "key": idempotency_key, "reference": reference_id})
        if self.create_error:
            raise self.create_error
        self._last_total = sum(i.unit_cents * i.quantity for i in items)
        return paypal_service.OrderResult("PP-ORDER-1", "PAYER_ACTION_REQUIRED",
                                          "https://sandbox.paypal.com/checkoutnow?token=PP-ORDER-1",
                                          self._last_total)

    def capture_order(self, order_id, idempotency_key):
        self.capture_calls.append({"order_id": order_id, "key": idempotency_key})
        if self.capture_error:
            raise self.capture_error
        amount = self._last_total if self.capture_amount is None else self.capture_amount
        return paypal_service.CaptureResult(order_id, self.capture_status, "CAP-1", amount)


@pytest.fixture
def paypal(monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(paypal_service, "create_order", fake.create_order)
    monkeypatch.setattr(paypal_service, "capture_order", fake.capture_order)
    return fake


@pytest.fixture(autouse=True)
def settings_and_database(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SIGNING_SECRET", "a" * 64)
    monkeypatch.setattr(config, "PAYPAL_ENV", "sandbox")
    monkeypatch.setattr(config, "STEP_UP_THRESHOLD_CENTS", 2500)
    monkeypatch.setattr(config, "DAILY_CAP_CENTS", 10000)
    monkeypatch.setattr(config, "MAX_ORDERS_PER_HOUR", 3)
    monkeypatch.setattr(config, "APPROVAL_TTL_SECONDS", 300)
    monkeypatch.setattr(config, "TRUSTED_MERCHANTS", ("demo-shop.example", "budget-store.example"))
    connection.init_db()


def candidate(product_id):
    product = catalog_demo.get_product(product_id)
    return Candidate(product, product.offers[0])


def make_approval(product_ids=("demo-bud-04",), budget=5000, source="demo", confirm=True):
    """A real signed approval. demo-bud-04 is $14.99 and needs no step-up; demo-bud-01 is $39.99 and does."""
    result = approvals.create_approval([candidate(p) for p in product_ids], budget, source, now=NOW)
    assert result.ok, result.reason
    if result.payload["step_up_required"] and confirm:
        assert step_up.confirm_step_up(result.approval_id, "CONFIRM", now=NOW).ok
    return result.approval_id


def run(approval_id, **kwargs):
    return checkout.execute_checkout(approval_id, now=NOW, **kwargs)


def assert_refused(result, code, paypal):
    assert not result.ok and result.code == code, (result.code, result.reason)
    assert paypal.create_calls == [], "PayPal must NOT be called when checkout is refused"
    assert any(row["action"] == "checkout_refused" for row in repository.list_logs())


def add_order(total_cents, status, age_seconds=0):
    return repository.insert_order("older-approval", "PP-OLD", status, total_cents, NOW - age_seconds)


def status_of(order_id):
    return repository.get_order(order_id)["status"]


# ---------- the happy path ----------

def test_happy_path_sends_the_signed_amounts_to_paypal_once(paypal):
    approval_id = make_approval()
    result = run(approval_id)
    assert result.ok and result.code == "OK"
    assert result.approve_url.startswith("https://sandbox.paypal.com/")

    assert len(paypal.create_calls) == 1
    call = paypal.create_calls[0]
    assert [(i.name, i.unit_cents, i.quantity) for i in call["items"]] == [("EchoTone Basic Buds", 1499, 1)]
    assert call["key"] == approval_id and call["reference"] == approval_id

    order = repository.get_order(result.order_id)
    assert (order["status"], order["total_cents"], order["paypal_order_id"]) == ("CREATED", 1499, "PP-ORDER-1")
    assert order["approval_id"] == approval_id
    assert repository.get_approval(approval_id)["used_at"] is not None
    assert any(row["action"] == "order_created" for row in repository.list_logs())


def test_a_basket_becomes_one_paypal_order_with_every_item(paypal):
    approval_id = make_approval(("demo-bud-04", "demo-chg-01"))
    result = run(approval_id)
    assert result.ok
    items = paypal.create_calls[0]["items"]
    assert sorted((i.name, i.unit_cents) for i in items) == [("EchoTone Basic Buds", 1499),
                                                            ("VoltLine 20W USB-C Charger", 1599)]
    assert repository.get_order(result.order_id)["total_cents"] == 1499 + 1599


def test_the_approval_is_used_up_before_paypal_is_called(paypal):
    run(make_approval())
    assert paypal.approval_was_used_when_called is True


def test_a_step_up_purchase_goes_through_once_confirmed(paypal):
    approval_id = make_approval(("demo-bud-01",), confirm=True)            # $39.99
    assert run(approval_id).ok


# ---------- refusals: PayPal must never be called ----------

def test_refused_when_not_in_sandbox(paypal, monkeypatch):
    approval_id = make_approval()
    monkeypatch.setattr(config, "PAYPAL_ENV", "live")
    assert_refused(run(approval_id), "NOT_SANDBOX", paypal)


def test_refused_while_paused(paypal):
    approval_id = make_approval()
    limits.set_paused(True)
    assert_refused(run(approval_id), "PAUSED", paypal)


def test_refused_for_an_unknown_approval(paypal):
    assert_refused(run("does-not-exist"), "NO_SUCH_APPROVAL", paypal)


def test_refused_for_an_expired_approval(paypal):
    approval_id = make_approval()
    assert_refused(checkout.execute_checkout(approval_id, now=NOW + 301), "EXPIRED", paypal)


def test_refused_for_a_tampered_approval(paypal):
    approval_id = make_approval()
    conn = connection.get_connection()
    conn.execute("UPDATE approvals SET payload = replace(payload, '1499', '1') WHERE id = ?", (approval_id,))
    conn.commit()
    conn.close()
    assert_refused(run(approval_id), "BAD_SIGNATURE", paypal)


def test_refused_when_step_up_was_not_confirmed(paypal):
    approval_id = make_approval(("demo-bud-01",), confirm=False)
    assert_refused(run(approval_id), "STEP_UP_REQUIRED", paypal)


def test_confirming_a_different_approval_does_not_help(paypal):
    needs_it = make_approval(("demo-bud-01",), confirm=False)
    make_approval(("demo-bud-01",), confirm=True)                          # a second approval, confirmed
    assert_refused(run(needs_it), "STEP_UP_REQUIRED", paypal)


def test_refused_over_the_daily_cap(paypal):
    approval_id = make_approval()
    add_order(9000, "CAPTURED")                                            # 9000 + 1499 is over 10000
    assert_refused(run(approval_id), "DAILY_CAP", paypal)


def test_refused_over_the_hourly_limit(paypal):
    approval_id = make_approval()
    for _ in range(3):
        add_order(100, "FAILED")
    assert_refused(run(approval_id), "HOURLY_LIMIT", paypal)


# ---------- refusals: the live price check ----------

def test_a_price_rise_within_budget_needs_a_fresh_approval(paypal):
    approval_id = make_approval()                                          # $14.99
    catalog_demo.set_price_drift("demo-bud-04", 500)                       # now $19.99
    assert_refused(run(approval_id), "PRICE_CHANGED", paypal)


def test_a_price_rise_over_budget_is_refused(paypal):
    approval_id = make_approval(budget=3000)
    catalog_demo.set_price_drift("demo-bud-04", 2000)                      # now $34.99, over $30
    assert_refused(run(approval_id), "OVER_BUDGET", paypal)


def test_a_price_drop_also_needs_a_fresh_approval(paypal):
    approval_id = make_approval()
    catalog_demo.set_price_drift("demo-bud-04", -100)
    assert_refused(run(approval_id), "PRICE_CHANGED", paypal)


def test_one_drifted_item_in_a_basket_refuses_the_whole_basket(paypal):
    approval_id = make_approval(("demo-bud-04", "demo-chg-01"))
    catalog_demo.set_price_drift("demo-chg-01", 300)
    assert_refused(run(approval_id), "PRICE_CHANGED", paypal)


def test_a_refused_price_check_does_not_burn_the_approval(paypal):
    approval_id = make_approval()
    catalog_demo.set_price_drift("demo-bud-04", 500)
    assert not run(approval_id).ok
    assert repository.get_approval(approval_id)["used_at"] is None
    catalog_demo.clear_price_drift()
    assert run(approval_id).ok                                             # the same approval still works


def test_refused_when_the_live_price_cannot_be_checked(paypal, monkeypatch):
    approval_id = make_approval()

    def broken(product_id, source):
        raise catalog.LivePriceUnavailable("Channel3 is down")
    monkeypatch.setattr(catalog, "get_live_product", broken)
    assert_refused(run(approval_id), "PRICE_UNVERIFIABLE", paypal)


def test_a_real_channel3_outage_fails_closed(paypal, fake_http):
    approval_id = make_approval(source="channel3")
    fake_http.respond(lambda method, url, kwargs: FakeResponse(500, None, text="down"))
    assert_refused(run(approval_id), "PRICE_UNVERIFIABLE", paypal)


def test_refused_for_an_unknown_source(paypal):
    approval_id = make_approval(source="mystery")
    assert_refused(run(approval_id), "PRICE_UNVERIFIABLE", paypal)


def test_refused_when_the_merchant_no_longer_lists_the_product(paypal, monkeypatch):
    approval_id = make_approval()
    product = catalog_demo.get_product("demo-bud-04")
    elsewhere = dataclasses.replace(product.offers[0], domain="other-shop.example")
    monkeypatch.setattr(catalog, "get_live_product",
                        lambda pid, src: dataclasses.replace(product, offers=(elsewhere,)))
    assert_refused(run(approval_id), "OFFER_GONE", paypal)


def test_refused_when_the_product_went_out_of_stock(paypal, monkeypatch):
    approval_id = make_approval()
    product = catalog_demo.get_product("demo-bud-04")
    sold_out = dataclasses.replace(product.offers[0], availability="OutOfStock")
    monkeypatch.setattr(catalog, "get_live_product",
                        lambda pid, src: dataclasses.replace(product, offers=(sold_out,)))
    assert_refused(run(approval_id), "OUT_OF_STOCK", paypal)


# ---------- replay, races and PayPal failures ----------

def test_replaying_an_approval_is_refused_and_paypal_is_called_once(paypal):
    approval_id = make_approval()
    assert run(approval_id).ok
    second = run(approval_id)
    assert not second.ok and second.code == "ALREADY_USED"
    assert len(paypal.create_calls) == 1


def test_losing_the_race_at_the_last_moment_is_refused(paypal, monkeypatch):
    """Two requests pass every check together. Only one may win the atomic 'mark as used'."""
    approval_id = make_approval()
    monkeypatch.setattr(repository, "mark_approval_used", lambda approval_id, used_at: False)
    assert_refused(run(approval_id), "ALREADY_USED", paypal)


def test_a_paypal_failure_is_recorded_and_the_approval_stays_used(paypal):
    approval_id = make_approval()
    paypal.create_error = paypal_service.PayPalError("sandbox is down")
    result = run(approval_id)
    assert not result.ok and result.code == "PAYPAL_FAILED"
    order = repository.get_order(result.order_id)
    assert order["status"] == "FAILED" and "sandbox is down" in order["note"]
    assert repository.get_approval(approval_id)["used_at"] is not None     # the person must approve again
    assert limits.spent_last_24h(NOW) == 0                                 # a failed order uses no cap
    assert any(row["action"] == "order_failed" for row in repository.list_logs())


# ---------- a signed payload that does not add up ----------

def store_signed(approval_id, drop=(), **changes):
    """Simulate a bug elsewhere: a correctly SIGNED approval whose contents are inconsistent."""
    payload = {
        "items": [{"product_id": "demo-bud-04", "title": "EchoTone Basic Buds",
                   "domain": "budget-store.example", "price_cents": 1499}],
        "total_cents": 1499, "budget_cents": 5000, "source": "demo", "nonce": "n",
        "expires_at": NOW + 300, "step_up_required": False, "step_up_reasons": [],
    }
    payload.update(changes)
    for key in drop:
        del payload[key]
    text = approvals._canonical(payload)
    repository.insert_approval(approval_id, text, approvals._sign(text), NOW, NOW + 300)


BAD_ITEM = {"product_id": "demo-bud-04", "title": "x", "domain": "budget-store.example"}


@pytest.mark.parametrize("changes", [
    {"total_cents": 100},                                                   # total does not match the items
    {"items": [], "total_cents": 0},                                        # nothing to buy
    {"items": [dict(BAD_ITEM, price_cents=0)], "total_cents": 0},           # a free item
    {"items": [dict(BAD_ITEM, price_cents=-5)], "total_cents": -5},         # a negative price
    {"items": [dict(BAD_ITEM, price_cents="1499")], "total_cents": 1499},   # a price that is text
    {"budget_cents": 1000},                                                 # total above the budget
    {"total_cents": True},                                                  # not a real number
])
def test_an_inconsistent_signed_payload_is_refused(paypal, changes):
    store_signed("forged-1", **changes)
    assert_refused(run("forged-1"), "BAD_PAYLOAD", paypal)


def test_a_signed_payload_without_a_step_up_flag_is_treated_as_needing_step_up(paypal):
    """If the flag is missing we cannot tell, so the safe answer is: require the typed confirmation."""
    store_signed("forged-2", drop=("step_up_required",))
    assert_refused(run("forged-2"), "STEP_UP_REQUIRED", paypal)


# ---------- finishing the payment ----------

def created_order(paypal):
    result = run(make_approval())
    assert result.ok
    return result.order_id


def test_complete_order_captures_with_the_same_idempotency_key(paypal):
    order_id = created_order(paypal)
    approval_id = repository.get_order(order_id)["approval_id"]
    result = checkout.complete_order(order_id, now=NOW + 10)
    assert result.ok and result.code == "CAPTURED"
    assert paypal.capture_calls == [{"order_id": "PP-ORDER-1", "key": approval_id}]
    order = repository.get_order(order_id)
    assert (order["status"], order["capture_id"], order["captured_at"]) == ("CAPTURED", "CAP-1", NOW + 10)
    assert limits.spent_last_24h(NOW + 10) == 1499


def test_a_buyer_who_has_not_approved_yet_can_try_again_later(paypal):
    order_id = created_order(paypal)
    paypal.capture_error = paypal_service.PayPalError("not approved", issue="ORDER_NOT_APPROVED")
    waiting = checkout.complete_order(order_id, now=NOW + 5)
    assert not waiting.ok and waiting.code == "WAITING_APPROVAL"
    assert status_of(order_id) == "CREATED"

    paypal.capture_error = None
    assert checkout.complete_order(order_id, now=NOW + 20).ok
    assert status_of(order_id) == "CAPTURED"


def test_another_paypal_error_keeps_the_order_open_for_a_retry(paypal):
    order_id = created_order(paypal)
    paypal.capture_error = paypal_service.PayPalError("timeout")
    result = checkout.complete_order(order_id, now=NOW + 5)
    assert not result.ok and result.code == "CAPTURE_FAILED"
    assert status_of(order_id) == "CREATED"


def test_a_capture_that_is_not_completed_fails_the_order(paypal):
    order_id = created_order(paypal)
    paypal.capture_status = "VOIDED"
    result = checkout.complete_order(order_id, now=NOW + 5)
    assert not result.ok and result.code == "CAPTURE_FAILED"
    order = repository.get_order(order_id)
    assert order["status"] == "FAILED" and "VOIDED" in order["note"]


def test_a_second_capture_is_refused_without_calling_paypal(paypal):
    order_id = created_order(paypal)
    assert checkout.complete_order(order_id, now=NOW + 5).ok
    again = checkout.complete_order(order_id, now=NOW + 6)
    assert not again.ok and again.code == "BAD_STATE"
    assert len(paypal.capture_calls) == 1


def test_completing_a_missing_order_is_refused(paypal):
    assert checkout.complete_order(9999).code == "NO_ORDER"
    assert paypal.capture_calls == []


def test_the_state_machine_cannot_be_bypassed(paypal, monkeypatch):
    """If the state machine refuses the move, the order must NOT be forced into the new status."""
    order_id = created_order(paypal)

    def refuse(*args, **kwargs):
        raise orders.InvalidMove("someone else already finished this order")
    monkeypatch.setattr(orders, "move_order", refuse)
    result = checkout.complete_order(order_id, now=NOW + 5)
    assert not result.ok
    assert status_of(order_id) == "CREATED"


def test_a_capture_for_the_wrong_amount_is_flagged(paypal):
    order_id = created_order(paypal)
    paypal.capture_amount = 1                                              # PayPal reports $0.01
    result = checkout.complete_order(order_id, now=NOW + 5)
    assert result.ok                                                       # the money did move
    assert "MISMATCH" in repository.get_order(order_id)["note"]
    assert any(row["action"] == "capture_amount_mismatch" for row in repository.list_logs())


# ---------- cancelling ----------

def test_cancelling_an_open_order_frees_the_daily_cap(paypal):
    order_id = created_order(paypal)
    assert limits.spent_last_24h(NOW) == 1499
    result = checkout.cancel_order(order_id)
    assert result.ok and status_of(order_id) == "CANCELLED"
    assert limits.spent_last_24h(NOW) == 0
    assert any(row["action"] == "order_cancelled" for row in repository.list_logs())


def test_a_captured_order_cannot_be_cancelled(paypal):
    order_id = created_order(paypal)
    checkout.complete_order(order_id, now=NOW + 5)
    result = checkout.cancel_order(order_id)
    assert not result.ok and result.code == "BAD_STATE"
    assert status_of(order_id) == "CAPTURED"


def test_cancelling_a_missing_order_is_refused(paypal):
    assert checkout.cancel_order(9999).code == "NO_ORDER"