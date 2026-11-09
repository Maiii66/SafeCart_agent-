import json

import pytest
import requests

from backend import config
from frontend import checkout_flow as flow
from frontend import client as api_client
from tests.conftest import FakeResponse


def _cart_item(product_id="p-1", domain="shop.example", source="demo", price_cents=1499):
    return {
        "product_id": product_id,
        "domain": domain,
        "source": source,
        "price_cents": price_cents,
        "title": "Test item",
        "url": "https://shop.example/item",
        "image_url": "https://shop.example/item.png",
    }


# ---------- PayPal sandbox URL validation ----------


@pytest.mark.parametrize(
    "url",
    [
        "https://sandbox.paypal.com/pay/PP-1",
        "https://www.sandbox.paypal.com/checkoutnow?token=PP-1",
        "HTTPS://SANDBOX.PAYPAL.COM/pay/PP-1",
        "https://Sandbox.PayPal.Com./pay/PP-1",
        "https://sandbox.paypal.com:443/pay/PP-1",
        "https://www.sandbox.paypal.com:443/checkoutnow?token=1",
    ],
)
def test_safe_sandbox_urls_are_accepted(url):
    assert flow.is_safe_paypal_sandbox_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        "not a url",
        "http://sandbox.paypal.com/pay/PP-1",
        "ftp://sandbox.paypal.com/pay/PP-1",
        "https://paypal.com/pay/PP-1",
        "https://www.paypal.com/pay/PP-1",
        "https://sandbox.paypal.com.evil.example/pay/PP-1",
        "https://sandbox.paypal.com@evil.example/pay/PP-1",
        "https://sandbox.paypal.com:8443/pay/PP-1",
        "https://sandbox.paypal.com:0/pay/PP-1",
        "https://sandbox.paypal.com:abc/pay/PP-1",
        "https://user:pass@sandbox.paypal.com/pay/PP-1",
        "https://sandbox.paypal.com\\@evil.example/pay/PP-1",
        "https://sandbox.paypal.com/pay PP-1",
        " https://sandbox.paypal.com/pay/PP-1",
        "https://sandbox.paypal.com/pay/PP-1 ",
        "https:///pay/PP-1",
    ],
)
def test_unsafe_or_malformed_urls_are_rejected(url):
    assert flow.is_safe_paypal_sandbox_url(url) is False


def test_normalise_hostname():
    assert flow.normalise_hostname("  Sandbox.PayPal.Com.  ") == "sandbox.paypal.com"
    assert flow.normalise_hostname(None) == ""
    assert flow.normalise_hostname("") == ""


# ---------- candidates and approvals ----------


def test_build_candidates_returns_pairs_and_source():
    cart = [_cart_item("p-1"), _cart_item("p-2", domain="other.example")]
    candidates, source, error = flow.build_candidates(cart)
    assert error == ""
    assert source == "demo"
    assert candidates == [
        {"product_id": "p-1", "domain": "shop.example"},
        {"product_id": "p-2", "domain": "other.example"},
    ]


@pytest.mark.parametrize(
    "cart,expected",
    [
        ([], "Your cart is empty."),
        ("nope", "Your cart is empty."),
        ([{"domain": "shop.example", "source": "demo"}], "missing its product id"),
        ([{"product_id": "p-1", "source": "demo"}], "missing its merchant"),
        ([{"product_id": "p-1", "domain": "d", "source": "mystery"}], "unknown catalog source"),
        (
            [_cart_item("p-1"), _cart_item("p-1")],
            "same product cannot appear twice",
        ),
        (
            [_cart_item("p-1", source="demo"), _cart_item("p-2", source="channel3")],
            "same catalog source",
        ),
    ],
)
def test_build_candidates_refuses_bad_carts(cart, expected):
    candidates, source, error = flow.build_candidates(cart)
    assert candidates is None
    assert source == ""
    assert expected in error


def test_build_candidates_rejects_more_than_ten_items():
    cart = [_cart_item(f"p-{i}") for i in range(11)]
    candidates, source, error = flow.build_candidates(cart)
    assert candidates is None
    assert "at most 10 products" in error


def test_canonical_material_is_order_independent_and_ignores_titles():
    first = [_cart_item("p-1"), _cart_item("p-2", domain="b.example")]
    second = [
        _cart_item("p-2", domain="b.example", price_cents=1),
        _cart_item("p-1", price_cents=999),
    ]
    first_material = flow.canonical_approval_material(first, 5000, "demo")
    second_material = flow.canonical_approval_material(second, 5000, "demo")
    assert first_material == second_material
    assert flow.approval_fingerprint(first, 5000, "demo") == flow.approval_fingerprint(
        second, 5000, "demo"
    )

    material = json.loads(first_material)
    assert material == {
        "budget_cents": 5000,
        "items": [
            {"product_id": "p-1", "domain": "shop.example"},
            {"product_id": "p-2", "domain": "b.example"},
        ],
        "source": "demo",
    }


def test_fingerprint_changes_with_domain_budget_or_source():
    cart = [_cart_item()]
    base = flow.approval_fingerprint(cart, 5000, "demo")
    assert flow.approval_fingerprint([_cart_item(domain="evil.example")], 5000, "demo") != base
    assert flow.approval_fingerprint(cart, 4000, "demo") != base
    assert flow.approval_fingerprint(cart, 5000, "channel3") != base


def _payload(cart, budget, source, total_cents=1499):
    return {
        "items": [
            {
                "product_id": item["product_id"],
                "domain": item["domain"],
                "price_cents": total_cents,
                "title": item.get("title"),
            }
            for item in cart
        ],
        "budget_cents": budget,
        "source": source,
        "total_cents": total_cents,
    }


def test_approval_matches_cart_only_when_pairs_budget_and_source_match():
    cart = [_cart_item("p-1"), _cart_item("p-2", domain="b.example")]
    payload = _payload(cart, 5000, "demo")
    assert flow.approval_matches_cart(payload, cart, 5000, "demo") is True

    reordered = list(reversed(cart))
    assert flow.approval_matches_cart(payload, reordered, 5000, "demo") is True

    assert flow.approval_matches_cart(payload, cart, 4000, "demo") is False
    assert flow.approval_matches_cart(payload, cart, 5000, "channel3") is False
    assert flow.approval_matches_cart(payload, [_cart_item("p-1")], 5000, "demo") is False
    assert flow.approval_matches_cart(None, cart, 5000, "demo") is False
    assert flow.approval_matches_cart({"items": "bad"}, cart, 5000, "demo") is False


def test_purchase_review_uses_verified_prices_and_remaining_budget():
    cart = [_cart_item("p-1", price_cents=1499), _cart_item("p-2", price_cents=1599)]
    payload = {
        "items": [{"product_id": "p-1", "domain": "shop.example", "price_cents": 1499}],
        "total_cents": 1499,
        "budget_cents": 5000,
        "source": "demo",
    }
    review = flow.purchase_review(cart, 5000, 10000, "USD", payload)
    assert review["estimated_total_cents"] == 3098
    assert review["verified_total_cents"] == 1499
    assert review["remaining_budget_cents"] == 5000 - 3098
    assert review["remaining_daily_cap_cents"] == 10000
    assert review["items"][0]["verified_price_cents"] == 1499
    assert review["items"][1]["verified_price_cents"] is None
    assert review["items"][0]["quantity"] == 1


def test_cart_source_and_order_status_helpers():
    assert flow.cart_source([_cart_item("p-1")]) == "demo"
    assert flow.cart_source([]) == ""
    assert flow.cart_source([_cart_item("p-1", source="demo"), _cart_item("p-2", source="channel3")]) == ""

    assert flow.is_captured_order({"status": "CAPTURED"}) is True
    assert flow.is_captured_order({"status": "CREATED"}) is False
    assert flow.is_captured_order(None) is False
    assert flow.should_clear_cart("CAPTURED") is True
    assert flow.should_clear_cart("CREATED") is False
    assert flow.should_clear_cart("UNKNOWN") is False


def test_friendly_error_prefers_known_codes_then_fallback():
    assert flow.friendly_error("PAUSED") == flow.ERROR_MESSAGES["PAUSED"]
    assert flow.friendly_error("PAUSED", "other") == flow.ERROR_MESSAGES["PAUSED"]
    assert flow.friendly_error("MYSTERY", "Server said no") == "Server said no"
    assert "No payment was confirmed" in flow.friendly_error("MYSTERY")


def test_format_epoch():
    assert flow.format_epoch(None) == ""
    assert flow.format_epoch("123") == ""
    assert flow.format_epoch(True) == ""
    assert flow.format_epoch(0) == ""
    assert flow.format_epoch(1_700_000_000)


# ---------- HTTP client ----------


def test_api_request_sends_key_payload_and_default_timeout(fake_http, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "k" * 40)
    fake_http.respond(lambda method, url, kwargs: FakeResponse(200, {"ok": True}))

    data, error = api_client.api_request("POST", "/api/shop", {"query": "earbuds"})

    assert data == {"ok": True}
    assert error == ""
    call = fake_http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{config.BACKEND_URL.rstrip('/')}/api/shop"
    assert call["headers"]["X-API-Key"] == "k" * 40
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"] == {"query": "earbuds"}
    assert call["timeout"] == api_client.DEFAULT_TIMEOUT_SECONDS


def test_api_request_timeout_is_not_reported_as_payment_failure(fake_http):
    def timeout(method, url, kwargs):
        raise requests.Timeout("slow")

    fake_http.respond(timeout)
    data, error = api_client.api_request("POST", "/api/checkout", {})
    assert data is None
    assert "took too long" in error
    assert "No confirmed payment was made" in error


def test_api_request_network_error(fake_http):
    def refused(method, url, kwargs):
        raise requests.ConnectionError("down")

    fake_http.respond(refused)
    data, error = api_client.api_request("GET", "/api/status")
    assert data is None
    assert "Cannot reach the SafeCart backend" in error


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (401, None, "rejected the API key"),
        (503, None, "not configured"),
        (422, None, "rejected the request as invalid"),
        (500, None, "internal error"),
        (400, {"detail": "Bad thing"}, "Bad thing"),
        (404, None, "Request failed (404)."),
    ],
)
def test_api_request_maps_http_errors(fake_http, status, payload, expected):
    fake_http.respond(lambda method, url, kwargs: FakeResponse(status, payload))
    data, error = api_client.api_request("GET", "/api/orders")
    assert data is None
    assert expected in error


def test_api_request_success_without_json_returns_none_and_no_error(fake_http):
    fake_http.respond(lambda method, url, kwargs: FakeResponse(200, None))
    data, error = api_client.api_request("GET", "/api/status")
    assert data is None
    assert error == ""


def test_search_products_payload(fake_http):
    fake_http.respond(lambda method, url, kwargs: FakeResponse(200, {"products": []}))
    data, error = api_client.search_products("earbuds", 5000, limit=7)
    assert error == ""
    assert data == {"products": []}
    call = fake_http.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/shop")
    assert call["json"] == {"query": "earbuds", "max_price_cents": 5000, "limit": 7}


def test_create_approval_payload(fake_http):
    fake_http.respond(lambda method, url, kwargs: FakeResponse(200, {"ok": True}))
    candidates = [{"product_id": "p-1", "domain": "shop.example"}]
    data, error = api_client.create_approval(candidates, 5000, "demo")
    assert error == ""
    assert data == {"ok": True}
    call = fake_http.calls[0]
    assert call["url"].endswith("/api/approvals")
    assert call["json"] == {
        "candidates": candidates,
        "budget_cents": 5000,
        "source": "demo",
    }


def test_confirm_step_up_quotes_the_approval_id(fake_http):
    fake_http.respond(lambda method, url, kwargs: FakeResponse(200, {"ok": True}))
    data, error = api_client.confirm_step_up("abc/../def", "CONFIRM")
    assert error == ""
    assert data == {"ok": True}
    call = fake_http.calls[0]
    assert call["url"].endswith("/api/approvals/abc%2F..%2Fdef/step-up")
    assert call["json"] == {"typed_text": "CONFIRM"}


def test_confirm_step_up_requires_an_id(fake_http):
    data, error = api_client.confirm_step_up("", "CONFIRM")
    assert data is None
    assert "identifier is missing" in error
    assert fake_http.calls == []


def test_payment_calls_use_the_longer_timeout(fake_http):
    fake_http.respond(lambda method, url, kwargs: FakeResponse(200, {"ok": True}))

    api_client.execute_checkout("approval-1")
    api_client.complete_order(12)

    assert fake_http.calls[0]["url"].endswith("/api/checkout")
    assert fake_http.calls[0]["timeout"] == api_client.PAYMENT_TIMEOUT_SECONDS
    assert fake_http.calls[1]["url"].endswith("/api/orders/12/complete")
    assert fake_http.calls[1]["timeout"] == api_client.PAYMENT_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "order_id,expected_path",
    [
        (12, "/api/orders/12"),
        ("12", "/api/orders/12"),
        ("007", "/api/orders/7"),
    ],
)
def test_get_order_normalises_ids(fake_http, order_id, expected_path):
    fake_http.respond(lambda method, url, kwargs: FakeResponse(200, {"id": 12}))
    data, error = api_client.get_order(order_id)
    assert error == ""
    assert data == {"id": 12}
    assert fake_http.calls[0]["url"].endswith(expected_path)


@pytest.mark.parametrize("order_id", [None, "", True, False, 0, -1, "0", "-1", "abc", 1.5])
def test_get_order_rejects_bad_ids_without_a_request(fake_http, order_id):
    data, error = api_client.get_order(order_id)
    assert data is None
    assert "identifier is missing" in error
    assert fake_http.calls == []


def test_complete_order_rejects_bad_ids(fake_http):
    data, error = api_client.complete_order("nope")
    assert data is None
    assert "identifier is missing" in error
    assert fake_http.calls == []


def test_get_order_by_paypal_uses_the_lookup_route(fake_http):
    fake_http.respond(lambda method, url, kwargs: FakeResponse(200, {"id": 12}))
    data, error = api_client.get_order_by_paypal("PP-ORDER-1")
    assert error == ""
    assert data == {"id": 12}
    assert fake_http.calls[0]["method"] == "GET"
    assert fake_http.calls[0]["url"].endswith("/api/orders/by-paypal/PP-ORDER-1")


@pytest.mark.parametrize(
    "token", [None, "", "   ", "bad token", "../x", "x" * 80, 12]
)
def test_get_order_by_paypal_rejects_bad_tokens_without_a_request(fake_http, token):
    data, error = api_client.get_order_by_paypal(token)
    assert data is None
    assert "identifier is missing" in error
    assert fake_http.calls == []


def test_cancel_order_posts_to_the_cancel_route(fake_http):
    fake_http.respond(lambda method, url, kwargs: FakeResponse(200, {"ok": True}))
    data, error = api_client.cancel_order(12)
    assert error == ""
    assert data == {"ok": True}
    call = fake_http.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/orders/12/cancel")
    assert call["timeout"] == api_client.PAYMENT_TIMEOUT_SECONDS


# ---------- return and cancel redirects ----------


def test_a_paypal_return_redirect_is_recognised():
    params = {"token": "PP-ORDER-1", "PayerID": "PAYER1"}
    assert flow.is_paypal_return(params) is True
    assert flow.paypal_return_token(params) == "PP-ORDER-1"
    assert flow.is_paypal_cancel(params) is False


def test_the_return_marker_alone_is_enough():
    params = {"paypal_return": "1", "token": "PP-ORDER-1"}
    assert flow.paypal_return_token(params) == "PP-ORDER-1"
    assert flow.is_paypal_cancel(params) is False


def test_a_paypal_cancel_redirect_is_recognised():
    params = {"paypal_cancel": "1", "token": "PP-ORDER-1"}
    assert flow.is_paypal_cancel(params) is True
    assert flow.paypal_cancel_token(params) == "PP-ORDER-1"
    assert flow.is_paypal_return(params) is False


@pytest.mark.parametrize(
    "params",
    [
        None,
        {},
        {"token": "bad token", "PayerID": "PAYER1"},
        {"token": "tiny", "PayerID": "PAYER1"},
        {"token": "", "paypal_return": "1"},
        {"paypal_return": "0", "token": "PP-ORDER-1"},
        {"paypal_cancel": "1"},
    ],
)
def test_incomplete_redirects_are_ignored(params):
    assert flow.is_paypal_return(params) is False
    assert flow.is_paypal_cancel(params) is False
    assert flow.paypal_return_token(params) == ""
    assert flow.paypal_cancel_token(params) == ""


def test_query_values_may_arrive_as_lists():
    params = {"token": ["PP-ORDER-1"], "PayerID": ["PAYER1"]}
    assert flow.paypal_return_token(params) == "PP-ORDER-1"
