# tests/test_limits.py
# Tests for backend/agent/limits.py (safety rule 5: daily cap, hourly limit, pause switch).

import time

import pytest

from backend import config
from backend.agent import limits
from database import connection, repository

NOW = 1_000_000
DAY = 86400
HOUR = 3600


@pytest.fixture(autouse=True)
def settings_and_database(tmp_path, monkeypatch):
    """A fresh database and pinned settings, so a different .env cannot change the results."""
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DAILY_CAP_CENTS", 10000)
    monkeypatch.setattr(config, "MAX_ORDERS_PER_HOUR", 3)
    connection.init_db()


def add_order(total_cents=1000, status="CREATED", age_seconds=0, approval_id="appr"):
    """Insert an order that was created `age_seconds` before NOW. Returns its id."""
    return repository.insert_order(approval_id, "PP-" + status, status, total_cents, NOW - age_seconds)


# ---------- daily spending cap ----------

def test_nothing_spent_means_the_whole_cap_is_left():
    assert limits.spent_last_24h(NOW) == 0
    assert limits.remaining_daily_cap(NOW) == 10000


def test_only_created_and_captured_orders_count_as_spent():
    add_order(1000, "CREATED")
    add_order(2000, "CAPTURED")
    add_order(4000, "FAILED")
    add_order(8000, "CANCELLED")
    assert limits.spent_last_24h(NOW) == 3000


def test_orders_older_than_24_hours_do_not_count():
    add_order(1000, "CAPTURED", age_seconds=DAY + 1)       # just too old
    add_order(2000, "CAPTURED", age_seconds=DAY - 1)       # just inside
    assert limits.spent_last_24h(NOW) == 2000


def test_an_order_exactly_24_hours_old_still_counts():
    """The safe choice for a spending cap: when in doubt, count it."""
    add_order(1000, "CAPTURED", age_seconds=DAY)
    assert limits.spent_last_24h(NOW) == 1000


def test_remaining_cap_is_the_cap_minus_spent():
    add_order(3000, "CAPTURED")
    assert limits.remaining_daily_cap(NOW) == 7000


def test_remaining_cap_never_goes_below_zero():
    add_order(12000, "CAPTURED")
    assert limits.remaining_daily_cap(NOW) == 0


def test_daily_cap_boundary_exactly_at_the_cap_is_fine_one_cent_over_is_refused():
    add_order(6000, "CAPTURED")                             # $40 left of a $100 cap
    assert limits.check_daily_cap(4000, NOW).ok
    refused = limits.check_daily_cap(4001, NOW)
    assert not refused.ok and refused.code == "DAILY_CAP"


def test_a_cancelled_order_gives_the_cap_back():
    order_id = add_order(9000, "CREATED")
    assert limits.check_daily_cap(2000, NOW).code == "DAILY_CAP"
    repository.update_order_status(order_id, "CANCELLED")
    assert limits.check_daily_cap(2000, NOW).ok


def test_a_failed_order_does_not_use_up_the_cap():
    add_order(9000, "FAILED")
    assert limits.check_daily_cap(10000, NOW).ok


# ---------- hourly order limit ----------

def test_hourly_limit_boundary():
    add_order(age_seconds=10)
    add_order(age_seconds=20)
    assert limits.check_hourly_limit(NOW).ok                # 2 orders, limit 3
    add_order(age_seconds=30)
    refused = limits.check_hourly_limit(NOW)                # 3 orders: the limit is reached
    assert not refused.ok and refused.code == "HOURLY_LIMIT"


@pytest.mark.parametrize("status", ["CREATED", "CAPTURED", "FAILED", "CANCELLED"])
def test_every_order_status_counts_as_an_attempt(status):
    for _ in range(3):
        add_order(status=status, age_seconds=5)
    assert limits.check_hourly_limit(NOW).code == "HOURLY_LIMIT"


def test_orders_older_than_an_hour_do_not_count():
    for _ in range(3):
        add_order(age_seconds=HOUR + 1)
    assert limits.check_hourly_limit(NOW).ok


def test_an_order_exactly_one_hour_old_still_counts():
    for _ in range(3):
        add_order(age_seconds=HOUR)
    assert limits.check_hourly_limit(NOW).code == "HOURLY_LIMIT"


def test_the_clock_defaults_to_the_real_time():
    for _ in range(3):
        repository.insert_order("appr", "PP-1", "CREATED", 100, int(time.time()))
    assert limits.check_hourly_limit().code == "HOURLY_LIMIT"


# ---------- pause switch ----------

def test_not_paused_by_default():
    assert limits.is_paused() is False


def test_pause_and_resume_persist():
    limits.set_paused(True)
    assert limits.is_paused() is True
    limits.set_paused(False)
    assert limits.is_paused() is False


def test_pausing_twice_is_harmless():
    limits.set_paused(True)
    limits.set_paused(True)
    assert limits.is_paused() is True


def test_pause_and_resume_are_logged():
    limits.set_paused(True)
    limits.set_paused(False)
    actions = [row["action"] for row in repository.list_logs()]
    assert "paused" in actions and "resumed" in actions


@pytest.mark.parametrize("garbage", ["true", "yes", "PAUSED", "", "2", " 1", "None"])
def test_an_unclear_pause_value_means_paused(garbage):
    """A kill switch must fail safe: if the stored value is not a clear 'not paused', stay paused."""
    repository.set_setting("paused", garbage)
    assert limits.is_paused() is True