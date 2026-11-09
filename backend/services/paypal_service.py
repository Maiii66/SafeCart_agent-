# backend/services/paypal_service.py
# The ONLY file that talks to PayPal. Orders v2: create an order, the buyer approves it on
# PayPal, then we capture it. Amounts come from verified integer cents, never from AI text.

import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from backend import config
from backend.money import MoneyError, parse_dollars_to_cents, to_dollars_str

TIMEOUT = 20
MAX_QUANTITY = 10
_token_cache = {"value": None, "expires_at": 0.0}


class PayPalError(Exception):
    """PayPal is switched off, unreachable, or refused a request."""

    def __init__(self, message="", issue=""):
        super().__init__(message)
        self.issue = issue or ""


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


@dataclass(frozen=True)
class OrderInfo:
    order_id: str
    status: str
    approve_url: str = ""
    capture_id: str = ""
    amount_cents: int = 0


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


def _error_details(response):
    """Return (message, issue) from a failed response, whatever shape its body has."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:200], ""
    if not isinstance(data, dict):
        return response.text[:200], ""
    detail = data.get("message") or response.text[:200]
    issue = ""
    details = data.get("details")
    if isinstance(details, list) and details and isinstance(details[0], dict):
        issue = details[0].get("issue") or ""
    return detail, issue


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
        detail, issue = _error_details(response)
        raise PayPalError(f"PayPal refused the request ({response.status_code}): {detail}", issue=issue)
    return response.json() if response.content else {}


def _clean_name(name):
    """Product titles are untrusted internet text. Keep printable characters only, max 127."""
    cleaned = re.sub(r"[^\x20-\x7E]", " ", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned or "Item")[:127]


def _clean_id(value, fallback):
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "", str(value))[:120]
    return cleaned or fallback


def _with_marker(url, key):
    """Add one query marker to a configured URL without losing its existing query."""
    base = str(url or "").strip() or "http://localhost:8501"
    try:
        parts = urlsplit(base)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query[key] = "1"
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(query), parts.fragment))
    except (TypeError, ValueError):
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}{key}=1"


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
            "return_url": _with_marker(config.PAYPAL_RETURN_URL, "paypal_return"),
            "cancel_url": _with_marker(config.PAYPAL_CANCEL_URL, "paypal_cancel"),
        }}},
    }
    return body, total


# ---------- calls ----------

def _capture_details(data):
    """Return (capture_id, amount_cents) when PayPal includes a capture, else ('', 0)."""
    try:
        capture = data["purchase_units"][0]["payments"]["captures"][0]
        capture_id = str(capture.get("id", ""))
        amount_cents = parse_dollars_to_cents(capture["amount"]["value"])
        return capture_id, amount_cents
    except (KeyError, IndexError, TypeError, MoneyError):
        return "", 0


def _order_info(data, fallback_id):
    links = {link.get("rel"): link.get("href") for link in data.get("links", [])}
    capture_id, amount_cents = _capture_details(data)
    return OrderInfo(
        order_id=str(data.get("id", fallback_id)),
        status=str(data.get("status", "")),
        approve_url=links.get("payer-action") or links.get("approve") or "",
        capture_id=capture_id,
        amount_cents=amount_cents,
    )


def get_order(order_id):
    """Read one PayPal order so an uncertain capture can be reconciled safely."""
    if not re.match(r"^[A-Za-z0-9_-]{5,64}$", str(order_id)):
        raise PayPalError("That is not a valid PayPal order id.")
    data = _call("GET", f"/v2/checkout/orders/{order_id}")
    return _order_info(data, order_id)


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
    capture_id, amount_cents = _capture_details(data)
    return CaptureResult(order_id=str(data.get("id", order_id)), status=str(data.get("status", "")),
                         capture_id=capture_id, amount_cents=amount_cents)
