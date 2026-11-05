import pytest

from backend import config
from backend.agent import shopper
from database import connection

ITEMS = [
    "planar magnetic headphones",
    "140W power brick",
    "MagSafe hard shell",
]


@pytest.fixture(autouse=True)
def demo_db(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", True)
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "basket.db")
    connection.init_db()


def test_optimal_basket_beats_greedy_under_one_hundred_dollars():
    result = shopper.search_basket(ITEMS, 10000, now=123)

    assert result.source == "demo"
    assert result.total_budget_cents == 10000
    assert result.total_cents == 10000
    assert result.remaining_cents == 0

    optimal = {choice.category: choice for choice in result.optimal.chosen}
    greedy = {choice.category: choice for choice in result.greedy.chosen}

    assert set(optimal) == {"140W power brick", "MagSafe hard shell"}
    assert set(greedy) == {"planar magnetic headphones"}
    assert set(result.optimal.unfulfilled) == {"planar magnetic headphones"}
    assert result.comparison.score_gain > 0
    assert result.comparison.price_difference_cents > 0


def test_each_item_reports_whether_it_was_chosen():
    result = shopper.search_basket(ITEMS, 10000, now=123)
    by_query = {item.query: item for item in result.items}

    assert by_query["planar magnetic headphones"].item is None
    assert "did not fit" in by_query["planar magnetic headphones"].note
    assert by_query["140W power brick"].item is not None
    assert by_query["MagSafe hard shell"].item is not None


def test_item_with_no_candidates_is_skipped_with_a_note():
    result = shopper.search_basket(ITEMS + ["unobtainium"], 10000, now=123)
    by_query = {item.query: item for item in result.items}

    assert by_query["unobtainium"].item is None
    assert "No safe listings" in by_query["unobtainium"].note
    assert set(choice.category for choice in result.optimal.chosen) == {
        "140W power brick",
        "MagSafe hard shell",
    }


def test_duplicate_queries_are_merged():
    result = shopper.search_basket(["earbuds", "Earbuds"], 10000, now=123)

    assert [item.query for item in result.items] == ["earbuds"]


@pytest.mark.parametrize("budget", [0, -1, True, "10000"])
def test_bad_budget_is_refused(budget):
    with pytest.raises(ValueError):
        shopper.search_basket(ITEMS, budget, now=123)


def test_empty_or_too_many_items_are_refused():
    with pytest.raises(ValueError):
        shopper.search_basket([], 10000, now=123)
    with pytest.raises(ValueError):
        shopper.search_basket([f"item {index}" for index in range(6)], 10000, now=123)
