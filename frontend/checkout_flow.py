from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlsplit

ALLOWED_PAYPAL_SANDBOX_HOSTS = frozenset({"sandbox.paypal.com", "www.sandbox.paypal.com"})
PAYPAL_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{5,64}$")

ERROR_MESSAGES = {
    "BAD_REQUEST": "The backend rejected this request. Check the cart and budget, then try again.",
    "BAD_BUDGET": "Enter a valid purchase budget above zero and within the allowed maximum.",
    "NO_SIGNING_SECRET": "The backend is not configured to create approvals.",
    "NO_OFFER": "This product no longer has an offer to buy.",
    "DUPLICATE_PRODUCT": "The same product cannot appear twice in one purchase.",
    "CURRENCY_MISMATCH": "This listing is not priced in the supported currency.",
    "OUT_OF_STOCK": "This product is currently out of stock.",
    "NOT_NEW": "This listing is not for a new product.",
    "OVER_BUDGET": "The verified total is above your purchase budget. Review the cart or raise the budget.",
    "NO_SUCH_APPROVAL": "This approval no longer exists. Please approve the purchase again.",
    "EXPIRED": "This approval expired. Review the cart and approve again.",
    "ALREADY_USED": "This approval was already used. Start a new approval to avoid a duplicate charge.",
    "BAD_SIGNATURE": "The approval could not be verified. Please approve the purchase again.",
    "BAD_PAYLOAD": "The approval contents could not be verified. Please approve the purchase again.",
    "STEP_UP_REQUIRED": "Type the required confirmation before paying.",
    "STEP_UP_REFUSED": "That confirmation did not match. Type CONFIRM or the exact approved total.",
    "DAILY_CAP": "This purchase would exceed the daily spending cap. No payment was made.",
    "HOURLY_LIMIT": "The hourly order limit has been reached. Try again later.",
    "PAUSED": "SafeCart is paused. Resume spending before trying again.",
    "NOT_SANDBOX": "SafeCart only makes payments in the PayPal sandbox.",
    "PRICE_CHANGED": "The live price changed after you approved. Review the cart and approve again.",
    "PRICE_UNVERIFIABLE": "The live price could not be verified, so no payment was made.",
    "OFFER_GONE": "The merchant no longer lists this product.",
    "PAYPAL_FAILED": "PayPal did not create an order. No confirmed payment was made.",
    "CAPTURE_FAILED": "PayPal did not confirm the payment capture.",
    "WAITING_APPROVAL": "PayPal is still waiting for you to approve the payment.",
    "ORDER_NOT_APPROVED": "PayPal is still waiting for you to approve the payment.",
    "BAD_STATE": "The order cannot move to that state.",
    "NO_ORDER": "The backend could not find this order.",
}


def _text(value) -> str:
    return str(value or "").strip()


def normalise_hostname(hostname) -> str:
    if not hostname:
        return ""
    return hostname.strip().rstrip(".").lower()


def is_safe_paypal_sandbox_url(url) -> bool:
    if not isinstance(url, str):
        return False
    if not url or url != url.strip():
        return False
    if any(character.isspace() for character in url):
        return False
    if "\\" in url:
        return False

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return False

    if parsed.scheme.lower() != "https":
        return False
    if not hostname:
        return False
    if "@" in parsed.netloc:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if port not in (None, 443):
        return False

    host = normalise_hostname(hostname)
    if not host or host not in ALLOWED_PAYPAL_SANDBOX_HOSTS:
        return False
    if not parsed.path or not parsed.path.startswith("/"):
        return False
    return True


def _query_first(query_params, key) -> str:
    if not query_params:
        return ""
    try:
        value = query_params.get(key)
    except Exception:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return _text(value)


def _query_flag(query_params, key) -> bool:
    return _query_first(query_params, key).lower() in ("1", "true", "yes", "on")


def _valid_paypal_token(value) -> str:
    text = _text(value)
    return text if PAYPAL_ORDER_ID_RE.match(text) else ""


def paypal_return_token(query_params) -> str:
    """The PayPal order id from a real return redirect, or '' for anything else."""
    token = _valid_paypal_token(
        _query_first(query_params, "token") or _query_first(query_params, "paypal_order_id")
    )
    if not token:
        return ""
    marker = _query_flag(query_params, "paypal_return")
    payer_id = bool(_query_first(query_params, "PayerID"))
    return token if marker or payer_id else ""


def is_paypal_return(query_params) -> bool:
    return bool(paypal_return_token(query_params))


def paypal_cancel_token(query_params) -> str:
    token = _valid_paypal_token(
        _query_first(query_params, "token") or _query_first(query_params, "paypal_order_id")
    )
    if not token:
        return ""
    if is_paypal_return(query_params):
        return ""
    return token if _query_flag(query_params, "paypal_cancel") else ""


def is_paypal_cancel(query_params) -> bool:
    return bool(paypal_cancel_token(query_params))


def build_candidates(cart):
    if not isinstance(cart, list) or not cart:
        return None, "", "Your cart is empty."

    candidates = []
    sources = set()
    seen_product_ids = set()

    for item in cart:
        if not isinstance(item, dict):
            return None, "", "A cart item is invalid."
        product_id = _text(item.get("product_id"))
        domain = _text(item.get("domain"))
        source = _text(item.get("source"))

        if not product_id:
            return None, "", "A cart item is missing its product id."
        if not domain:
            return None, "", "A cart item is missing its merchant."
        if source not in ("demo", "channel3"):
            return None, "", "A cart item has an unknown catalog source."
        if product_id in seen_product_ids:
            return None, "", "The same product cannot appear twice in one purchase."

        seen_product_ids.add(product_id)
        sources.add(source)
        candidates.append({"product_id": product_id, "domain": domain})

    if len(sources) != 1:
        return None, "", "All cart items must come from the same catalog source."
    if len(candidates) > 10:
        return None, "", "An approval can contain at most 10 products."

    return candidates, sources.pop(), ""


def canonical_approval_material(cart, budget_cents, source) -> str:
    items = []
    if isinstance(cart, list):
        pairs = sorted(
            (_text(item.get("product_id")), _text(item.get("domain")))
            for item in cart
            if isinstance(item, dict)
        )
        items = [{"product_id": product_id, "domain": domain} for product_id, domain in pairs]

    material = {
        "budget_cents": budget_cents,
        "items": items,
        "source": _text(source),
    }
    return json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def approval_fingerprint(cart, budget_cents, source) -> str:
    material = canonical_approval_material(cart, budget_cents, source)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def approval_matches_cart(payload, cart, budget_cents, source) -> bool:
    if not isinstance(payload, dict):
        return False
    items = payload.get("items")
    if not isinstance(items, list):
        return False
    payload_material = canonical_approval_material(
        items, payload.get("budget_cents"), payload.get("source")
    )
    current_material = canonical_approval_material(cart, budget_cents, source)
    return payload_material == current_material


def _valid_cents(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def purchase_review(
    cart,
    budget_cents,
    remaining_daily_cap_cents=0,
    currency="USD",
    approval_payload=None,
):
    verified_items = {}
    if isinstance(approval_payload, dict):
        payload_items = approval_payload.get("items")
        if isinstance(payload_items, list):
            for item in payload_items:
                if isinstance(item, dict):
                    key = (_text(item.get("product_id")), _text(item.get("domain")))
                    verified_items[key] = item

    items = []
    estimated_total_cents = 0

    if isinstance(cart, list):
        for item in cart:
            if not isinstance(item, dict):
                continue
            product_id = _text(item.get("product_id"))
            domain = _text(item.get("domain"))
            price_cents = _valid_cents(item.get("price_cents"))
            if price_cents is not None:
                estimated_total_cents += price_cents

            verified = verified_items.get((product_id, domain))
            verified_price_cents = _valid_cents(verified.get("price_cents")) if verified else None

            items.append(
                {
                    "product_id": product_id,
                    "title": _text(item.get("title")) or "Untitled listing",
                    "domain": domain,
                    "quantity": 1,
                    "price_cents": price_cents,
                    "verified_price_cents": verified_price_cents,
                    "url": _text(item.get("url")),
                    "image_url": _text(item.get("image_url")),
                    "source": _text(item.get("source")),
                }
            )

    budget = _valid_cents(budget_cents)
    verified_total_cents = None
    if isinstance(approval_payload, dict):
        verified_total_cents = _valid_cents(approval_payload.get("total_cents"))

    remaining_budget_cents = None
    if budget is not None:
        remaining_budget_cents = max(0, budget - estimated_total_cents)

    return {
        "items": items,
        "estimated_total_cents": estimated_total_cents,
        "verified_total_cents": verified_total_cents,
        "budget_cents": budget,
        "remaining_budget_cents": remaining_budget_cents,
        "remaining_daily_cap_cents": _valid_cents(remaining_daily_cap_cents),
        "currency": _text(currency) or "USD",
    }


def cart_source(cart) -> str:
    if not isinstance(cart, list):
        return ""
    sources = {
        _text(item.get("source"))
        for item in cart
        if isinstance(item, dict) and _text(item.get("source"))
    }
    return sources.pop() if len(sources) == 1 else ""


def is_captured_order(order) -> bool:
    return isinstance(order, dict) and _text(order.get("status")) == "CAPTURED"


def should_clear_cart(order_status) -> bool:
    return _text(order_status) == "CAPTURED"


def friendly_error(code, fallback="") -> str:
    key = _text(code)
    if key in ERROR_MESSAGES:
        return ERROR_MESSAGES[key]
    return _text(fallback) or "The request could not be completed. No payment was confirmed."


def format_epoch(value) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return ""
    try:
        return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (OSError, OverflowError, ValueError):
        return ""
