"""Tests for scripts/try_checkout.py: scripted answers, fake PayPal, no real network."""

import pytest

from backend import config
from backend.services import paypal_service
from database import connection
from scripts import try_checkout


class FakePayPal:
    def __init__(self):
        self.create_calls = []
        self.capture_calls = []
        self.waiting = 0
        self._total = 0

    def create_order(self, items, idempotency_key, reference_id="safecart-order"):
        self.create_calls.append({"items": list(items), "key": idempotency_key,
                                  "reference": reference_id})
        self._total = sum(item.unit_cents * item.quantity for item in items)
        return paypal_service.OrderResult(
            "PP-TEST-1",
            "PAYER_ACTION_REQUIRED",
            "https://sandbox.paypal.com/checkoutnow?token=PP-TEST-1",
            self._total,
        )

    def capture_order(self, order_id, idempotency_key):
        self.capture_calls.append({"order_id": order_id, "key": idempotency_key})
        if self.waiting > 0:
            self.waiting -= 1
            raise paypal_service.PayPalError("not approved yet", issue="ORDER_NOT_APPROVED")
        return paypal_service.CaptureResult(order_id, "COMPLETED", "CAP-TEST-1", self._total)


class ScriptedIO:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.lines = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def say(self, line):
        self.lines.append(str(line))

    @property
    def text(self):
        return "\n".join(self.lines)


@pytest.fixture(autouse=True)
def safe_settings(monkeypatch):
    monkeypatch.setattr(config, "SIGNING_SECRET", "s" * 64)
    monkeypatch.setattr(config, "PAYPAL_ENV", "sandbox")
    monkeypatch.setattr(config, "STEP_UP_THRESHOLD_CENTS", 2500)
    monkeypatch.setattr(config, "DAILY_CAP_CENTS", 10000)
    monkeypatch.setattr(config, "MAX_ORDERS_PER_HOUR", 3)
    monkeypatch.setattr(config, "APPROVAL_TTL_SECONDS", 300)
    monkeypatch.setattr(config, "MAX_BUDGET_CENTS", 50000)
    monkeypatch.setattr(config, "TRUSTED_MERCHANTS",
                        ("demo-shop.example", "budget-store.example"))


@pytest.fixture
def fake_paypal(monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(paypal_service, "create_order", fake.create_order)
    monkeypatch.setattr(paypal_service, "capture_order", fake.capture_order)
    return fake


def run(io, argv=None):
    return try_checkout.main([] if argv is None else argv, ask=io.ask, say=io.say)


def test_default_product_succeeds(fake_paypal):
    io = ScriptedIO([""])
    code = run(io)
    assert code == 0
    assert "SUCCESS" in io.text
    assert "https://sandbox.paypal.com/checkoutnow?token=PP-TEST-1" in io.text
    assert len(fake_paypal.create_calls) == 1
    assert len(fake_paypal.capture_calls) == 1


def test_demo_bud_01_asks_for_confirmation_and_confirm_works(fake_paypal):
    io = ScriptedIO(["CONFIRM", ""])
    code = run(io, ["demo-bud-01"])
    assert code == 0
    assert any("39.99" in prompt and "CONFIRM" in prompt for prompt in io.prompts)
    assert len(fake_paypal.create_calls) == 1
    assert len(fake_paypal.capture_calls) == 1


def test_a_wrong_confirmation_refuses_without_calling_paypal(fake_paypal):
    io = ScriptedIO(["39.98"])
    code = run(io, ["demo-bud-01"])
    assert code == 1
    assert "Not confirmed (STEP_UP_REFUSED)" in io.text
    assert fake_paypal.create_calls == []
    assert fake_paypal.capture_calls == []


def test_waiting_for_approval_then_succeeds(fake_paypal):
    fake_paypal.waiting = 1
    io = ScriptedIO(["", ""])
    code = run(io)
    assert code == 0
    assert "has not been approved yet" in io.text
    assert len(fake_paypal.create_calls) == 1
    assert len(fake_paypal.capture_calls) == 2


def test_three_waiting_attempts_give_up(fake_paypal):
    fake_paypal.waiting = 3
    io = ScriptedIO(["", "", ""])
    code = run(io)
    assert code == 1
    assert "The payment did not complete." in io.text
    assert len(fake_paypal.capture_calls) == 3


def test_missing_paypal_keys_return_one_without_calling_paypal(fake_paypal, monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "")
    io = ScriptedIO([])
    code = run(io)
    assert code == 1
    assert "PayPal keys are missing" in io.text
    assert fake_paypal.create_calls == []
    assert fake_paypal.capture_calls == []


def test_live_mode_returns_one_without_calling_paypal(fake_paypal, monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_ENV", "live")
    io = ScriptedIO([])
    code = run(io)
    assert code == 1
    assert "sandbox" in io.text.lower()
    assert fake_paypal.create_calls == []
    assert fake_paypal.capture_calls == []


def test_short_signing_secret_returns_one_without_calling_paypal(fake_paypal, monkeypatch):
    monkeypatch.setattr(config, "SIGNING_SECRET", "too-short")
    io = ScriptedIO([])
    code = run(io)
    assert code == 1
    assert "SIGNING_SECRET" in io.text
    assert fake_paypal.create_calls == []
    assert fake_paypal.capture_calls == []


def test_unknown_product_returns_one(fake_paypal):
    io = ScriptedIO([])
    code = run(io, ["not-a-product"])
    assert code == 1
    assert "is not in the demo catalog" in io.text
    assert fake_paypal.create_calls == []
    assert fake_paypal.capture_calls == []


def test_output_never_contains_secrets_and_database_path_is_restored(fake_paypal):
    before_db_path = connection.DB_PATH
    io = ScriptedIO([""])
    code = run(io)
    assert code == 0
    assert config.PAYPAL_CLIENT_SECRET not in io.text
    assert config.SIGNING_SECRET not in io.text
    assert connection.DB_PATH == before_db_path
