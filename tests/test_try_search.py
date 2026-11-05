# tests/test_try_search.py
import pytest

from backend import config
from database import connection
from scripts import try_search


@pytest.fixture(autouse=True)
def script_env(monkeypatch, safe_defaults):
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", False)


def run(argv, lines):
    return try_search.main(argv, say=lines.append)


def text_of(lines):
    return "\n".join(lines)


def test_demo_search_prints_the_expected_ranking():
    lines = []
    code = run(["--demo", "wireless earbuds under $50"], lines)
    text = text_of(lines)
    assert code == 0
    assert "I heard: wireless earbuds" in text
    assert "EchoTone Basic Buds" in text
    assert "suspicious_price" in text
    assert "BLOCKED by the injection guard: BassKing Power Earbuds" in text
    assert "REJECTED OUT_OF_STOCK: Orbit Lite Earbuds" in text
    assert text.index("EchoTone Basic Buds") < text.index("VoltMax Studio Earbuds")


def test_earbuds_without_a_budget_stops_before_searching():
    lines = []
    code = run(["earbuds"], lines)
    assert code == 1
    assert "A budget is needed" in text_of(lines)
    assert "Results for" not in text_of(lines)


def test_no_arguments_prints_usage():
    lines = []
    assert run([], lines) == 1
    assert "Usage" in text_of(lines)


def test_blank_text_returns_one():
    lines = []
    assert run(["   "], lines) == 1
    text = text_of(lines)
    assert "Usage" in text or "Not understood" in text


def test_two_items_print_two_sections_and_the_basket_note():
    lines = []
    code = run(["--demo", "earbuds and a phone case under $100"], lines)
    text = text_of(lines)
    assert code == 0
    assert text.count("Results for") == 2
    assert "The app combines them into an optimized basket." in text


def test_the_real_database_path_is_unchanged():
    before = connection.DB_PATH
    lines = []
    run(["--demo", "wireless earbuds under $50"], lines)
    assert connection.DB_PATH == before
