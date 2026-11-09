# backend/agent/policy.py
# RULE 7: a spending policy written by the person. The AI may only suggest it;
# this file clamps it against config and enforces it in plain Python.
#
# A policy can only make the global limits STRICTER. It can never raise MAX_BUDGET,
# the daily cap or the hourly limit, and it can never add a trusted merchant.

import re
import time

from backend import config
from backend import guardrails
from backend.money import format_usd
from database import repository

DAY_SECONDS = 86400
HOUR_SECONDS = 3600
SPENT_STATUSES = ("CREATED", "CAPTURED")
MAX_BLOCKED_MERCHANTS = 50

POLICY_FIELDS = {
    "max_item_cents",
    "max_order_cents",
    "max_daily_spend_cents",
    "max_orders_per_hour",
    "max_orders_per_day",
    "trusted_only",
    "blocked_merchants",
}

_DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


class PolicyError(ValueError):
    """A policy field was malformed or unsafe."""


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _yes(reason="ok"):
    return guardrails.Verdict(True, "OK", reason)


def _no(code, reason):
    return guardrails.Verdict(False, code, reason)


def normalise_domain(domain):
    """' WWW.Walmart.com ' -> 'walmart.com'. An invalid value becomes ''."""
    text = str(domain or "").strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].strip().strip(".")
    return text[4:] if text.startswith("www.") else text


def _valid_domain(domain):
    return bool(_DOMAIN.match(domain))


def is_trusted(domain):
    """Trusted means the domain IS a configured merchant or a sub-domain of one."""
    name = normalise_domain(domain)
    if not name:
        return False
    return any(name == trusted or name.endswith("." + trusted)
               for trusted in config.TRUSTED_MERCHANTS)


def is_blocked(domain, blocked_merchants):
    """A blocked domain also blocks its sub-domains, never a look-alike domain."""
    name = normalise_domain(domain)
    if not name or not isinstance(blocked_merchants, (list, tuple)):
        return False
    return any(name == blocked or name.endswith("." + blocked)
               for blocked in blocked_merchants)


def _amount(raw, key, ceiling, notices):
    value = raw.get(key)
    if value is None:
        return None
    if not _positive_int(value):
        raise PolicyError(f"{key} must be a positive whole number of cents.")
    if value > ceiling:
        notices.append(
            f"{key.replace('_cents', '').replace('_', ' ')}: "
            f"{format_usd(value)} was lowered to the global maximum of {format_usd(ceiling)}."
        )
        return ceiling
    return value


def _count(raw, key, ceiling, notices):
    value = raw.get(key)
    if value is None:
        return None
    if not _positive_int(value):
        raise PolicyError(f"{key} must be a positive whole number.")
    if value > ceiling:
        notices.append(f"{key.replace('_', ' ')}: {value} was lowered to the global maximum of {ceiling}.")
        return ceiling
    return value


def _blocked(raw, notices):
    value = raw.get("blocked_merchants")
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise PolicyError("blocked_merchants must be a list of domains.")
    if len(value) > MAX_BLOCKED_MERCHANTS:
        raise PolicyError(f"At most {MAX_BLOCKED_MERCHANTS} blocked merchants are allowed.")

    cleaned = []
    for item in value:
        if not isinstance(item, str):
            raise PolicyError("Each blocked merchant must be a domain name.")
        domain = normalise_domain(item)
        if not _valid_domain(domain):
            raise PolicyError(f"'{item}' is not a valid merchant domain.")
        if domain not in cleaned:
            cleaned.append(domain)

    if len(cleaned) != len(value):
        notices.append("Duplicate blocked merchants were merged.")
    return cleaned


def clamp_policy(raw):
    """Validate a policy and clamp anything looser than config. Returns (policy, notices)."""
    if not isinstance(raw, dict):
        raise PolicyError("A policy must be an object.")
    unknown = set(raw) - POLICY_FIELDS
    if unknown:
        raise PolicyError(f"Unknown policy field(s): {', '.join(sorted(unknown))}.")

    notices = []
    max_item = _amount(raw, "max_item_cents", config.MAX_BUDGET_CENTS, notices)
    max_order = _amount(raw, "max_order_cents", config.MAX_BUDGET_CENTS, notices)
    max_daily = _amount(raw, "max_daily_spend_cents", config.DAILY_CAP_CENTS, notices)
    per_hour = _count(raw, "max_orders_per_hour", config.MAX_ORDERS_PER_HOUR, notices)
    per_day = _count(raw, "max_orders_per_day", max(1, int(config.MAX_ORDERS_PER_HOUR) * 24), notices)

    trusted_only = raw.get("trusted_only", False)
    if trusted_only is None:
        trusted_only = False
    if not isinstance(trusted_only, bool):
        raise PolicyError("trusted_only must be true or false.")

    blocked = _blocked(raw, notices)

    if max_item is not None and max_order is not None and max_item > max_order:
        notices.append(
            f"Per-item limit {format_usd(max_item)} was lowered to the order limit "
            f"of {format_usd(max_order)}."
        )
        max_item = max_order

    policy = {
        "max_item_cents": max_item,
        "max_order_cents": max_order,
        "max_daily_spend_cents": max_daily,
        "max_orders_per_hour": per_hour,
        "max_orders_per_day": per_day,
        "trusted_only": trusted_only,
        "blocked_merchants": blocked,
    }
    return policy, notices


def has_rules(policy):
    return bool(policy) and (
        any(policy.get(key) is not None for key in (
            "max_item_cents", "max_order_cents", "max_daily_spend_cents",
            "max_orders_per_hour", "max_orders_per_day",
        ))
        or bool(policy.get("trusted_only"))
        or bool(policy.get("blocked_merchants"))
    )


def describe(policy):
    """Plain-English rows for the checklist, the UI and the audit log."""
    if not isinstance(policy, dict):
        return []
    rows = []
    if _positive_int(policy.get("max_item_cents")):
        rows.append(f"Max per item: {format_usd(policy['max_item_cents'])}")
    if _positive_int(policy.get("max_order_cents")):
        rows.append(f"Max per order: {format_usd(policy['max_order_cents'])}")
    if _positive_int(policy.get("max_daily_spend_cents")):
        rows.append(f"Max spend per day: {format_usd(policy['max_daily_spend_cents'])}")
    if _positive_int(policy.get("max_orders_per_hour")):
        rows.append(f"Max orders per hour: {policy['max_orders_per_hour']}")
    if _positive_int(policy.get("max_orders_per_day")):
        rows.append(f"Max orders per day: {policy['max_orders_per_day']}")
    if policy.get("trusted_only"):
        rows.append("Trusted merchants only")
    blocked = policy.get("blocked_merchants")
    if isinstance(blocked, (list, tuple)) and blocked:
        rows.append("Blocked merchants: " + ", ".join(blocked))
    return rows


def summary(policy):
    rows = describe(policy)
    return "; ".join(rows) if rows else "No spending rules"


def get_policy():
    return repository.get_policy()


def save_policy(policy):
    repository.save_policy(policy)


def delete_policy():
    return repository.delete_policy()


def spent_last_24h(now=None):
    """Exactly the same spend as limits.spent_last_24h: CREATED + CAPTURED, rolling 24h."""
    now = time.time() if now is None else now
    return repository.spent_since(int(now) - DAY_SECONDS, list(SPENT_STATUSES))


def _order_count(seconds, now):
    return repository.count_orders_since(int(now) - seconds, None)


def check_offer(domain, price_cents, policy=None):
    """Check one offer against the policy's item and merchant rules."""
    policy = get_policy() if policy is None else policy
    if not isinstance(policy, dict):
        return _yes()

    if is_blocked(domain, policy.get("blocked_merchants")):
        return _no("POLICY_BLOCKED_MERCHANT", f"{normalise_domain(domain)} is on your blocked-merchant list.")
    if policy.get("trusted_only") and not is_trusted(domain):
        return _no("POLICY_UNTRUSTED_STORE", f"{normalise_domain(domain)} is not a trusted merchant.")

    max_item = policy.get("max_item_cents")
    if _positive_int(max_item) and _positive_int(price_cents) and price_cents > max_item:
        return _no(
            "POLICY_ITEM_LIMIT",
            f"{format_usd(price_cents)} is above your per-item limit of {format_usd(max_item)}.",
        )
    return _yes()


def check_approval(items, total_cents, now=None, policy=None):
    """Enforce the policy at approval time. Returns a Verdict."""
    policy = get_policy() if policy is None else policy
    if not isinstance(policy, dict) or not has_rules(policy):
        return _yes()

    now = time.time() if now is None else now
    for item in items or ():
        if not isinstance(item, dict):
            continue
        verdict = check_offer(item.get("domain"), item.get("price_cents"), policy)
        if not verdict.ok:
            return verdict

    max_order = policy.get("max_order_cents")
    if _positive_int(max_order) and _positive_int(total_cents) and total_cents > max_order:
        return _no(
            "POLICY_ORDER_LIMIT",
            f"This order's {format_usd(total_cents)} is above your per-order limit of {format_usd(max_order)}.",
        )

    max_daily = policy.get("max_daily_spend_cents")
    if _positive_int(max_daily):
        spent = spent_last_24h(now)
        if spent + int(total_cents) > max_daily:
            return _no(
                "POLICY_DAILY_SPEND",
                f"{format_usd(spent)} was already spent in the last 24 hours; adding "
                f"{format_usd(total_cents)} would exceed your daily limit of {format_usd(max_daily)}.",
            )

    per_hour = policy.get("max_orders_per_hour")
    if _positive_int(per_hour):
        attempts = _order_count(HOUR_SECONDS, now)
        if attempts >= per_hour:
            return _no(
                "POLICY_HOURLY_ORDERS",
                f"You have already attempted {attempts} orders in the last hour; your limit is {per_hour}.",
            )

    per_day = policy.get("max_orders_per_day")
    if _positive_int(per_day):
        attempts = _order_count(DAY_SECONDS, now)
        if attempts >= per_day:
            return _no(
                "POLICY_DAILY_ORDERS",
                f"You have already attempted {attempts} orders in the last 24 hours; your limit is {per_day}.",
            )

    return _yes()


def check_checkout(items, total_cents, now=None, policy=None):
    """Same rules as approval, but items must carry the LIVE price and domain."""
    return check_approval(items, total_cents, now=now, policy=policy)


def effective_search_budget(budget_cents, policy=None):
    """Never search above the policy's per-order limit."""
    policy = get_policy() if policy is None else policy
    if not isinstance(policy, dict):
        return budget_cents
    max_order = policy.get("max_order_cents")
    if not _positive_int(max_order):
        return budget_cents
    if not _positive_int(budget_cents) or budget_cents > max_order:
        return max_order
    return budget_cents


def filter_candidates(candidates, policy=None):
    """Split search candidates into kept and policy-rejected (with reasons to display)."""
    policy = get_policy() if policy is None else policy
    candidates = list(candidates)
    if not isinstance(policy, dict) or not has_rules(policy):
        return candidates, []

    kept, rejected = [], []
    for candidate in candidates:
        verdict = check_offer(candidate.offer.domain, candidate.offer.price_cents, policy)
        if verdict.ok:
            kept.append(candidate)
        else:
            rejected.append(guardrails.Rejection(
                candidate.product.id,
                candidate.product.title,
                verdict.code,
                verdict.reason,
            ))
    return kept, rejected
