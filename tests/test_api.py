import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.agent import approvals
from backend.main import app
from backend.services import paypal_service
from database import connection, repository
from tests.conftest import FakeResponse

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


# ---------- helpers and fakes for the new endpoints ----------


class FakePayPalFull:
    """Fake create and capture, so API tests never touch PayPal."""

    def __init__(self):
        self.create_calls = []
        self.capture_calls = []
        self.capture_error = None
        self._last_total = 0

    def create_order(self, items, idempotency_key, reference_id="safecart-order"):
        self.create_calls.append(
            {
                "items": list(items),
                "idempotency_key": idempotency_key,
                "reference_id": reference_id,
            }
        )
        self._last_total = sum(item.unit_cents * item.quantity for item in items)
        return paypal_service.OrderResult(
            "PP-API-FULL",
            "PAYER_ACTION_REQUIRED",
            "https://sandbox.paypal.com/pay/PP-API-FULL",
            self._last_total,
        )

    def capture_order(self, order_id, idempotency_key):
        self.capture_calls.append(
            {"order_id": order_id, "idempotency_key": idempotency_key}
        )
        if self.capture_error is not None:
            raise self.capture_error
        return paypal_service.CaptureResult(
            order_id, "COMPLETED", "CAP-API-1", 1499
        )


@pytest.fixture
def paypal_full(monkeypatch):
    fake = FakePayPalFull()
    monkeypatch.setattr(paypal_service, "create_order", fake.create_order)
    monkeypatch.setattr(paypal_service, "capture_order", fake.capture_order)
    return fake


def _post_approval(client, candidates, budget=5000, source="demo"):
    return client.post(
        "/api/approvals",
        json={
            "candidates": candidates,
            "budget_cents": budget,
            "source": source,
        },
    )


# ---------- Part A: the server decides the safety flags ----------


def test_flags_field_is_rejected(client):
    for flags in ({}, {"demo-bud-04": ("suspicious_price",)}):
        response = client.post(
            "/api/approvals",
            json={
                "candidates": [{"product_id": BUD}],
                "budget_cents": 5000,
                "flags": flags,
            },
        )
        assert response.status_code == 422


def test_unknown_fields_are_rejected(client):
    assert (
        client.post(
            "/api/shop", json={"query": "earbuds", "surprise": 1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/checkout", json={"approval_id": "x", "surprise": 1}
        ).status_code
        == 422
    )


def test_no_server_flags_means_no_step_up(client):
    data = approve(client, [BUD], 5000)
    assert data["ok"] is True
    assert data["payload"]["step_up_required"] is False


def test_server_flags_drive_step_up(client, monkeypatch):
    from backend.agent import risk_flags

    monkeypatch.setattr(
        risk_flags,
        "compute_flags",
        lambda candidates, source: {BUD: ("suspicious_price",)},
    )
    data = approve(client, [BUD], 5000)
    assert data["ok"] is True
    assert data["payload"]["step_up_required"] is True
    assert "suspicious_price" in data["payload"]["step_up_reasons"]


def test_server_computes_flags_from_candidates(client, monkeypatch):
    from backend.agent import risk_flags

    seen = {}

    def fake_compute(candidates, source):
        seen["ids"] = [candidate.product.id for candidate in candidates]
        seen["source"] = source
        return {}

    monkeypatch.setattr(risk_flags, "compute_flags", fake_compute)
    data = approve(client, [BUD, CHARGER], 10000)
    assert data["ok"] is True
    assert seen == {"ids": [BUD, CHARGER], "source": "demo"}


def test_unknown_source_is_rejected(client):
    response = _post_approval(
        client, [{"product_id": BUD}], source="mystery"
    )
    assert response.status_code == 422


def test_preferred_domain_must_match_exactly(client):
    response = _post_approval(
        client, [{"product_id": STEP_UP_BUD, "domain": "budget-store.example"}]
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    item = data["payload"]["items"][0]
    assert item["domain"] == "budget-store.example"
    assert item["price_cents"] == 4299

    response = _post_approval(
        client, [{"product_id": BUD, "domain": "demo-shop.example"}]
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["code"] == "BAD_REQUEST"


def test_channel3_approval_uses_live_product(client, fake_http):
    import json
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "channel3_search_sample.json"
    first_product = json.loads(fixture.read_text(encoding="utf-8"))["products"][0]
    fake_http.respond(
        lambda method, url, kwargs: FakeResponse(200, first_product)
    )
    response = _post_approval(
        client,
        [{"product_id": "Ec1r4E1"}],
        budget=20000,
        source="channel3",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["payload"]["source"] == "channel3"
    assert data["payload"]["items"][0]["price_cents"] == 14995
    assert data["payload"]["step_up_required"] is True


def test_channel3_failure_is_bad_request(client, fake_http):
    fake_http.respond(
        lambda method, url, kwargs: FakeResponse(500, None, text="down")
    )
    response = _post_approval(
        client,
        [{"product_id": "Ec1r4E1"}],
        budget=20000,
        source="channel3",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["code"] == "BAD_REQUEST"


# ---------- Part B: new endpoints and input limits ----------


def test_step_up_endpoint_unlocks_checkout(client, paypal):
    approval = approve(client, [STEP_UP_BUD], 5000)
    first = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    )
    assert first.json()["ok"] is False
    assert first.json()["code"] == "STEP_UP_REQUIRED"

    response = client.post(
        f"/api/approvals/{approval['approval_id']}/step-up",
        json={"typed_text": "CONFIRM"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    second = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    )
    assert second.json()["ok"] is True


def test_step_up_accepts_dollar_amount(client):
    approval = approve(client, [STEP_UP_BUD], 5000)
    response = client.post(
        f"/api/approvals/{approval['approval_id']}/step-up",
        json={"typed_text": "$39.99"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "code": "OK",
        "reason": "Step-up confirmed.",
    }


def test_wrong_step_up_is_refused_and_unknown_approval_is_reported(client):
    approval = approve(client, [STEP_UP_BUD], 5000)
    response = client.post(
        f"/api/approvals/{approval['approval_id']}/step-up",
        json={"typed_text": "39.98"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["code"] == "STEP_UP_REFUSED"

    missing = client.post(
        "/api/approvals/nope/step-up", json={"typed_text": "CONFIRM"}
    )
    assert missing.status_code == 200
    assert missing.json()["ok"] is False
    assert missing.json()["code"] == "NO_SUCH_APPROVAL"


def test_step_up_input_validation(client):
    approval = approve(client, [STEP_UP_BUD], 5000)
    too_long = client.post(
        f"/api/approvals/{approval['approval_id']}/step-up",
        json={"typed_text": "x" * 41},
    )
    assert too_long.status_code == 422
    empty = client.post(
        f"/api/approvals/{approval['approval_id']}/step-up", json={}
    )
    assert empty.status_code == 422


def test_complete_order_captures_and_cannot_repeat(client, paypal_full):
    approval = approve(client, [BUD], 5000)
    created = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    ).json()
    order_id = created["order_id"]

    done = client.post(f"/api/orders/{order_id}/complete")
    assert done.status_code == 200
    assert done.json()["ok"] is True
    assert done.json()["code"] == "CAPTURED"

    detail = client.get(f"/api/orders/{order_id}")
    assert detail.json()["status"] == "CAPTURED"

    again = client.post(f"/api/orders/{order_id}/complete")
    assert again.status_code == 200
    assert again.json()["ok"] is False
    assert again.json()["code"] == "BAD_STATE"
    assert len(paypal_full.capture_calls) == 1


def test_complete_waits_for_buyer_approval(client, paypal_full):
    approval = approve(client, [BUD], 5000)
    created = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    ).json()
    paypal_full.capture_error = paypal_service.PayPalError(
        "buyer has not approved", issue="ORDER_NOT_APPROVED"
    )
    response = client.post(f"/api/orders/{created['order_id']}/complete")
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["code"] == "WAITING_APPROVAL"
    detail = client.get(f"/api/orders/{created['order_id']}")
    assert detail.json()["status"] == "CREATED"


def test_missing_and_bad_order_ids(client):
    for action in ("complete", "cancel"):
        response = client.post(f"/api/orders/9999/{action}")
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert response.json()["code"] == "NO_ORDER"
        assert client.post(f"/api/orders/abc/{action}").status_code == 422


def test_cancel_open_order_frees_daily_cap(client, paypal):
    approval = approve(client, [BUD], 5000)
    created = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    ).json()

    before = client.get("/api/status").json()
    assert before["spent_last_24h_cents"] == 1499

    cancelled = client.post(f"/api/orders/{created['order_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["ok"] is True
    assert cancelled.json()["code"] == "CANCELLED"

    after = client.get("/api/status").json()
    assert after["spent_last_24h_cents"] == 0


def test_cancel_captured_order_is_refused(client, paypal_full):
    approval = approve(client, [BUD], 5000)
    created = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    ).json()
    assert (
        client.post(f"/api/orders/{created['order_id']}/complete").json()["ok"]
        is True
    )

    response = client.post(f"/api/orders/{created['order_id']}/cancel")
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["code"] == "BAD_STATE"


def test_status_on_fresh_database(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["paused"] is False
    assert data["sandbox"] is True
    assert data["paypal_env"] == "sandbox"
    assert data["daily_cap_cents"] == 10000
    assert data["spent_last_24h_cents"] == 0
    assert data["remaining_daily_cap_cents"] == 10000
    assert data["max_orders_per_hour"] == 3
    assert data["step_up_threshold_cents"] == 2500
    for key in (
        "max_budget_cents",
        "currency",
        "channel3_enabled",
        "paypal_enabled",
        "gemini_enabled",
    ):
        assert key in data


def test_status_after_purchase(client, paypal):
    approval = approve(client, [BUD], 5000)
    assert (
        client.post(
            "/api/checkout", json={"approval_id": approval["approval_id"]}
        ).json()["ok"]
        is True
    )
    data = client.get("/api/status").json()
    assert data["spent_last_24h_cents"] == 1499
    assert data["remaining_daily_cap_cents"] == 8501


def test_pause_blocks_checkout_and_resume_allows_it(client, paypal):
    approval = approve(client, [BUD], 5000)

    paused = client.post("/api/pause")
    assert paused.status_code == 200
    assert paused.json()["paused"] is True

    refused = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    )
    assert refused.json()["ok"] is False
    assert refused.json()["code"] == "PAUSED"
    assert paypal.create_calls == []

    resumed = client.post("/api/resume")
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False

    allowed = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    )
    assert allowed.json()["ok"] is True


def test_no_secrets_are_exposed(client, paypal_full):
    approval = approve(client, [BUD], 5000)
    created = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    ).json()
    client.post(f"/api/orders/{created['order_id']}/complete")

    responses = [
        client.get("/api/status"),
        client.get("/api/orders"),
        client.get("/api/logs"),
        client.get(f"/api/orders/{created['order_id']}"),
        client.get("/openapi.json"),
    ]
    secrets = (
        config.SIGNING_SECRET,
        config.PAYPAL_CLIENT_SECRET,
        config.PAYPAL_CLIENT_ID,
        config.CHANNEL3_API_KEY,
    )
    for response in responses:
        assert response.status_code == 200
        for secret in secrets:
            assert secret not in response.text


@pytest.mark.parametrize("path", ["/api/logs", "/api/orders"])
def test_list_limit_validation(client, path):
    assert client.get(f"{path}?limit=500").status_code == 200
    assert client.get(f"{path}?limit=501").status_code == 422
    assert client.get(f"{path}?limit=0").status_code == 422


def test_order_responses_hide_approval_id(client, paypal):
    approval = approve(client, [BUD], 5000)
    created = client.post(
        "/api/checkout", json={"approval_id": approval["approval_id"]}
    ).json()
    listing = client.get("/api/orders")
    detail = client.get(f"/api/orders/{created['order_id']}")
    assert "approval_id" not in listing.text
    assert "approval_id" not in detail.text


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "x" * 201},
        {"query": "earbuds", "max_price_cents": -1},
    ],
)
def test_shop_input_validation(client, payload):
    assert client.post("/api/shop", json=payload).status_code == 422


def test_openapi_includes_new_paths(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/api/approvals/{approval_id}/step-up",
        "/api/orders/{order_id}/complete",
        "/api/orders/{order_id}/cancel",
        "/api/status",
        "/api/pause",
        "/api/resume",
    } <= paths
