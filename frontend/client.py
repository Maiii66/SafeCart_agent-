from __future__ import annotations

import re
from urllib.parse import quote

import requests

from backend import config

DEFAULT_TIMEOUT_SECONDS = 10
PAYMENT_TIMEOUT_SECONDS = 45


def _headers() -> dict:
    return {"X-API-Key": config.API_KEY, "Content-Type": "application/json"}


def api_request(method: str, path: str, payload: dict | None = None, timeout=None):
    base = config.BACKEND_URL.rstrip("/")
    wait = DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        response = requests.request(
            method,
            f"{base}{path}",
            headers=_headers(),
            json=payload,
            timeout=wait,
        )
    except requests.Timeout:
        return None, (
            "The SafeCart backend took too long to respond. No confirmed payment was made. "
            "Check the order status before trying again."
        )
    except requests.RequestException:
        return None, (
            "Cannot reach the SafeCart backend. Start it with: "
            "uvicorn backend.main:app --reload"
        )

    if response.status_code == 401:
        return None, "The backend rejected the API key. Check the server configuration."
    if response.status_code == 503:
        return None, "The backend API key is not configured on the server."

    try:
        data = response.json()
    except ValueError:
        data = None

    if response.status_code >= 400:
        if response.status_code == 422:
            if path == "/api/agent/run":
                detail = data.get("detail") if isinstance(data, dict) else None
                return None, str(detail or "The shopping agent request was invalid.")
            return None, "The backend rejected the request as invalid. Check the cart and budget."
        if response.status_code >= 500:
            return None, "The SafeCart backend hit an internal error. No confirmed payment was made."
        detail = data.get("detail") if isinstance(data, dict) else None
        return None, str(detail or f"Request failed ({response.status_code}).")

    return data, ""


def search_products(query: str, max_price_cents, limit: int = 10):
    return api_request(
        "POST",
        "/api/shop",
        {"query": query, "max_price_cents": max_price_cents, "limit": limit},
    )


def search_basket(items, total_budget_cents, limit: int = 10):
    return api_request(
        "POST",
        "/api/basket",
        {"items": list(items), "total_budget_cents": total_budget_cents, "limit": limit},
    )


def run_agent(goal: str, budget_cents=None):
    return api_request(
        "POST",
        "/api/agent/run",
        {"goal": str(goal or ""), **(
            {"budget_cents": budget_cents} if budget_cents is not None else {}
        )},
    )


def fetch_status():
    return api_request("GET", "/api/status")


def get_policy():
    return api_request("GET", "/api/policy")


def propose_policy(text):
    return api_request("POST", "/api/policy/propose", {"text": str(text or "")})


def confirm_policy(policy):
    return api_request("POST", "/api/policy/confirm", policy)


def delete_policy():
    return api_request("DELETE", "/api/policy")


def pause():
    return api_request("POST", "/api/pause", {})


def resume():
    return api_request("POST", "/api/resume", {})


def create_approval(candidates, budget_cents, source):
    return api_request(
        "POST",
        "/api/approvals",
        {"candidates": candidates, "budget_cents": budget_cents, "source": source},
    )


def confirm_step_up(approval_id, typed_text):
    approval_path = quote(str(approval_id or ""), safe="")
    if not approval_path:
        return None, "The approval identifier is missing."
    return api_request(
        "POST",
        f"/api/approvals/{approval_path}/step-up",
        {"typed_text": str(typed_text or "")},
    )


def execute_checkout(approval_id):
    return api_request(
        "POST",
        "/api/checkout",
        {"approval_id": str(approval_id or "")},
        timeout=PAYMENT_TIMEOUT_SECONDS,
    )


def _order_id_text(order_id):
    if isinstance(order_id, bool):
        return ""
    if isinstance(order_id, int) and order_id > 0:
        return str(order_id)
    if isinstance(order_id, str) and order_id.isdigit() and int(order_id) > 0:
        return str(int(order_id))
    return ""


def get_order(order_id):
    order_text = _order_id_text(order_id)
    if not order_text:
        return None, "The order identifier is missing."
    return api_request("GET", f"/api/orders/{order_text}")


def _paypal_order_id_text(paypal_order_id):
    text = str(paypal_order_id or "").strip()
    if not re.match(r"^[A-Za-z0-9_-]{5,64}$", text):
        return ""
    return text


def get_order_by_paypal(paypal_order_id):
    order_text = _paypal_order_id_text(paypal_order_id)
    if not order_text:
        return None, "The PayPal order identifier is missing."
    return api_request("GET", f"/api/orders/by-paypal/{quote(order_text, safe='')}")


def cancel_order(order_id):
    order_text = _order_id_text(order_id)
    if not order_text:
        return None, "The order identifier is missing."
    return api_request("POST", f"/api/orders/{order_text}/cancel", {}, timeout=PAYMENT_TIMEOUT_SECONDS)


def complete_order(order_id):
    order_text = _order_id_text(order_id)
    if not order_text:
        return None, "The order identifier is missing."
    return api_request(
        "POST",
        f"/api/orders/{order_text}/complete",
        {},
        timeout=PAYMENT_TIMEOUT_SECONDS,
    )
