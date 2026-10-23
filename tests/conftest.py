import pytest
import requests

from backend import config
from backend.services import catalog_demo, paypal_service


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")
        self.content = b"x" if payload is not None else b""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture(autouse=True)
def safe_defaults(monkeypatch):
    """Every test starts with fake keys and NO network. A test that forgets to mock fails loudly."""
    monkeypatch.setattr(config, "CHANNEL3_API_KEY", "test-channel3-key")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "test-id")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", False)
    monkeypatch.setattr(config, "CATALOG_FALLBACK", True)
    monkeypatch.setattr(config, "CURRENCY", "USD")
    monkeypatch.setattr(config, "MAX_BUDGET_CENTS", 50000)

    def no_network(*args, **kwargs):
        raise AssertionError("A test tried to use the real network. Mock requests.request.")
    monkeypatch.setattr(requests, "request", no_network)
    paypal_service.clear_token_cache()
    catalog_demo.clear_price_drift()
    yield
    paypal_service.clear_token_cache()
    catalog_demo.clear_price_drift()


@pytest.fixture
def fake_http(monkeypatch):
    """Record every request and answer from a handler: handler(method, url, kwargs) -> FakeResponse."""
    calls = []
    state = {"handler": lambda method, url, kwargs: FakeResponse(200, {})}

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return state["handler"](method, url, kwargs)

    monkeypatch.setattr(requests, "request", fake_request)

    class Http:
        def __init__(self):
            self.calls = calls

        def respond(self, handler):
            state["handler"] = handler

    return Http()
