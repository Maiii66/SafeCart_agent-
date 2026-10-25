# backend/agent/checkout.py
# THE ONLY PAYMENT PATH. Nothing else in the project may start or finish a PayPal payment.
#
# execute_checkout() runs a fixed list of checks. At the first one that fails it stops, writes a
# log line, and returns a refusal WITHOUT calling PayPal. Every amount sent to PayPal comes from
# the SIGNED approval, never from live data, an AI, or whoever is calling this function.

import time
from dataclasses import dataclass

from backend import config
from backend import guardrails
from backend.agent import approvals, limits, orders
from backend.models import Offer
from backend.money import format_usd
from backend.services import catalog, paypal_service
from database import repository


@dataclass(frozen=True)
class CheckoutResult:
    ok: bool
    code: str = "OK"
    reason: str = ""
    order_id: int = None
    approve_url: str = ""


def _refuse(code, reason, approval_id):
    """Write a log line and return a refusal. Every 'no' in execute_checkout goes through here."""
    repository.add_log("guardrail", "checkout_refused", f"{code}: {reason}", approval_id)
    return CheckoutResult(ok=False, code=code, reason=reason)


def _payload_problem(payload):
    """The payload is signed, but we still insist that it adds up. Returns a reason, or None if it is fine."""
    if not isinstance(payload, dict):
        return "The approval is not readable."
    items = payload.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        return "The approval has no usable items."

    total = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("product_id") or not item.get("domain"):
            return "An item is missing its product or merchant."
        price = item.get("price_cents")
        if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
            return "An item has an invalid price."
        total += price

    stored_total = payload.get("total_cents")
    if isinstance(stored_total, bool) or stored_total != total:
        return "The total does not match the items."
    budget = payload.get("budget_cents")
    if not isinstance(budget, int) or isinstance(budget, bool) or total > budget:
        return "The total is above the budget."
    return None


def _step_up_confirmed(approval_id):
    row = repository.get_approval(approval_id)
    return bool(row) and row["step_up_confirmed"] == 1


def _check_live_price(item, source, budget_cents):
    """Compare what the person approved with the listing RIGHT NOW. If we cannot check, we refuse."""
    try:
        live_product = catalog.get_live_product(item["product_id"], source)
    except catalog.LivePriceUnavailable as error:
        return guardrails.Verdict(False, "PRICE_UNVERIFIABLE", str(error))
    approved = Offer(domain=item["domain"], price_cents=item["price_cents"], currency=config.CURRENCY,
                     availability="InStock", condition="new", url="")
    return guardrails.verify_live_offer(approved, live_product, budget_cents)


def execute_checkout(approval_id, now=None):
    """Turn a signed approval into a PayPal order, or refuse. Never raises for an expected refusal."""
    now = time.time() if now is None else now

    # 1. Sandbox only, and not paused.
    if not config.is_sandbox():
        return _refuse("NOT_SANDBOX", "SafeCart only makes payments in the PayPal sandbox.", approval_id)
    if limits.is_paused():
        return _refuse("PAUSED", "Spending is paused.", approval_id)

    # 2. The approval must be genuine, unexpired and unused, and its contents must add up.
    verdict, payload = approvals.verify_approval(approval_id, now=now)
    if not verdict.ok:
        return _refuse(verdict.code, verdict.reason, approval_id)
    problem = _payload_problem(payload)
    if problem:
        return _refuse("BAD_PAYLOAD", problem, approval_id)

    # 3. Step-up: the requirement comes from the SIGNED payload (and defaults to "required" if missing).
    if payload.get("step_up_required", True) and not _step_up_confirmed(approval_id):
        return _refuse("STEP_UP_REQUIRED", "A typed confirmation is required first.", approval_id)

    # 4. Spending limits.
    for limit in (limits.check_daily_cap(payload["total_cents"], now=now),
                  limits.check_hourly_limit(now=now)):
        if not limit.ok:
            return _refuse(limit.code, limit.reason, approval_id)

    # 5. Re-check EVERY item's live price (fail closed if it cannot be checked).
    for item in payload["items"]:
        price_check = _check_live_price(item, payload.get("source"), payload["budget_cents"])
        if not price_check.ok:
            title = item.get("title") or item["product_id"]
            return _refuse(price_check.code, f"{title}: {price_check.reason}", approval_id)

    # 6. Use the approval up BEFORE calling PayPal. Only one request can win this atomic step.
    if not repository.mark_approval_used(approval_id, int(now)):
        return _refuse("ALREADY_USED", "This approval was just used by another request.", approval_id)

    # 7. Ask PayPal for the order, using ONLY the signed items. The approval id is the idempotency key.
    line_items = [paypal_service.LineItem(item.get("title") or item["product_id"], item["price_cents"], 1)
                  for item in payload["items"]]
    total_cents = payload["total_cents"]
    try:
        created = paypal_service.create_order(line_items, idempotency_key=approval_id, reference_id=approval_id)
    except paypal_service.PayPalError as error:
        order_id = repository.insert_order(approval_id, None, "FAILED", total_cents, int(now), note=str(error))
        repository.add_log("agent", "order_failed", f"PayPal refused the order: {error}", str(order_id))
        return CheckoutResult(ok=False, code="PAYPAL_FAILED", reason=str(error), order_id=order_id)

    order_id = repository.insert_order(approval_id, created.order_id, "CREATED", total_cents, int(now))
    repository.add_log("agent", "order_created",
                       f"Order for {format_usd(total_cents)} created; waiting for the buyer to approve on PayPal",
                       str(order_id))
    return CheckoutResult(ok=True, code="OK", reason="Order created.", order_id=order_id,
                          approve_url=created.approve_url)


def _move(order, new_status, **fields):
    """Ask the state machine to move an order. Returns None on success, or a refusal. Never forces a move."""
    try:
        orders.move_order(order["id"], new_status, **fields)
    except orders.InvalidMove as error:
        repository.add_log("guardrail", "order_move_refused", str(error), str(order["id"]))
        return CheckoutResult(ok=False, code="BAD_STATE", reason=str(error), order_id=order["id"])
    return None


def complete_order(order_id, now=None):
    """Take the money, after the buyer approved the order on PayPal."""
    now = time.time() if now is None else now
    order = repository.get_order(order_id)
    if not order:
        return CheckoutResult(ok=False, code="NO_ORDER", reason="No such order.")
    ref = str(order["id"])

    if order["status"] != "CREATED":
        repository.add_log("guardrail", "order_complete_refused", f"Order is {order['status']}, not CREATED", ref)
        return CheckoutResult(ok=False, code="BAD_STATE", order_id=order["id"],
                              reason=f"This order is already {order['status']}.")

    # The SAME idempotency key as when the order was created, so a retry can never charge twice.
    try:
        capture = paypal_service.capture_order(order["paypal_order_id"], idempotency_key=order["approval_id"])
    except paypal_service.PayPalError as error:
        if error.issue == "ORDER_NOT_APPROVED":
            repository.add_log("agent", "order_waiting_approval", "The buyer has not approved on PayPal yet", ref)
            return CheckoutResult(ok=False, code="WAITING_APPROVAL", order_id=order["id"],
                                  reason="Waiting for the buyer to approve the payment on PayPal.")
        repository.add_log("agent", "order_capture_failed", str(error), ref)
        return CheckoutResult(ok=False, code="CAPTURE_FAILED", order_id=order["id"], reason=str(error))

    if capture.status != "COMPLETED":
        note = f"PayPal capture status: {capture.status}"
        failure = _move(order, "FAILED", note=note)
        if failure:
            return failure
        repository.add_log("agent", "order_failed", note, ref)
        return CheckoutResult(ok=False, code="CAPTURE_FAILED", order_id=order["id"], reason=note)

    # The money moved. It must be exactly what was approved: if not, say so loudly.
    note = None
    if capture.amount_cents != order["total_cents"]:
        note = (f"AMOUNT MISMATCH: PayPal reports {format_usd(capture.amount_cents)}, "
                f"expected {format_usd(order['total_cents'])}")
    failure = _move(order, "CAPTURED", captured_at=int(now), capture_id=capture.capture_id or None, note=note)
    if failure:
        return failure
    if note:
        repository.add_log("guardrail", "capture_amount_mismatch", note, ref)
    repository.add_log("agent", "order_captured", f"Captured order {order['id']}", ref)
    return CheckoutResult(ok=True, code="CAPTURED", reason="Payment captured.", order_id=order["id"])


def cancel_order(order_id):
    """Stop SafeCart from capturing an open order. (An order the buyer never approves expires on PayPal by itself.)"""
    order = repository.get_order(order_id)
    if not order:
        return CheckoutResult(ok=False, code="NO_ORDER", reason="No such order.")
    failure = _move(order, "CANCELLED")
    if failure:
        return failure
    repository.add_log("human", "order_cancelled", f"Cancelled order {order['id']}", str(order["id"]))
    return CheckoutResult(ok=True, code="CANCELLED", reason="Order cancelled.", order_id=order["id"])