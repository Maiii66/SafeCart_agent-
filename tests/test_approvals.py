# tests/test_approvals.py
# Tests for backend/agent/approvals.py (safety rule 4: signed, one-time, expiring approvals).

import pytest

from backend import config
from backend.agent import approvals
from database import connection, repository

NOW = 1_000_000
TTL = 300


@pytest.fixture(autouse=True)
def fresh_database(tmp_path, monkeypatch):
    """Every test gets its own empty database and a valid signing secret."""
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SIGNING_SECRET", "a" * 64)
    connection.init_db()


def store_signed_approval(approval_id="appr-1", **changes):
    """Put a correctly signed approval straight into the database (no create_approval needed)."""
    payload = {
        "items": [{"product_id": "demo-bud-01", "title": "Nimbus Air Buds",
                   "domain": "demo-shop.example", "price_cents": 3999}],
        "total_cents": 3999, "budget_cents": 5000, "source": "demo", "nonce": "n1",
        "expires_at": NOW + TTL, "step_up_required": False, "step_up_reasons": [],
    }
    payload.update(changes)
    text = approvals._canonical(payload)
    repository.insert_approval(approval_id, text, approvals._sign(text), NOW, NOW + TTL)
    return payload


def test_good_approval_returns_verdict_and_payload():
    stored = store_signed_approval()
    verdict, payload = approvals.verify_approval("appr-1", now=NOW + 10)
    assert verdict.ok
    assert payload["total_cents"] == stored["total_cents"]


def test_missing_approval_still_returns_a_pair():
    verdict, payload = approvals.verify_approval("does-not-exist", now=NOW)
    assert not verdict.ok and verdict.code == "NO_SUCH_APPROVAL"
    assert payload is None


def test_tampered_payload_is_refused_as_a_pair():
    store_signed_approval()
    conn = connection.get_connection()
    conn.execute("UPDATE approvals SET payload = replace(payload, '3999', '1') WHERE id = 'appr-1'")
    conn.commit()
    conn.close()
    verdict, payload = approvals.verify_approval("appr-1", now=NOW + 10)
    assert verdict.code == "BAD_SIGNATURE"
    assert payload is None


def test_expired_approval_is_refused_as_a_pair():
    store_signed_approval()
    verdict, payload = approvals.verify_approval("appr-1", now=NOW + TTL + 1)
    assert verdict.code == "EXPIRED"
    assert payload is None


def test_used_approval_is_refused_as_a_pair():
    store_signed_approval()
    assert repository.mark_approval_used("appr-1", NOW + 5)
    verdict, payload = approvals.verify_approval("appr-1", now=NOW + 10)
    assert verdict.code == "ALREADY_USED"
    assert payload is None