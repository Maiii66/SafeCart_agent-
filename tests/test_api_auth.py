import logging

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.agent import checkout as agent_checkout
from backend.main import app
from backend.services import paypal_service
from database import connection, repository
from tests.conftest import FakeResponse

API_KEY = "k" * 40
BUD = "demo-bud-04"

APPROVAL_BODY = {
    "candidates": [{"product_id": BUD}],
    "budget_cents": 5000,
    "source": "demo",
}

PROTECTED = [
    ("POST", "/api/shop", {"query": "earbuds"}),
    ("POST", "/api/approvals", APPROVAL_BODY),
    ("POST", "/api/approvals/abc/step-up", {"typed_text": "CONFIRM"}),
    ("POST", "/api/checkout", {"approval_id": "abc"}),
    ("GET", "/api/orders", None),
    ("GET", "/api/orders/1", None),
    ("POST", "/api/orders/1/complete", None),
    ("POST", "/api/orders/1/cancel", None),
    ("GET", "/api/logs", None),
    ("GET", "/api/status", None),
    ("POST", "/api/pause", None),
    ("POST", "/api/resume", None),
]

PARAM_TEMPLATES = {
    "/api/approvals/abc/step-up": "/api/approvals/{approval_id}/step-up",
    "/api/orders/1": "/api/orders/{order_id}",
    "/api/orders/1/complete": "/api/orders/{order_id}/complete",
    "/api/orders/1/cancel": "/api/orders/{order_id}/cancel",
}


def _template(path):
    return PARAM_TEMPLATES.get(path, path)


@pytest.fixture(autouse=True)
def auth_env(monkeypatch, tmp_path):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", True)
    monkeypatch.setattr(config, "SIGNING_SECRET", "a" * 64)
    monkeypatch.setattr(config, "API_KEY", API_KEY, raising=False)
    monkeypatch.setattr(config, "PAYPAL_ENV", "sandbox")
    connection.init_db()


@pytest.fixture
def client(auth_env):
    return TestClient(app, headers={"X-API-Key": API_KEY})


@pytest.fixture
def bare_client(auth_env):
    return TestClient(app)


def test_every_api_operation_is_in_protected():
    operations = {
        (method.upper(), path)
        for path, methods in app.openapi()["paths"].items()
        if path.startswith("/api")
        for method in methods
    }
    assert operations == {(method, _template(path)) for method, path, _ in PROTECTED}


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_missing_key_is_401(bare_client, method, path, body):
    response = bare_client.request(method, path, json=body)
    assert response.status_code == 401


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_wrong_key_is_401(auth_env, method, path, body):
    wrong = TestClient(app, headers={"X-API-Key": "x" * 40})
    response = wrong.request(method, path, json=body)
    assert response.status_code == 401


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_right_key_is_not_refused(client, method, path, body):
    response = client.request(method, path, json=body)
    assert response.status_code not in (401, 503)


def test_missing_key_is_checked_before_the_body(bare_client):
    response = bare_client.post("/api/checkout", json={})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "key",
    [
        API_KEY.upper(),
        API_KEY + " ",
        " " + API_KEY,
        API_KEY[:-1],
        API_KEY + "k",
        "",
    ],
)
def test_nearly_right_keys_are_401(auth_env, key):
    wrong = TestClient(app, headers={"X-API-Key": key})
    response = wrong.post("/api/shop", json={"query": "earbuds"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "url,headers",
    [
        (f"/api/shop?X-API-Key={API_KEY}", {}),
        (f"/api/shop?api_key={API_KEY}", {}),
        ("/api/shop", {"Authorization": f"Bearer {API_KEY}"}),
    ],
)
def test_key_outside_the_header_is_rejected(auth_env, url, headers):
    wrong = TestClient(app, headers=headers)
    response = wrong.post(url, json={"query": "earbuds"})
    assert response.status_code == 401


def test_health_and_root_are_open(bare_client):
    assert bare_client.get("/health").status_code == 200
    assert bare_client.get("/").status_code == 200


def test_invalid_server_key_makes_every_api_route_503(monkeypatch):
    with_key = TestClient(app, headers={"X-API-Key": API_KEY})
    without_key = TestClient(app)
    for bad in ("", "too-short", "k" * 31):
        monkeypatch.setattr(config, "API_KEY", bad)
        for method, path, body in PROTECTED:
            assert with_key.request(method, path, json=body).status_code == 503
            assert without_key.request(method, path, json=body).status_code == 503


def test_constant_time_compare_is_used(client, monkeypatch):
    import backend.api.auth as auth_module

    calls = []
    real = auth_module.hmac.compare_digest

    def spy(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(auth_module.hmac, "compare_digest", spy)
    assert client.get("/api/status").status_code == 200
    assert calls


def test_the_key_is_never_echoed(client, bare_client):
    responses = [
        client.get("/api/status"),
        client.get("/api/orders"),
        client.get("/api/logs"),
        client.get("/openapi.json"),
        client.get("/health"),
    ]
    for response in responses:
        assert API_KEY not in response.text
    refused = bare_client.get("/api/status")
    assert refused.status_code == 401
    assert API_KEY not in refused.text


def test_openapi_declares_the_api_key_header(client):
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]
    assert any(
        scheme.get("type") == "apiKey"
        and scheme.get("name") == "X-API-Key"
        and scheme.get("in") == "header"
        for scheme in schemes.values()
    )


def test_a_refused_request_changes_nothing(client, bare_client, monkeypatch):
    assert repository.get_setting("paused") is None
    assert bare_client.post("/api/pause").status_code == 401
    assert repository.get_setting("paused") is None

    paypal_calls = []
    monkeypatch.setattr(
        paypal_service,
        "create_order",
        lambda *args, **kwargs: paypal_calls.append(args),
    )
    approval = client.post("/api/approvals", json=APPROVAL_BODY).json()
    assert approval["ok"] is True
    refused = bare_client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    )
    assert refused.status_code == 401
    assert paypal_calls == []


def test_unexpected_error_is_hidden_and_logged(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(agent_checkout, "execute_checkout", boom)
    caplog.set_level(logging.INFO, logger="safecart")
    failing = TestClient(
        app, headers={"X-API-Key": API_KEY}, raise_server_exceptions=False
    )
    response = failing.post("/api/checkout", json={"approval_id": "abc"})
    assert response.status_code == 500
    data = response.json()
    assert data["detail"] == "Internal error"
    assert len(data["error_id"]) == 8
    assert "secret internal detail" not in response.text
    assert "RuntimeError" not in response.text
    assert data["error_id"] in caplog.text
    assert "secret internal detail" in caplog.text
    assert "RuntimeError" in caplog.text


def test_broken_listing_is_hidden_and_logged(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("secret listing detail")

    monkeypatch.setattr(repository, "list_orders", boom)
    caplog.set_level(logging.INFO, logger="safecart")
    failing = TestClient(
        app, headers={"X-API-Key": API_KEY}, raise_server_exceptions=False
    )
    response = failing.get("/api/orders")
    assert response.status_code == 500
    data = response.json()
    assert data["detail"] == "Internal error"
    assert len(data["error_id"]) == 8
    assert "secret listing detail" not in response.text
    assert "RuntimeError" not in response.text
    assert data["error_id"] in caplog.text
    assert "secret listing detail" in caplog.text
    assert "RuntimeError" in caplog.text


def test_shop_channel3_failure_is_502(monkeypatch, fake_http):
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", False)
    monkeypatch.setattr(config, "CATALOG_FALLBACK", False)
    fake_http.respond(lambda method, url, kwargs: FakeResponse(500, None, text="down"))
    client = TestClient(app, headers={"X-API-Key": API_KEY})
    response = client.post("/api/shop", json={"query": "earbuds"})
    assert response.status_code == 502
    assert response.json()["detail"] == "Product search is unavailable right now."


def test_shop_channel3_failure_falls_back(monkeypatch, fake_http):
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", False)
    monkeypatch.setattr(config, "CATALOG_FALLBACK", True)
    fake_http.respond(lambda method, url, kwargs: FakeResponse(500, None, text="down"))
    client = TestClient(app, headers={"X-API-Key": API_KEY})
    response = client.post("/api/shop", json={"query": "earbuds"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "demo"
    assert "unavailable" in data["note"]
