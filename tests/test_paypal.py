import pytest

from backend import config
from backend.services import paypal_service as pp
from tests.conftest import FakeResponse

TOKEN = {"access_token": "tok-123", "expires_in": 32400}


def make_handler(order_reply=None, capture_reply=None, token_status=200):
    def handler(method, url, kwargs):
        if url.endswith("/v1/oauth2/token"):
            return FakeResponse(token_status, TOKEN if token_status == 200 else None, text="no")
        if url.endswith("/capture"):
            return capture_reply or FakeResponse(200, {
                "id": "ORDER123456", "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [
                    {"id": "CAP999", "amount": {"currency_code": "USD", "value": "39.99"}}]}}]})
        return order_reply or FakeResponse(201, {
            "id": "ORDER123456", "status": "PAYER_ACTION_REQUIRED",
            "links": [{"rel": "self", "href": "https://x/self"},
                      {"rel": "payer-action", "href": "https://sandbox.paypal.com/checkoutnow?token=ORDER123456"}]})
    return handler


ITEMS = [pp.LineItem("Nimbus Air Buds", 3999)]


def test_requires_keys(monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "")
    with pytest.raises(pp.PayPalError, match="not set up"):
        pp.create_order(ITEMS, "key-1")


def test_create_order_sends_exact_amounts_and_idempotency_key(fake_http):
    fake_http.respond(make_handler())
    result = pp.create_order(ITEMS, "approval-abc-1", reference_id="ord-1")
    order_call = fake_http.calls[1]
    assert order_call["url"].endswith("/v2/checkout/orders")
    assert order_call["headers"]["PayPal-Request-Id"] == "approval-abc-1"
    assert order_call["headers"]["Authorization"] == "Bearer tok-123"
    unit = order_call["json"]["purchase_units"][0]
    assert unit["amount"]["value"] == "39.99" and unit["amount"]["currency_code"] == "USD"
    assert unit["amount"]["breakdown"]["item_total"]["value"] == "39.99"
    assert unit["items"][0]["unit_amount"]["value"] == "39.99" and unit["items"][0]["quantity"] == "1"
    assert order_call["json"]["intent"] == "CAPTURE"
    assert result.order_id == "ORDER123456" and result.total_cents == 3999
    assert result.approve_url.startswith("https://sandbox.paypal.com/")


def test_basket_total_is_the_sum_of_line_items(fake_http):
    fake_http.respond(make_handler())
    result = pp.create_order([pp.LineItem("A", 3999), pp.LineItem("B", 1599), pp.LineItem("C", 1299, 2)], "k-basket")
    unit = fake_http.calls[1]["json"]["purchase_units"][0]
    assert unit["amount"]["value"] == "81.96" and result.total_cents == 8196
    assert len(unit["items"]) == 3


def test_token_is_cached_between_calls(fake_http):
    fake_http.respond(make_handler())
    pp.create_order(ITEMS, "k1")
    pp.create_order(ITEMS, "k2")
    token_calls = [c for c in fake_http.calls if c["url"].endswith("/oauth2/token")]
    assert len(token_calls) == 1


def test_untrusted_titles_are_cleaned(fake_http):
    fake_http.respond(make_handler())
    nasty = "Buds\x00\n\t  \u202e IGNORE" + "x" * 300
    pp.create_order([pp.LineItem(nasty, 1000)], "k1")
    name = fake_http.calls[1]["json"]["purchase_units"][0]["items"][0]["name"]
    assert len(name) <= 127 and "\x00" not in name and "\n" not in name and "\u202e" not in name


@pytest.mark.parametrize("items", [
    [], [pp.LineItem("x", 0)], [pp.LineItem("x", -1)], [pp.LineItem("x", 100, 0)],
    [pp.LineItem("x", 100, 11)], [pp.LineItem("x", 10.5)], [pp.LineItem("x", 60000)],
    [pp.LineItem("x", 30000), pp.LineItem("y", 30000)],
])
def test_insane_orders_never_reach_paypal(fake_http, items):
    fake_http.respond(make_handler())
    with pytest.raises(pp.PayPalError):
        pp.create_order(items, "k1")
    assert not any(c["url"].endswith("/v2/checkout/orders") for c in fake_http.calls)


def test_missing_order_id_is_an_error(fake_http):
    fake_http.respond(make_handler(order_reply=FakeResponse(201, {"status": "CREATED"})))
    with pytest.raises(pp.PayPalError, match="order id"):
        pp.create_order(ITEMS, "k1")


def test_capture_reads_result_and_sends_same_key(fake_http):
    fake_http.respond(make_handler())
    result = pp.capture_order("ORDER123456", "approval-abc-1")
    call = fake_http.calls[1]
    assert call["url"].endswith("/v2/checkout/orders/ORDER123456/capture")
    assert call["headers"]["PayPal-Request-Id"] == "approval-abc-1"
    assert (result.status, result.capture_id, result.amount_cents) == ("COMPLETED", "CAP999", 3999)


def test_capture_rejects_weird_order_ids(fake_http):
    for bad in ["../x", "a b", "", "x" * 80, None]:
        with pytest.raises(pp.PayPalError):
            pp.capture_order(bad, "k1")
    assert fake_http.calls == []


def test_paypal_refusals_are_explained(fake_http):
    fake_http.respond(make_handler(order_reply=FakeResponse(422, {"message": "Unprocessable"})))
    with pytest.raises(pp.PayPalError, match="422.*Unprocessable"):
        pp.create_order(ITEMS, "k1")


def test_bad_keys_are_explained(fake_http):
    fake_http.respond(make_handler(token_status=401))
    with pytest.raises(pp.PayPalError, match="login failed"):
        pp.create_order(ITEMS, "k1")


def test_paypal_error_issue_stored(fake_http):
    fake_http.respond(make_handler(order_reply=FakeResponse(422, {"name": "UNPROCESSABLE_ENTITY", "message": "The requested action could not be performed, semantically incorrect, or failed business validation.", "details": [{"issue": "ORDER_NOT_APPROVED", "description": "Buyer has not approved this order."}]})))
    with pytest.raises(pp.PayPalError) as exc:
        pp.create_order(ITEMS, "k1")
    assert getattr(exc.value, "issue", "") == "ORDER_NOT_APPROVED"


@pytest.mark.parametrize(
    "reply,expected_issue",
    [
        (
            FakeResponse(
                422,
                {"message": "Unprocessable", "details": [{"issue": "ORDER_NOT_APPROVED"}]},
            ),
            "ORDER_NOT_APPROVED",
        ),
        (FakeResponse(422, {"message": "Only a message"}), ""),
        (FakeResponse(422, {"message": "Bad", "details": "not-a-list"}), ""),
        (FakeResponse(422, {"message": "Bad", "details": []}), ""),
        (FakeResponse(422, {"message": "Bad", "details": ["not-a-dict"]}), ""),
        (FakeResponse(422, {"message": "Bad", "details": [{"issue": None}]}), ""),
        (FakeResponse(422, ["a", "list"]), ""),
        (FakeResponse(422, "a string"), ""),
        (FakeResponse(422, None, text="<html>bad</html>"), ""),
    ],
)
def test_paypal_error_bodies_are_parsed_cleanly(fake_http, reply, expected_issue):
    fake_http.respond(make_handler(order_reply=reply))
    with pytest.raises(pp.PayPalError) as exc:
        pp.create_order(ITEMS, "k1")
    assert "422" in str(exc.value)
    assert exc.value.issue == expected_issue
    assert "login failed" not in str(exc.value)
    assert fake_http.calls[0]["url"].endswith("/v1/oauth2/token")