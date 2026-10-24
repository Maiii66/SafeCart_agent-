# backend/agent/checkout.py
# THE ONLY PAYMENT PATH.

import json
import time
from dataclasses import dataclass

from backend import config
from backend import guardrails
from database import repository
from backend.agent import approvals, limits, orders
from backend.services import paypal_service, catalog


@dataclass(frozen=True)
class CheckoutResult:
    ok: bool
    code: str = "OK"
    reason: str = ""
    order_id: int = None
    approve_url: str = ""


def execute_checkout(approval_id, now=None):
    now = time.time() if now is None else now
    # 1
    if not config.is_sandbox():
        repository.add_log("guardrail", "checkout_refused", "Not in sandbox", approval_id)
        return CheckoutResult(ok=False, code="NOT_SANDBOX", reason="Only sandbox allowed.")
    # 2
    if limits.is_paused():
        repository.add_log("guardrail", "checkout_refused", "Paused", approval_id)
        return CheckoutResult(ok=False, code="PAUSED", reason="System paused.")
    # 3
    v = approvals.verify_approval(approval_id, now=now)
    if isinstance(v, tuple):
        verdict, payload = v[0], v[1]
    else:
        verdict = v
        payload = None
    if not verdict.ok:
        repository.add_log("guardrail", "checkout_refused", verdict.code, approval_id)
        return CheckoutResult(ok=False, code="BAD_APPROVAL", reason=verdict.reason)
    if payload is None:
        try:
            row = repository.get_approval(approval_id)
            if row:
                payload = json.loads(row["payload"])
        except Exception:
            pass
    if not payload:
        repository.add_log("guardrail", "checkout_refused", "BAD_APPROVAL", approval_id)
        return CheckoutResult(ok=False, code="BAD_APPROVAL", reason="Missing payload.")
    # 4
    if payload.get("step_up_required"):
        row = repository.get_approval(approval_id)
        if not row or int(row.get("step_up_confirmed") or 0) != 1:
            repository.add_log("guardrail", "checkout_refused", "STEP_UP_REQUIRED", approval_id)
            return CheckoutResult(ok=False, code="STEP_UP_REQUIRED", reason="Step-up confirmation required.")
    # 5
    vcap = limits.check_daily_cap(payload.get("total_cents", 0), now=now)
    if not vcap.ok:
        repository.add_log("guardrail", "checkout_refused", vcap.code, approval_id)
        return CheckoutResult(ok=False, code=vcap.code, reason=vcap.reason)
    vhour = limits.check_hourly_limit(now=now)
    if not vhour.ok:
        repository.add_log("guardrail", "checkout_refused", vhour.code, approval_id)
        return CheckoutResult(ok=False, code=vhour.code, reason=vhour.reason)
    # 6
    source = payload.get("source", "demo")
    for it in payload.get("items", []):
        pid = it.get("product_id")
        try:
            prod = catalog.get_live_product(pid, source)
        except Exception as e:
            # check if LivePriceUnavailable
            from backend.services import catalog as catalog_mod
            if isinstance(e, catalog_mod.LivePriceUnavailable) or e.__class__.__name__ == "LivePriceUnavailable":
                repository.add_log("guardrail", "checkout_refused", "PRICE_UNVERIFIABLE", approval_id)
                return CheckoutResult(ok=False, code="PRICE_UNVERIFIABLE", reason="Price unverifiable.")
            repository.add_log("guardrail", "checkout_refused", "PRICE_UNVERIFIABLE", approval_id)
            return CheckoutResult(ok=False, code="PRICE_UNVERIFIABLE", reason="Price unverifiable.")
        # verify live offer against approved price and domain
        # build offer-like from live product
        from backend.models import Offer
        offer = Offer(
            offer_id=getattr(prod, "offer_id", pid),
            product_id=pid,
            title=prod.title,
            price_cents=int(prod.price_cents),
            currency=prod.currency,
            availability=prod.availability,
            condition=prod.condition,
            domain=getattr(prod, "domain", it.get("domain", "")),
            shipping_cents=getattr(prod, "shipping_cents", 0),
        )
        v2 = guardrails.verify_live_offer(offer, it.get("price_cents"), it.get("domain"))
        if not v2.ok:
            repository.add_log("guardrail", "checkout_refused", v2.code, approval_id)
            return CheckoutResult(ok=False, code=v2.code, reason=v2.reason)
    # 7
    used = repository.mark_approval_used(approval_id, int(now))
    if not used:
        repository.add_log("guardrail", "checkout_refused", "ALREADY_USED", approval_id)
        return CheckoutResult(ok=False, code="ALREADY_USED", reason="Approval already used.")
    # 8
    items_pp = []
    for it in payload.get("items", []):
        items_pp.append(paypal_service.LineItem(it.get("title") or it.get("product_id"), int(it.get("price_cents")), 1))
    try:
        res = paypal_service.create_order(items_pp, idempotency_key=approval_id, reference_id=approval_id)
    except paypal_service.PayPalError as e:
        order_id = repository.insert_order(approval_id, None, "FAILED", int(payload.get("total_cents", 0)), int(now), note=str(e))
        repository.add_log("agent", "order_failed", str(e), str(order_id))
        return CheckoutResult(ok=False, code="PAYPAL_FAILED", reason=str(e))
    # 9
    order_id = repository.insert_order(approval_id, getattr(res, "order_id"), "CREATED", int(payload.get("total_cents", 0)), int(now))
    repository.add_log("agent", "order_created", f"Order {order_id} created", str(order_id))
    return CheckoutResult(ok=True, code="OK", reason="created", order_id=order_id, approve_url=getattr(res, "approve_url", ""))
def complete_order(order_id, now=None):
    now = time.time() if now is None else now
    order = repository.get_order(order_id)
    if not order:
        return CheckoutResult(ok=False, code="NO_ORDER", reason="No such order")
    if order["status"] != "CREATED":
        repository.add_log("agent", "order_complete_refused", f"Order not in CREATED state: {order['status']}", str(order_id))
        return CheckoutResult(ok=False, code="BAD_STATE", reason="Order not in CREATED state")
    approval_id = order["approval_id"]
    try:
        cap = paypal_service.capture_order(order["paypal_order_id"], idempotency_key=approval_id)
    except paypal_service.PayPalError as e:
        issue = getattr(e, "issue", "") or ""
        if issue == "ORDER_NOT_APPROVED" or "NOT_APPROVED" in str(e).upper():
            repository.add_log("agent", "order_waiting_approval", str(e), str(order_id))
            return CheckoutResult(ok=False, code="WAITING_APPROVAL", reason="waiting for the buyer to approve on PayPal")
        # other error - keep order CREATED and return error so person can retry
        repository.add_log("agent", "order_capture_failed", str(e), str(order_id))
        return CheckoutResult(ok=False, code="CAPTURE_FAILED", reason=str(e))
    status = getattr(cap, "status", "") or ""
    if status == "COMPLETED":
        try:
            orders.move_order(order_id, "CAPTURED")
        except Exception:
            repository.update_order_status(order_id, "CAPTURED", captured_at=int(now), capture_id=getattr(cap, "capture_id", None))
        else:
            repository.update_order_status(order_id, "CAPTURED", captured_at=int(now), capture_id=getattr(cap, "capture_id", None))
        repository.add_log("agent", "order_captured", f"Captured {order_id}", str(order_id))
        return CheckoutResult(ok=True, code="CAPTURED", reason="captured", order_id=order_id)
    else:
        try:
            orders.move_order(order_id, "FAILED")
        except Exception:
            repository.update_order_status(order_id, "FAILED")
        repository.add_log("agent", "order_failed", f"Capture status {status}", str(order_id))
        return CheckoutResult(ok=False, code="CAPTURE_FAILED", reason=f"Capture status {status}")


def cancel_order(order_id):
    order = repository.get_order(order_id)
    if not order:
        return False
    try:
        orders.move_order(order_id, "CANCELLED")
    except Exception:
        return False
    repository.update_order_status(order_id, "CANCELLED")
    repository.add_log("agent", "order_cancelled", f"Cancelled {order_id}", str(order_id))
    return True