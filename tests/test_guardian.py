import pytest

from backend import config
from backend.agent import guardian, injection, shopper
from backend.models import Candidate
from backend.services import catalog_demo, llm


@pytest.fixture(autouse=True)
def clear_guardian_cache():
    guardian.clear_cache()
    yield
    guardian.clear_cache()


def _candidate(product_id="demo-bud-04"):
    product = catalog_demo.get_product(product_id)
    return Candidate(product, product.offers[0])


def test_hostile_listing_is_scanned_before_guardian_review():
    product = catalog_demo.get_product("demo-bud-09")
    result = injection.scan_product(product)
    assert result.flagged
    assert "description:impersonation" in result.reasons


def test_ai_low_cannot_clear_a_rule_flag(monkeypatch):
    monkeypatch.setattr(llm, "review_guardian", lambda listings: {
        "reviews": [{
            "product_id": "demo-bud-04",
            "risk_level": "low",
            "reasons": [],
            "summary": "Looks ordinary.",
        }]
    })
    reviews, available = guardian.review(
        [_candidate()],
        {"demo-bud-04": ("suspicious_price",)},
    )
    assert available
    assert reviews["demo-bud-04"]["risk_level"] == "medium"


def test_invalid_ai_output_falls_back_to_rules(monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm,
        "review_guardian",
        lambda listings: calls.append(listings) or {"reviews": [{"risk_level": "high"}]},
    )
    reviews, available = guardian.review([_candidate()], {})
    assert calls
    assert not available
    assert reviews["demo-bud-04"]["risk_level"] == "low"
    assert "AI review unavailable" in reviews["demo-bud-04"]["summary"]


def test_guardian_caches_by_product_id_and_listing_text_hash(monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm,
        "review_guardian",
        lambda listings: calls.append(listings) or {
            "reviews": [
                {
                    "product_id": listing["id"],
                    "risk_level": "medium",
                    "reasons": ["Unusually low price."],
                    "summary": "Review this listing carefully.",
                }
                for listing in listings
            ]
        },
    )
    candidate = _candidate()
    first, _ = guardian.review([candidate], {})
    second, _ = guardian.review([candidate], {})
    assert len(calls) == 1
    assert first == second


def test_search_falls_back_when_guardian_fails(monkeypatch, safe_defaults):
    monkeypatch.setattr(config, "USE_DEMO_CATALOG", True)
    monkeypatch.setattr(llm, "review_guardian", lambda listings: (_ for _ in ()).throw(RuntimeError("down")))
    outcome = shopper.search_and_rank("wireless earbuds", 5000, now=1_000_000)
    assert not outcome.guardian_available
    assert outcome.ranked
    assert all("AI review unavailable" in review["summary"]
               for review in outcome.guardian_reviews.values())
