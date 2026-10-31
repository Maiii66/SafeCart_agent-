import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.agent import approvals
from backend.main import app
from backend.services import paypal_service
from database import connection, repository

BUD = "demo-bud-04"
STEP_UP_BUD = "demo-bud-01"
CHARGER = "demo-chg-01"


@pytest.fixture(autouse=True)
def api_env(monkeypatch, tmp_path, safe_defaults):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", True)
    monkeypatch.setattr(config, "SIGNING_SECRET", "a" * 64)
    monkeypatch.setattr(config, "PAYPAL_ENV", "sandbox")
    monkeypatch.setattr(config, "STEP_UP_THRESHOLD_CENTS", 2500)
    monkeypatch.setattr(config, "DAILY_CAP_CENTS", 10000)
    monkeypatch.setattr(config, "MAX_ORDERS_PER_HOUR", 3)
    monkeypatch.setattr(config, "APPROVAL_TTL_SECONDS", 300)
    monkeypatch.setattr(
        config,
        "TRUSTED_MERCHANTS",
        ("demo-shop.example", "budget-store.example"),
    )
    connection.init_db()


@pytest.fixture
def client(api_env):
    return TestClient(app)


class FakePayPal:
    def __init__(self):
        self.create_calls = []

    def create_order(self, items, idempotency_key, reference_id="safecart-order"):
        total_in_cents = sum(item.unit_cents * item.quantity for item in items)
        self.create_calls.append(
            {
                "items": list(items),
                "idempotency_key": idempotency_key,
                "reference_id": reference_id,
            }
        )
        return paypal_service.OrderResult(
            "PP-API-1",
            "PAYER_ACTION_REQUIRED",
            "https://sandbox.paypal.com/pay/PP-API-1",
            total_in_cents,
        )


@pytest.fixture
def paypal(monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(paypal_service, "create_order", fake.create_order)
    return fake


def approve(client, product_ids, budget, source="demo"):
    candidates = [{"product_id": product_id} for product_id in product_ids]
    response = client.post(
        "/api/approvals",
        json={"candidates": candidates, "budget_cents": budget, "source": source},
    )
    assert response.status_code == 200
    return response.json()


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"name": "SafeCart API", "docs": "/docs"}


def test_openapi_includes_required_paths(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = set(response.json()["paths"])
    assert {
        "/health",
        "/api/shop",
        "/api/approvals",
        "/api/checkout",
        "/api/orders",
        "/api/logs",
    } <= paths


def test_logs_empty_table(client):
    response = client.get("/api/logs")
    assert response.status_code == 200
    assert response.json() == {"logs": []}


def test_logs_one_entry(client):
    repository.add_log("human", "approval_created", "...", "ref-1")
    response = client.get("/api/logs")
    assert response.status_code == 200
    logs = response.json()["logs"]
    assert len(logs) == 1
    log = logs[0]
    assert log["actor"] == "human"
    assert log["action"] == "approval_created"
    assert log["ref"] == "ref-1"
    assert isinstance(log["ts"], str)
    assert len(log["ts"]) >= 19


def test_logs_newest_first_and_limit(client):
    for number in range(1, 6):
        repository.add_log("agent", "test_action", f"log-{number}")
    response = client.get("/api/logs")
    assert response.status_code == 200
    logs = response.json()["logs"]
    assert [log["detail"] for log in logs] == [
        "log-5",
        "log-4",
        "log-3",
        "log-2",
        "log-1",
    ]
    response = client.get("/api/logs?limit=2")
    assert response.status_code == 200
    logs = response.json()["logs"]
    assert len(logs) == 2
    assert [log["detail"] for log in logs] == ["log-5", "log-4"]


def test_orders_empty(client):
    response = client.get("/api/orders")
    assert response.status_code == 200
    assert response.json() == {"orders": []}


def test_orders_list_all_statuses(client):
    created_id = repository.insert_order("approval-1", "PP-1", "CREATED", 1499, 1000)
    failed_id = repository.insert_order(
        "approval-2", None, "FAILED", 3999, 2000, note="PayPal refused"
    )
    captured_id = repository.insert_order(
        "approval-3",
        "PP-3",
        "CAPTURED",
        1599,
        3000,
        captured_at=3100,
        capture_id="CAP-3",
    )
    response = client.get("/api/orders")
    assert response.status_code == 200
    orders = {order["id"]: order for order in response.json()["orders"]}
    assert orders[created_id]["status"] == "CREATED"
    assert orders[failed_id]["status"] == "FAILED"
    assert orders[failed_id]["paypal_order_id"] is None
    assert orders[failed_id]["note"] == "PayPal refused"
    assert orders[captured_id]["status"] == "CAPTURED"
    assert orders[captured_id]["capture_id"] == "CAP-3"
    assert orders[captured_id]["captured_at"] == 3100


def test_get_order_by_id_and_validation(client):
    order_id = repository.insert_order("approval-1", "PP-1", "CREATED", 1499, 1000)
    response = client.get(f"/api/orders/{order_id}")
    assert response.status_code == 200
    assert response.json()["id"] == order_id
    assert client.get("/api/orders/9999").status_code == 404
    assert client.get("/api/orders/abc").status_code == 422


def test_shop_search_uses_demo_catalog(client):
    response = client.post("/api/shop", json={"query": "wireless earbuds"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "demo"
    assert len(data["products"]) >= 3
    for product in data["products"]:
        for key in ("id", "title", "description", "offers", "rating_value"):
            assert key in product
        for offer in product["offers"]:
            for key in ("domain", "price_cents", "currency", "availability"):
                assert key in offer


def test_shop_max_price_filter(client):
    response = client.post(
        "/api/shop",
        json={"query": "wireless earbuds", "max_price_cents": 2000},
    )
    assert response.status_code == 200
    products = response.json()["products"]
    assert products
    assert all(
        any(offer["price_cents"] <= 2000 for offer in product["offers"])
        for product in products
    )


def test_shop_limit_and_validation(client):
    response = client.post(
        "/api/shop",
        json={"query": "wireless earbuds", "limit": 2},
    )
    assert response.status_code == 200
    assert len(response.json()["products"]) == 2
    assert (
        client.post(
            "/api/shop",
            json={"query": "wireless earbuds", "limit": 0},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/shop",
            json={"query": "wireless earbuds", "limit": 51},
        ).status_code
        == 422
    )


def test_shop_no_matches(client):
    response = client.post("/api/shop", json={"query": "zzzzzz"})
    assert response.status_code == 200
    assert response.json()["products"] == []


def test_valid_approval_and_signature(client):
    data = approve(client, [BUD], 5000)
    assert data["ok"] is True
    assert data["approval_id"]
    assert data["payload"]["total_cents"] == 1499
    verdict, payload = approvals.verify_approval(data["approval_id"])
    assert verdict.ok is True
    assert payload["items"][0]["product_id"] == BUD


def test_basket_total(client):
    data = approve(client, [BUD, CHARGER], 10000)
    assert data["ok"] is True
    assert data["payload"]["total_cents"] == 1499 + 1599


@pytest.mark.parametrize(
    "product_ids,budget,expected_code",
    [
        (["missing-product"], 5000, "BAD_REQUEST"),
        ([], 5000, "BAD_REQUEST"),
        ([BUD], 0, "BAD_BUDGET"),
        ([BUD], -5, "BAD_BUDGET"),
        ([BUD], 1000, "OVER_BUDGET"),
    ],
)
def test_approval_refusals(client, product_ids, budget, expected_code):
    data = approve(client, product_ids, budget)
    assert data["ok"] is False
    assert data["code"] == expected_code


def test_approval_empty_body_is_422(client):
    assert client.post("/api/approvals", json={}).status_code == 422


def test_full_purchase(client, paypal):
    approval = approve(client, [BUD], 5000)
    response = client.post(
        "/api/checkout",
        json={"approval_id": approval["approval_id"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["approve_url"].startswith("https://sandbox.paypal.com/")
    assert len(paypal.create_calls) == 1
    orders_response = client.get("/api/orders")
    assert orders_response.status_code == 200
    orders = orders_response.json()["orders"]
    assert len(orders) == 1
    assert orders[0]["id"] == data["order_id"]
    assert orders[0]["status"] == "CREATED"
    assert orders[0]["total_cents"] == 1499


def test_checkout_refusals_never_call_paypal(client, paypal):
    response = client.post(
        "/api/checkout",
        json={"approval_id": "missing-approval"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["code"] == "NO_SUCH_APPROVAL"

    approval = approve(client, [STEP_UP_BUD], 5000)
    response = client.post(
        "/api/checkout",
        json={"approval_id": approval["approval_id"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["code"] == "STEP_UP_REQUIRED"
    assert paypal.create_calls == []


def test_replayed_approval_is_refused(client, paypal):
    approval = approve(client, [BUD], 5000)
    first = client.post(
        "/api/checkout",
        json={"approval_id": approval["approval_id"]},
    )
    assert first.status_code == 200
    assert first.json()["ok"] is True
    second = client.post(
        "/api/checkout",
        json={"approval_id": approval["approval_id"]},
    )
    assert second.status_code == 200
    assert second.json()["ok"] is False
    assert second.json()["code"] == "ALREADY_USED"
    assert len(paypal.create_calls) == 1


def test_checkout_empty_body_is_422(client):
    assert client.post("/api/checkout", json={}).status_code == 422


def test_full_story_logs_contain_approval_and_order_created(client, paypal):
    approval = approve(client, [BUD], 5000)
    response = client.post(
        "/api/checkout",
        json={"approval_id": approval["approval_id"]},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    logs_response = client.get("/api/logs")
    assert logs_response.status_code == 200
    actions = {log["action"] for log in logs_response.json()["logs"]}
    assert "approval_created" in actions
    assert "order_created" in actions
