# backend/agent/limits.py
# RULE 5: limits over time. A daily spending cap, an hourly order limit, and a pause switch.
# Every function takes an optional `now` (epoch seconds) so tests can control the clock.

import time

from backend import config
from backend import guardrails
from database import repository

DAY_SECONDS = 86400
HOUR_SECONDS = 3600


def spent_last_24h(now=None):
    """Money tied up in the last 24 hours: orders that are CREATED (awaiting payment) or CAPTURED (paid)."""
    now = time.time() if now is None else now
    return repository.spent_since(int(now) - DAY_SECONDS, ["CREATED", "CAPTURED"])


def remaining_daily_cap(now=None):
    """What is left of the daily cap, in cents. Never below zero."""
    return max(0, config.DAILY_CAP_CENTS - spent_last_24h(now))


def check_daily_cap(total_cents, now=None):
    """Refuse an order that would push the last 24 hours of spending over the cap."""
    if spent_last_24h(now) + total_cents > config.DAILY_CAP_CENTS:
        return guardrails.Verdict(False, "DAILY_CAP", "Daily spending cap exceeded.")
    return guardrails.Verdict(True, "OK", "ok")


def check_hourly_limit(now=None):
    """Refuse when too many orders were attempted in the last hour. Orders in ANY status count."""
    now = time.time() if now is None else now
    attempts = repository.count_orders_since(int(now) - HOUR_SECONDS, None)
    if attempts >= config.MAX_ORDERS_PER_HOUR:
        return guardrails.Verdict(False, "HOURLY_LIMIT", "Hourly order limit reached.")
    return guardrails.Verdict(True, "OK", "ok")


def is_paused():
    """True unless the stored value is clearly "0" (or nothing was ever stored).

    A kill switch must fail safe: a corrupted or unexpected value keeps the system paused.
    """
    value = repository.get_setting("paused")
    return value is not None and value != "0"


def set_paused(flag):
    """Pause or resume all spending. Stored as "1" or "0" and written to the log."""
    repository.set_setting("paused", "1" if flag else "0")
    if flag:
        repository.add_log("human", "paused", "Spending paused", None)
    else:
        repository.add_log("human", "resumed", "Spending resumed", None)