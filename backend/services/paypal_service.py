# backend/services/paypal_service.py
# The ONLY file that talks to PayPal. Orders v2: create an order, the buyer approves it on
# PayPal, then we capture it. Amounts come from verified integer cents, never from AI text.

import re
import time
from dataclasses import dataclass

import requests

from backend import config
from backend.money import MoneyError, to_dollars_str

TIMEOUT = 20
MAX_QUANTITY = 10
_token_cache = {"value": None, "expires_at": 0.0}


class PayPalError(Exception):
    """PayPal is switched off, unreachable, or refused a request."""


@dataclass(frozen=True)
class LineItem:
    name: str
    unit_cents: int
    quantity: int = 1


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: str
    approve_url: str
    total_cents: int


@dataclass(frozen=True)
class CaptureResult:
    order_id: str
    status: str
    capture_id: str
    amount_cents: int


# ---------- plumbing ----------

def clear_token_cache():
    _token_cache.update(value=None, expires_at=0.0)


def _get_token():
    if not config.paypal_enabled():
        raise PayPalError("PayPal is not set up. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to .env.")
    if _token_cache["value"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["value"]
    try:
        response = requests.request(
            "POST", config.PAYPAL_BASE_URL + "/v1/oauth2/token",
            auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials"}, timeout=TIMEOUT)
    except requests.exceptions.RequestException as error:
        raise PayPalError(f"Could not reach PayPal: {error}") from error
    if response.status_code != 200:
        raise PayPalError(f"PayPal login failed ({response.status_code}). Check your sandbox keys.")
    data = response.json()
    _token_cache["value"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 300)) - 60
    return _token_cache["value"]


def _call(method, path, json=None, request_id=None):
    headers = {"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"}
    if request_id:
        # PayPal uses this to make a repeated request harmless: same id, same single result.
        headers["PayPal-Request-Id"] = request_id
    try:
        response = requests.request(method, config.PAYPAL_BASE_URL + path,
                                    headers=headers, json=json, timeout=TIMEOUT)
    except requests.exceptions.RequestException as error:
        raise PayPalError(f"Could not reach PayPal: {error}") from error
    if response.status_code >= 400:
        try:
            detail = response.json().get("message") or response.text[:200]
        except ValueError:
            detail = response.text[:200]
        raise PayPalError(f"PayPal refused the request ({response.status_code}): {detail}")
    return response.json() if response.content else {}


def _clean_name(name):
    """Product titles are untrusted internet text. Keep printable characters only, max 127."""
    cleaned = re.sub(r"[^\x20-\x7E]", " ", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned or "Item")[:127]


def _clean_id(value, fallback):
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "", str(value))[:120]
    return cleaned or fallback


def build_order_body(items, reference_id):
    """Returns (body, total_cents). Refuses anything that does not look like a sane order."""
    if not items:
        raise PayPalError("An order needs at least one item.")
    lines, total = [], 0
    for item in items:
        if not isinstance(item.unit_cents, int) or item.unit_cents <= 0:
            raise PayPalError("Every item needs a positive price.")
        if not isinstance(item.quantity, int) or not 1 <= item.quantity <= MAX_QUANTITY:
            raise PayPalError(f"Quantity must be between 1 and {MAX_QUANTITY}.")
        total += item.unit_cents * item.quantity
        lines.append({
            "name": _clean_name(item.name),
            "quantity": str(item.quantity),
            "unit_amount": {"currency_code": config.CURRENCY, "value": to_dollars_str(item.unit_cents)},
        })
    if total > config.MAX_BUDGET_CENTS:
        raise PayPalError("This order is above the maximum amount SafeCart will ever send to PayPal.")
    amount = {"currency_code": config.CURRENCY, "value": to_dollars_str(total)}
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": _clean_id(reference_id, "safecart-order"),
            "description": "SafeCart order (PayPal sandbox)",
            "amount": {**amount, "breakdown": {"item_total": dict(amount)}},
            "items": lines,
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "SafeCart",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
            "return_url": config.PAYPAL_RETURN_URL,
            "cancel_url": config.PAYPAL_RETURN_URL,
        }}},
    }
    return body, total


# ---------- calls ----------

def create_order(items, idempotency_key, reference_id="safecart-order"):
    """Create a PayPal order. The buyer must still approve it on PayPal (approve_url)."""
    body, total = build_order_body(items, reference_id)
    data = _call("POST", "/v2/checkout/orders", json=body,
                 request_id=_clean_id(idempotency_key, "") or None)
    links = {link.get("rel"): link.get("href") for link in data.get("links", [])}
    approve_url = links.get("payer-action") or links.get("approve") or ""
    if not data.get("id"):
        raise PayPalError("PayPal did not return an order id.")
    return OrderResult(order_id=data["id"], status=str(data.get("status", "")),
                       approve_url=approve_url, total_cents=total)


def capture_order(order_id, idempotency_key):
    """Take the money, after the buyer approved. Repeating this with the same key cannot charge twice."""
    if not re.match(r"^[A-Za-z0-9_-]{5,64}$", str(order_id)):
        raise PayPalError("That is not a valid PayPal order id.")
    data = _call("POST", f"/v2/checkout/orders/{order_id}/capture", json={},
                 request_id=_clean_id(idempotency_key, "") or None)
    capture_id, amount_cents = "", 0
    try:
        capture = data["purchase_units"][0]["payments"]["captures"][0]
        capture_id = str(capture.get("id", ""))
        from backend.money import parse_dollars_to_cents
        amount_cents = parse_dollars_to_cents(capture["amount"]["value"])
    except (KeyError, IndexError, TypeError, MoneyError):
        pass
    return CaptureResult(order_id=str(data.get("id", order_id)), status=str(data.get("status", "")),
                         capture_id=capture_id, amount_cents=amount_cents)


def get_order(order_id):
    if not re.match(r"^[A-Za-z0-9_-]{5,64}$", str(order_id)):
        raise PayPalError("That is not a valid PayPal order id.")
    return _call("GET", f"/v2/checkout/orders/{order_id}")
