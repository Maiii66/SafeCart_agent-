# backend/agent/limits.py
# Daily/hourly caps and pause state.

import time
from backend import config
from backend import guardrails
from database import repository


def spent_last_24h(now=None):
    now = time.time() if now is None else now
    cutoff = int(now) - 86400
    return int(repository.spent_since(cutoff, ["CREATED", "CAPTURED"]))


def remaining_daily_cap(now=None):
    remaining = int(config.DAILY_CAP_CENTS) - spent_last_24h(now)
    if remaining < 0:
        remaining = 0
    return remaining


def check_daily_cap(total_cents, now=None):
    now = time.time() if now is None else now
    spent = spent_last_24h(now)
    if spent + int(total_cents) > int(config.DAILY_CAP_CENTS):
        return guardrails.Verdict(False, "DAILY_CAP", "Daily cap exceeded.")
    return guardrails.Verdict(True, "OK", "ok")


def check_hourly_limit(now=None):
    now = time.time() if now is None else now
    cutoff = int(now) - 3600
    count = repository.count_orders_since(cutoff, None)  # any status counts as attempt
    if count >= int(config.MAX_ORDERS_PER_HOUR):
        return guardrails.Verdict(False, "HOURLY_LIMIT", "Hourly limit exceeded.")
    return guardrails.Verdict(True, "OK", "ok")


def is_paused():
    val = repository.get_setting("paused")
    return str(val) == "1"


def set_paused(flag, now=None):
    now = time.time() if now is None else now
    val = "1" if flag else "0"
    repository.set_setting("paused", val)
    if flag:
        repository.add_log("human", "paused", "System paused", None)
    else:
        repository.add_log("human", "resumed", "System resumed", None)