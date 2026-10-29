"""Run SafeCart's safety attacks against a temporary world. No keys, no network.

Each scenario gets a fresh database, pinned safe settings, and a fake PayPal that only
counts create_order calls. Any exception is reported as ERROR, never as a refusal.
"""

import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass

from backend import config
from backend.agent import approvals, checkout, limits, step_up
from backend.models import Candidate, Offer
from backend.services import catalog, catalog_demo, paypal_service
from database import connection, repository

NOW = 1_000_000
DEFAULT_BUDGET_CENTS = 5000

_SETTINGS = {
    "SIGNING_SECRET": "s" * 64,
    "PAYPAL_ENV": "sandbox",
    "STEP_UP_THRESHOLD_CENTS": 2500,
    "DAILY_CAP_CENTS": 10000,
    "MAX_ORDERS_PER_HOUR": 3,
    "APPROVAL_TTL_SECONDS": 300,
    "MAX_BUDGET_CENTS": 50000,
    "TRUSTED_MERCHANTS": ("demo-shop.example", "budget-store.example"),
}


@dataclass(frozen=True)
class Result:
    number: int
    title: str
    expected: str
    actual: str
    reason: str
    paypal_calls: int
    expected_paypal_calls: int
    passed: bool


class _FakePayPal:
    def __init__(self):
        self.create_calls = []
        self.capture_calls = []
        self._last_total = 0

    def create_order(self, items, idempotency_key, reference_id="safecart-order"):
        self.create_calls.append({"items": list(items), "key": idempotency_key,
                                  "reference": reference_id})
        self._last_total = sum(item.unit_cents * item.quantity for item in items)
        return paypal_service.OrderResult(
            "ATTACK-PP-ORDER",
            "PAYER_ACTION_REQUIRED",
            "https://sandbox.paypal.com/checkoutnow?token=ATTACK-PP-ORDER",
            self._last_total,
        )

    def capture_order(self, order_id, idempotency_key):
        self.capture_calls.append({"order_id": order_id, "key": idempotency_key})
        return paypal_service.CaptureResult(order_id, "COMPLETED", "ATTACK-CAP",
                                            self._last_total)


@contextmanager
def fresh_world():
    saved_db_path = connection.DB_PATH
    saved_settings = {name: getattr(config, name) for name in _SETTINGS}
    saved_create = paypal_service.create_order
    saved_capture = paypal_service.capture_order
    saved_live_product = catalog.get_live_product
    fake = _FakePayPal()
    temp_dir = tempfile.mkdtemp(prefix="safecart_attacks_")
    try:
        connection.DB_PATH = os.path.join(temp_dir, "attacks.db")
        for name, value in _SETTINGS.items():
            setattr(config, name, value)
        paypal_service.create_order = fake.create_order
        paypal_service.capture_order = fake.capture_order
        catalog_demo.clear_price_drift()
        connection.init_db()
        yield fake
    finally:
        catalog_demo.clear_price_drift()
        connection.DB_PATH = saved_db_path
        for name, value in saved_settings.items():
            setattr(config, name, value)
        paypal_service.create_order = saved_create
        paypal_service.capture_order = saved_capture
        catalog.get_live_product = saved_live_product
        shutil.rmtree(temp_dir, ignore_errors=True)


def _candidate(product_id):
    product = catalog_demo.get_product(product_id)
    if product is None:
        raise RuntimeError(f"Demo product {product_id} is missing.")
    return Candidate(product, product.offers[0])


def _approve(product_id, budget_cents=DEFAULT_BUDGET_CENTS, candidate=None):
    chosen = candidate if candidate is not None else _candidate(product_id)
    result = approvals.create_approval([chosen], budget_cents, "demo", now=NOW)
    if not result.ok:
        raise RuntimeError(f"Approval setup failed: {result.code}: {result.reason}")
    return result


def _confirm(approval_id, typed_text):
    verdict = step_up.confirm_step_up(approval_id, typed_text, now=NOW)
    if not verdict.ok:
        raise RuntimeError(f"Step-up setup failed: {verdict.code}: {verdict.reason}")
    return verdict


def _normal_purchase():
    approval = _approve("demo-bud-04")
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _replay():
    approval = _approve("demo-bud-04")
    first = checkout.execute_checkout(approval.approval_id, now=NOW)
    if not first.ok:
        raise RuntimeError(f"First checkout failed: {first.code}: {first.reason}")
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _tamper_total():
    approval = _approve("demo-bud-04")
    conn = connection.get_connection()
    try:
        conn.execute(
            "UPDATE approvals SET payload = replace(payload, "
            "'\"total_cents\":1499', '\"total_cents\":1') WHERE id = ?",
            (approval.approval_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _expired():
    approval = _approve("demo-bud-04")
    return checkout.execute_checkout(approval.approval_id, now=NOW + 600)


def _price_over_budget():
    approval = _approve("demo-bud-01")
    _confirm(approval.approval_id, "CONFIRM")
    catalog_demo.set_price_drift("demo-bud-01", 2000)
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _price_changed():
    approval = _approve("demo-bud-04")
    catalog_demo.set_price_drift("demo-bud-04", 500)
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _unavailable_price(product_id, source):
    raise catalog.LivePriceUnavailable("The shop price could not be checked.")


def _price_unverifiable():
    approval = _approve("demo-bud-04")
    catalog.get_live_product = _unavailable_price
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _daily_cap():
    approval = _approve("demo-bud-04")
    repository.insert_order("cap-setup", "PP-CAP", "CAPTURED", 9000, NOW)
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _hourly_limit():
    approval = _approve("demo-bud-04")
    for index in range(3):
        repository.insert_order(f"hourly-{index}", f"PP-HOURLY-{index}", "FAILED", 100, NOW)
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _paused():
    approval = _approve("demo-bud-04")
    limits.set_paused(True)
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _live_mode():
    approval = _approve("demo-bud-04")
    config.PAYPAL_ENV = "live"
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _no_typed_confirmation():
    approval = _approve("demo-bud-01")
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _wrong_typed_confirmation():
    approval = _approve("demo-bud-01")
    return step_up.confirm_step_up(approval.approval_id, "39.98", now=NOW)


def _right_typed_confirmation():
    approval = _approve("demo-bud-01")
    _confirm(approval.approval_id, "39.99")
    return checkout.execute_checkout(approval.approval_id, now=NOW)


def _unknown_merchant():
    product = catalog_demo.get_product("demo-bud-04")
    if product is None:
        raise RuntimeError("Demo product demo-bud-04 is missing.")
    offer = Offer("evilbestbuy.com", 1000, "USD", "InStock", "new",
                  "https://evilbestbuy.com/x")
    approval = _approve("demo-bud-04", candidate=Candidate(product, offer))
    return checkout.execute_checkout(approval.approval_id, now=NOW)


SCENARIOS = [
    ("Normal $14.99 purchase", "ACCEPTED", 1, _normal_purchase),
    ("Replay the same approval", "ALREADY_USED", 1, _replay),
    ("Edit the approved total after signing", "BAD_SIGNATURE", 0, _tamper_total),
    ("Use an approval after it expired", "EXPIRED", 0, _expired),
    ("Price rises from $39.99 to $59.99 on a $50 budget", "OVER_BUDGET", 0, _price_over_budget),
    ("Price rises from $14.99 to $19.99, still in budget", "PRICE_CHANGED", 0, _price_changed),
    ("The shop's price cannot be checked", "PRICE_UNVERIFIABLE", 0, _price_unverifiable),
    ("Purchase would exceed the daily cap", "DAILY_CAP", 0, _daily_cap),
    ("Too many orders in one hour", "HOURLY_LIMIT", 0, _hourly_limit),
    ("Agent is paused", "PAUSED", 0, _paused),
    ("Someone tries live mode", "NOT_SANDBOX", 0, _live_mode),
    ("$39.99 order with no typed confirmation", "STEP_UP_REQUIRED", 0, _no_typed_confirmation),
    ("Wrong typed confirmation (39.98)", "STEP_UP_REFUSED", 0, _wrong_typed_confirmation),
    ("Right typed confirmation (39.99)", "ACCEPTED", 1, _right_typed_confirmation),
    ("Unknown merchant (evilbestbuy.com)", "STEP_UP_REQUIRED", 0, _unknown_merchant),
]


def run_scenarios():
    results = []
    for number, (title, expected, expected_calls, function) in enumerate(SCENARIOS, start=1):
        actual, reason, calls = "ERROR", "", 0
        try:
            with fresh_world() as fake:
                try:
                    outcome = function()
                finally:
                    calls = len(fake.create_calls)
                actual = "ACCEPTED" if outcome.ok else outcome.code
                reason = outcome.reason
        except Exception as error:
            actual = "ERROR"
            reason = str(error) or error.__class__.__name__
        passed = actual == expected and calls == expected_calls
        results.append(Result(
            number=number,
            title=title,
            expected=expected,
            actual=actual,
            reason=reason,
            paypal_calls=calls,
            expected_paypal_calls=expected_calls,
            passed=passed,
        ))
    return results


def main():
    results = run_scenarios()
    print("SafeCart attack scenarios (no keys, no network)")
    failed = 0
    for result in results:
        if result.actual == "ACCEPTED":
            status = "ACCEPTED"
        elif result.actual == "ERROR":
            status = "ERROR"
        else:
            status = f"REFUSED ({result.actual})"
        marker = "" if result.passed else "   <-- NOT AS EXPECTED"
        print(f"{result.number:2d}. {result.title}: {status} | "
              f"PayPal calls: {result.paypal_calls}{marker}")
        if result.actual != "ACCEPTED":
            print(f"    why: {result.reason}")
        if not result.passed:
            print(f"    expected: {result.expected} with "
                  f"{result.expected_paypal_calls} PayPal call(s)")
            failed += 1
    if failed:
        print(f"FAILED: {failed} of {len(results)} scenarios did not behave as expected.")
        return 1
    print(f"All {len(results)} scenarios behaved exactly as expected.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
