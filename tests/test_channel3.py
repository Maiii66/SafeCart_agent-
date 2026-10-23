import json
from pathlib import Path

import pytest
import requests

from backend import config
from backend.services import channel3_service as c3
from tests.conftest import FakeResponse

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "channel3_search_sample.json").read_text())


def test_parses_real_response_shape():
    products, skipped = c3.parse_search_response(SAMPLE)
    assert skipped == 0 and len(products) == 3
    first = products[0]
    assert first.id == "Ec1r4E1" and first.brand == "Yamaha"
    assert first.offers[0].price_cents == 14995 and first.offers[0].currency == "USD"
    assert first.offers[0].availability == "InStock" and first.offers[0].condition == "new"
    assert (first.rating_value, first.rating_count) == (4.0, 38)
    assert first.image_url == "https://cdn.example/clean-1"        # cleaned image preferred


def test_missing_ratings_become_none_not_zero():
    products, _ = c3.parse_search_response(SAMPLE)
    assert products[1].rating_value is None and products[1].rating_count == 0
    assert products[2].image_url == ""                              # no images is fine


def test_malformed_products_are_skipped():
    data = {"products": [{"title": "no id"}, {"id": "x"}, "garbage", SAMPLE["products"][0]]}
    products, skipped = c3.parse_search_response(data)
    assert [p.id for p in products] == ["Ec1r4E1"] and skipped == 3


def test_bad_offers_are_dropped_but_product_survives():
    raw = dict(SAMPLE["products"][0])
    raw["offers"] = [{"domain": "a.com", "price": {"price": "oops", "currency": "USD"}},
                     {"domain": "b.com", "price": {"price": 10, "currency": "USD"},
                      "availability": "InStock", "condition": "new", "url": "u"}]
    product = c3.parse_product(raw)
    assert [o.domain for o in product.offers] == ["b.com"]


def test_search_sends_key_query_and_price_filter(fake_http):
    fake_http.respond(lambda m, u, k: FakeResponse(200, SAMPLE))
    products = c3.search("wireless earbuds", max_price_cents=5000, limit=5)
    call = fake_http.calls[0]
    assert call["method"] == "POST" and call["url"].endswith("/v1/search")
    assert call["headers"]["x-api-key"] == "test-channel3-key"
    assert call["json"]["query"] == "wireless earbuds" and call["json"]["limit"] == 5
    assert call["json"]["filters"]["price"] == {"min_price": 0, "max_price": 50.0}
    assert call["json"]["filters"]["availability"] == ["InStock"]
    assert len(products) == 3


def test_search_without_price_filter(fake_http):
    fake_http.respond(lambda m, u, k: FakeResponse(200, SAMPLE))
    c3.search("earbuds")
    assert "price" not in fake_http.calls[0]["json"]["filters"]


def test_needs_a_key(monkeypatch):
    monkeypatch.setattr(config, "CHANNEL3_API_KEY", "")
    with pytest.raises(c3.Channel3Error, match="not set up"):
        c3.search("earbuds")


@pytest.mark.parametrize("status,word", [(401, "key"), (403, "key"), (429, "limit"), (500, "500")])
def test_http_errors_are_explained(fake_http, status, word):
    fake_http.respond(lambda m, u, k: FakeResponse(status, None, text="boom"))
    with pytest.raises(c3.Channel3Error, match=word):
        c3.search("earbuds")


def test_network_failure(monkeypatch):
    def down(*a, **k):
        raise requests.exceptions.ConnectionError("offline")
    monkeypatch.setattr(requests, "request", down)
    with pytest.raises(c3.Channel3Error, match="Could not reach"):
        c3.search("earbuds")


def test_get_product_parses_and_checks_id(fake_http):
    fake_http.respond(lambda m, u, k: FakeResponse(200, SAMPLE["products"][0]))
    assert c3.get_product("Ec1r4E1").offers[0].price_cents == 14995
    assert fake_http.calls[0]["method"] == "GET" and fake_http.calls[0]["url"].endswith("/v1/products/Ec1r4E1")
    for bad in ["../secret", "a b", "", "x" * 100, None, "id?x=1"]:
        with pytest.raises(c3.Channel3Error):
            c3.get_product(bad)
