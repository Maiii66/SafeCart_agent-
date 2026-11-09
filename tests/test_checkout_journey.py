import pytest
import requests
from fastapi.testclient import TestClient
from urllib.parse import urlsplit

from backend import config
from backend.main import app
from backend.services import paypal_service
from database import connection, repository
from frontend import checkout_flow as flow
from frontend import client as api_client

API_KEY = "k" * 40
NO_STEP_UP_PRODUCT = "demo-bud-04"
STEP_UP_PRODUCT = "demo-bud-01"


@pytest.fixture
def journey_env(monkeypatch, tmp_path, safe_defaults):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", True)
    monkeypatch.setattr(config, "SIGNING_SECRET", "a" * 64)
    monkeypatch.setattr(config, "API_KEY", API_KEY)
    monkeypatch.setattr(config, "PAYPAL_ENV", "sandbox")
    monkeypatch.setattr(config, "STEP_UP_THRESHOLD_CENTS", 2500)
    monkeypatch.setattr(config, "DAILY_CAP_CENTS", 10000)
    monkeypatch.setattr(config, "MAX_ORDERS_PER_HOUR", 3)
    monkeypatch.setattr(config, "APPROVAL_TTL_SECONDS", 300)
    monkeypatch.setattr(
        config, "TRUSTED_MERCHANTS", ("demo-shop.example", "budget-store.example")
    )
    connection.init_db()
    return TestClient(app, headers={"X-API-Key": API_KEY})


class FakePayPal:
    def __init__(self):
        self.create_calls = []
        self.capture_calls = []
        self.get_order_calls = []
        self.get_order_error = paypal_service.PayPalError("status unavailable")
        self._totals = {}

    def create_order(self, items, idempotency_key, reference_id="safecart-order"):
        total = sum(item.unit_cents * item.quantity for item in items)
        paypal_order_id = f"PP-JOURNEY-{len(self.create_calls) + 1}"
        self.create_calls.append(
            {
                "items": list(items),
                "idempotency_key": idempotency_key,
                "reference_id": reference_id,
            }
        )
        self._totals[paypal_order_id] = total
        return paypal_service.OrderResult(
            paypal_order_id,
            "PAYER_ACTION_REQUIRED",
            f"https://sandbox.paypal.com/pay/{paypal_order_id}",
            total,
        )

    def capture_order(self, order_id, idempotency_key):
        self.capture_calls.append(
            {"order_id": order_id, "idempotency_key": idempotency_key}
        )
        return paypal_service.CaptureResult(
            order_id,
            "COMPLETED",
            f"CAP-{order_id}",
            self._totals.get(order_id, 0),
        )

    def get_order(self, order_id):
        self.get_order_calls.append(order_id)
        if self.get_order_error:
            raise self.get_order_error
        return paypal_service.OrderInfo(
            order_id,
            "APPROVED",
            amount_cents=self._totals.get(order_id, 0),
        )


@pytest.fixture
def paypal(monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(paypal_service, "create_order", fake.create_order)
    monkeypatch.setattr(paypal_service, "capture_order", fake.capture_order)
    monkeypatch.setattr(paypal_service, "get_order", fake.get_order)
    return fake


class Transport:
    """Route the frontend's requests through the real FastAPI app without any sockets."""

    def __init__(self, test_client):
        self.test_client = test_client
        self.calls = []
        self.fail_paths = set()

    def __call__(self, method, url, headers=None, json=None, timeout=None, **kwargs):
        path = urlsplit(url).path
        self.calls.append({"method": method, "url": url, "path": path, "json": json})
        if any(fragment in path for fragment in self.fail_paths):
            raise requests.Timeout("simulated timeout")
        return self.test_client.request(method, path, headers=headers, json=json)


@pytest.fixture
def transport(monkeypatch, journey_env):
    routed = Transport(journey_env)
    monkeypatch.setattr(requests, "request", routed)
    return routed


def _search(client=api_client):
    data, error = client.search_products("wireless earbuds", 5000)
    assert error == ""
    assert isinstance(data, dict)
    assert data["source"] == "demo"
    return data


def _product(data, product_id):
    return next(product for product in data["products"] if product["id"] == product_id)


def _cart_for(data, product_id):
    product = _product(data, product_id)
    offer = product["ranking"]["offer"]
    return [
        {
            "product_id": product["id"],
            "title": product["title"],
            "domain": offer["domain"],
            "price_cents": offer["price_cents"],
            "url": offer["url"],
            "image_url": product["image_url"],
            "source": data["source"],
        }
    ]


def _approve(cart, budget=5000):
    candidates, source, error = flow.build_candidates(cart)
    assert error == ""
    approval, error = api_client.create_approval(candidates, budget, source)
    assert error == ""
    assert approval["ok"] is True, approval
    assert flow.approval_matches_cart(approval["payload"], cart, budget, source)
    return approval, source


def test_search_approve_checkout_capture_clears_cart_only_after_captured(transport, paypal):
    data = _search()
    cart = _cart_for(data, NO_STEP_UP_PRODUCT)

    approval, source = _approve(cart)
    assert approval["payload"]["step_up_required"] is False

    checkout, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    assert checkout["ok"] is True
    assert flow.is_safe_paypal_sandbox_url(checkout["approve_url"])
    order_id = checkout["order_id"]

    order, error = api_client.get_order(order_id)
    assert error == ""
    assert order["status"] == "CREATED"
    assert flow.is_captured_order(order) is False
    assert flow.should_clear_cart(order["status"]) is False

    captured, error = api_client.complete_order(order_id)
    assert error == ""
    assert captured["ok"] is True
    assert captured["code"] == "CAPTURED"

    final, error = api_client.get_order(order_id)
    assert error == ""
    assert flow.is_captured_order(final) is True
    assert flow.should_clear_cart(final["status"]) is True
    assert final["capture_id"] == f"CAP-{final['paypal_order_id']}"
    assert paypal.capture_calls


def test_a_paypal_return_can_find_the_order_and_confirm_it(transport, paypal):
    data = _search()
    cart = _cart_for(data, NO_STEP_UP_PRODUCT)
    approval, _ = _approve(cart)

    checkout, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    order_id = checkout["order_id"]

    order, error = api_client.get_order(order_id)
    assert error == ""
    returned, error = api_client.get_order_by_paypal(order["paypal_order_id"])
    assert error == ""
    assert returned["id"] == order_id
    assert returned["status"] == "CREATED"

    confirmed, error = api_client.complete_order(returned["id"])
    assert error == ""
    assert confirmed["code"] == "CAPTURED"

    final, error = api_client.get_order_by_paypal(order["paypal_order_id"])
    assert error == ""
    assert flow.is_captured_order(final) is True


def test_a_paypal_cancel_can_find_the_order_and_cancel_it(transport, paypal):
    data = _search()
    cart = _cart_for(data, NO_STEP_UP_PRODUCT)
    approval, _ = _approve(cart)

    checkout, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    order, error = api_client.get_order(checkout["order_id"])
    assert error == ""

    cancelled, error = api_client.cancel_order(order["id"])
    assert error == ""
    assert cancelled["ok"] is True

    final, error = api_client.get_order_by_paypal(order["paypal_order_id"])
    assert error == ""
    assert final["status"] == "CANCELLED"
    assert paypal.capture_calls == []


def test_step_up_is_required_for_an_expensive_signed_approval(transport, paypal):
    data = _search()
    cart = _cart_for(data, STEP_UP_PRODUCT)

    approval, _ = _approve(cart)
    assert approval["payload"]["step_up_required"] is True
    assert approval["payload"]["step_up_reasons"]

    refused, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    assert refused["ok"] is False
    assert refused["code"] == "STEP_UP_REQUIRED"
    assert paypal.create_calls == []

    stepped, error = api_client.confirm_step_up(approval["approval_id"], "CONFIRM")
    assert error == ""
    assert stepped["ok"] is True

    checkout, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    assert checkout["ok"] is True
    assert len(paypal.create_calls) == 1


def test_capture_timeout_keeps_cart_until_status_is_refetched_as_captured(transport, paypal, journey_env):
    data = _search()
    cart = _cart_for(data, NO_STEP_UP_PRODUCT)
    approval, _ = _approve(cart)

    checkout, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    order_id = checkout["order_id"]

    transport.fail_paths.add("/complete")
    result, error = api_client.complete_order(order_id)
    assert result is None
    assert "took too long" in error

    pending, error = api_client.get_order(order_id)
    assert error == ""
    assert pending["status"] == "CREATED"
    assert flow.should_clear_cart(pending["status"]) is False

    transport.fail_paths.clear()
    completed = journey_env.post(
        f"/api/orders/{order_id}/complete",
        headers={"X-API-Key": API_KEY},
    )
    assert completed.status_code == 200
    assert completed.json()["code"] == "CAPTURED"

    final, error = api_client.get_order(order_id)
    assert error == ""
    assert flow.is_captured_order(final) is True
    assert flow.should_clear_cart(final["status"]) is True


def test_checkout_timeout_is_unknown_and_does_not_reach_payment(transport, paypal):
    data = _search()
    cart = _cart_for(data, NO_STEP_UP_PRODUCT)
    approval, _ = _approve(cart)

    transport.fail_paths.add("/api/checkout")
    result, error = api_client.execute_checkout(approval["approval_id"])
    assert result is None
    assert "took too long" in error
    assert flow.should_clear_cart("UNKNOWN") is False
    assert paypal.create_calls == []

    transport.fail_paths.clear()
    checkout, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    assert checkout["ok"] is True
    assert len(paypal.create_calls) == 1


def test_replayed_approval_is_refused_and_cart_stays(transport, paypal):
    data = _search()
    cart = _cart_for(data, NO_STEP_UP_PRODUCT)
    approval, _ = _approve(cart)

    first, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    assert first["ok"] is True

    second, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    assert second["ok"] is False
    assert second["code"] == "ALREADY_USED"
    assert flow.should_clear_cart("ALREADY_USED") is False
    assert len(paypal.create_calls) == 1


def test_price_change_after_approval_is_refused_before_paypal(transport, paypal):
    from backend.services import catalog_demo

    data = _search()
    cart = _cart_for(data, NO_STEP_UP_PRODUCT)
    approval, _ = _approve(cart)

    catalog_demo.set_price_drift(NO_STEP_UP_PRODUCT, 500)

    checkout, error = api_client.execute_checkout(approval["approval_id"])
    assert error == ""
    assert checkout["ok"] is False
    assert checkout["code"] in ("PRICE_CHANGED", "OFFER_GONE", "PRICE_UNVERIFIABLE")
    assert paypal.create_calls == []
