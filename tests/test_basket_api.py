import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.main import app
from database import connection

API_KEY = "b" * 40
ITEMS = [
    "planar magnetic headphones",
    "140W power brick",
    "MagSafe hard shell",
]


@pytest.fixture(autouse=True)
def api_env(monkeypatch, tmp_path):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "basket-api.db")
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", True)
    monkeypatch.setattr(config, "API_KEY", API_KEY)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    connection.init_db()


@pytest.fixture
def client():
    return TestClient(app, headers={"X-API-Key": API_KEY})


def test_basket_returns_optimal_and_greedy_results(client):
    response = client.post(
        "/api/basket",
        json={"items": ITEMS, "total_budget_cents": 10000},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "demo"
    assert data["total_cents"] == 10000
    assert data["remaining_cents"] == 0
    assert {item["category"] for item in data["optimal"]["chosen"]} == {
        "140W power brick",
        "MagSafe hard shell",
    }
    assert {item["category"] for item in data["greedy"]["chosen"]} == {
        "planar magnetic headphones"
    }
    assert data["score_gain"] > 0
    assert data["price_difference_cents"] > 0
    assert data["explanation"]
    assert data["explanation_source"] == "template"


def test_basket_reports_items_and_notes(client):
    response = client.post(
        "/api/basket",
        json={"items": ITEMS + ["unobtainium"], "total_budget_cents": 10000},
    )

    assert response.status_code == 200
    data = response.json()
    by_query = {item["query"]: item for item in data["items"]}
    assert by_query["unobtainium"]["product"] is None
    assert "No safe listings" in by_query["unobtainium"]["note"]
    assert by_query["140W power brick"]["product"]["title"] == "VoltEdge 140W Power Brick"


def test_basket_requires_an_api_key(client):
    unauthenticated = TestClient(app)
    response = unauthenticated.post(
        "/api/basket",
        json={"items": ITEMS, "total_budget_cents": 10000},
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [], "total_budget_cents": 10000},
        {"items": ITEMS, "total_budget_cents": 0},
        {"items": ITEMS, "total_budget_cents": 10000, "extra": "no"},
        {"items": [f"item {index}" for index in range(6)], "total_budget_cents": 10000},
        {"items": ["   "], "total_budget_cents": 10000},
    ],
)
def test_basket_rejects_invalid_payloads(client, payload):
    response = client.post("/api/basket", json=payload)

    assert response.status_code == 422


def test_shop_response_includes_a_template_explanation(client):
    response = client.post(
        "/api/shop",
        json={"query": "earbuds", "max_price_cents": 5000, "limit": 5},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["products"]
    assert data["explanation"]
    assert data["explanation_source"] == "template"
