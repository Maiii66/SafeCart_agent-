"""Tests for scripts/try_attacks.py: real refusals, honest failures, and clean cleanup."""

import json

from backend import config, guardrails
from backend.agent import checkout
from database import connection, repository
from scripts import try_attacks as attacks


def test_every_scenario_behaves_exactly_as_expected():
    results = attacks.run_scenarios()
    assert len(results) >= 15
    assert all(result.passed for result in results), [
        (result.title, result.actual, result.reason, result.paypal_calls)
        for result in results if not result.passed
    ]


def test_main_reports_success_and_returns_zero(capsys):
    code = attacks.main()
    output = capsys.readouterr().out
    assert code == 0
    assert "All 15 scenarios behaved exactly as expected." in output
    assert "NOT AS EXPECTED" not in output


def test_refusals_never_call_paypal():
    results = attacks.run_scenarios()
    for result in results:
        if result.expected == "ACCEPTED":
            continue
        if result.title == "Replay the same approval":
            assert result.paypal_calls == 1
        else:
            assert result.paypal_calls == 0, (result.title, result.paypal_calls)


def test_running_scenarios_restores_settings_and_database_path():
    watched = ("SIGNING_SECRET", "PAYPAL_ENV", "DAILY_CAP_CENTS")
    before = {name: getattr(config, name) for name in watched}
    before_db_path = connection.DB_PATH

    attacks.run_scenarios()

    assert connection.DB_PATH == before_db_path
    for name, value in before.items():
        assert getattr(config, name) == value


def test_an_exception_is_reported_as_error_not_a_refusal(monkeypatch, capsys):
    def boom():
        raise ValueError("boom")

    monkeypatch.setattr(attacks, "SCENARIOS",
                        [("Raises an exception", "ACCEPTED", 0, boom)])
    results = attacks.run_scenarios()
    assert len(results) == 1
    result = results[0]
    assert result.actual == "ERROR"
    assert result.passed is False
    assert "boom" in result.reason

    code = attacks.main()
    output = capsys.readouterr().out
    assert code == 1
    assert "FAILED" in output
    assert "NOT AS EXPECTED" in output


def test_weakened_replay_protection_makes_the_report_fail(monkeypatch, capsys):
    real_verify = checkout.approvals.verify_approval

    def weak_verify(approval_id, now=None):
        verdict, payload = real_verify(approval_id, now=now)
        if not verdict.ok and verdict.code == "ALREADY_USED":
            row = repository.get_approval(approval_id)
            if row:
                return guardrails.Verdict(True, "OK", "ok"), json.loads(row["payload"])
        return verdict, payload

    monkeypatch.setattr(checkout.approvals, "verify_approval", weak_verify)
    monkeypatch.setattr(checkout.repository, "mark_approval_used",
                        lambda *args, **kwargs: True)

    code = attacks.main()
    output = capsys.readouterr().out
    assert code == 1
    assert "FAILED" in output
    assert "NOT AS EXPECTED" in output


def test_a_wrong_paypal_call_count_is_a_failure(monkeypatch, capsys):
    def purchase():
        approval = attacks._approve("demo-bud-04")
        return checkout.execute_checkout(approval.approval_id, now=attacks.NOW)

    monkeypatch.setattr(attacks, "SCENARIOS",
                        [("Call count mismatch", "ACCEPTED", 0, purchase)])
    results = attacks.run_scenarios()
    assert len(results) == 1
    result = results[0]
    assert result.actual == "ACCEPTED"
    assert result.paypal_calls == 1
    assert result.passed is False

    code = attacks.main()
    output = capsys.readouterr().out
    assert code == 1
    assert "NOT AS EXPECTED" in output
