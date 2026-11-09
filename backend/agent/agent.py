"""Bounded AI shopping planner.

This module may propose a basket, but it has no approval, checkout, payment,
or policy-mutation capability.
"""

import time

from backend import config
from backend.agent import guardian, injection, optimizer, policy, shopper, understand
from backend.api import utils
from backend.services import llm

ALLOWED_TOOLS = frozenset({
    "search_products", "compare_products", "get_risk_review",
    "check_policy", "build_basket", "finish",
})
MAX_STEPS = 8
MAX_SECONDS = 60


class AgentError(ValueError):
    pass


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AgentError(f"{name} must be non-empty text.")
    if injection.scan_text(value).flagged:
        raise AgentError(f"{name} failed the safety check.")
    return value.strip()


def _budget(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AgentError("budget_cents must be a positive integer.")
    return value


def _effective_budget(user_budget, active_policy):
    limits = [config.MAX_BUDGET_CENTS]
    if isinstance(user_budget, int):
        limits.append(user_budget)
    if isinstance(active_policy, dict) and isinstance(active_policy.get("max_order_cents"), int):
        limits.append(active_policy["max_order_cents"])
    return min(limits)


def _safe_result(value):
    if isinstance(value, str):
        return injection.sanitize_for_llm(value, 600)
    if isinstance(value, dict):
        return {
            injection.sanitize_for_llm(str(key), 80): _safe_result(item)
            for key, item in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_result(item) for item in list(value)[:20]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return injection.sanitize_for_llm(str(value), 600)


def _args(value, required, optional=()):
    if not isinstance(value, dict):
        raise AgentError("Tool arguments must be an object.")
    allowed = set(required) | set(optional)
    if set(value) - allowed or any(key not in value for key in required):
        raise AgentError("Tool arguments do not match the declared schema.")
    return value


def _proposal(outcome, budget):
    chosen = []
    for choice in outcome.optimal.chosen:
        item = choice.item
        chosen.append({
            "category": choice.category,
            "product": utils.ranked_to_dict(item),
            "price_cents": choice.price_cents,
        })
    return {
        "status": "PROPOSED",
        "source": outcome.source,
        "budget_cents": budget,
        "total_cents": outcome.total_cents,
        "items": chosen,
        "note": "The agent cannot pay. Review and send this proposal to your cart.",
    }


def _fallback(goal, user_budget, trace, started):
    parsed = understand.parse_request(goal)
    queries = [item.query for item in parsed.items]
    parsed_budget = parsed.total_budget_cents
    if parsed_budget is None and len(parsed.items) == 1:
        parsed_budget = parsed.items[0].budget_cents
    budget = _effective_budget(user_budget or parsed_budget, policy.get_policy())
    if not queries or budget <= 0:
        raise AgentError("I could not find a usable shopping goal and budget.")
    trace.append({"step": len(trace) + 1, "tool": "fallback", "message": "Fallback mode: building a safe proposal."})
    outcome = shopper.search_basket(queries, budget, limit=10)
    return trace, _proposal(outcome, budget), "Fallback mode"


def run(goal, budget_cents=None, *, clock=None):
    goal = _text(goal, "goal", 500)
    if budget_cents is not None:
        budget_cents = _budget(budget_cents)
    clock = time.monotonic if clock is None else clock
    started = clock()
    trace = []
    active_policy = policy.get_policy()
    try:
        parsed = understand.parse_request(goal)
        parsed_budget = parsed.total_budget_cents
        if parsed_budget is None and len(parsed.items) == 1:
            parsed_budget = parsed.items[0].budget_cents
        effective = _effective_budget(budget_cents or parsed_budget, active_policy)
        queries = [item.query for item in parsed.items]
        if not queries or effective <= 0:
            raise AgentError("I could not find a usable shopping goal and budget.")
    except Exception:
        return _fallback(goal, budget_cents, trace, started)

    state = {"search": {}, "reviews": {}, "basket": None}
    for step in range(MAX_STEPS):
        if clock() - started > MAX_SECONDS:
            return _fallback(goal, budget_cents, trace, started)
        if not llm.available():
            return _fallback(goal, budget_cents, trace, started)
        response = llm.agent_turn(goal, trace, llm._AGENT_TOOL_SCHEMAS)
        if not isinstance(response, dict):
            return _fallback(goal, budget_cents, trace, started)
        name = response.get("tool_name") or response.get("name")
        args = response.get("arguments", response.get("args", {}))
        if name not in ALLOWED_TOOLS:
            raise AgentError(f"Tool '{name}' is not allowed.")
        if not isinstance(args, dict):
            raise AgentError("Tool arguments must be an object.")
        try:
            if name == "search_products":
                args = _args(args, ("query", "budget_cents"))
                query = _text(args.get("query"), "query", 200)
                tool_budget = _budget(args.get("budget_cents"))
                if tool_budget != effective:
                    raise AgentError("The model cannot change the effective budget.")
                outcome = shopper.search_and_rank(query, effective, limit=10)
                state["search"].update({
                    item.candidate.product.id: item for item in outcome.ranked
                })
                result = {"products": [
                    utils.ranked_to_dict(item) for item in outcome.ranked
                ]}
                message = f"Searched for {query}."
            elif name == "compare_products":
                args = _args(args, ("queries",))
                qs = args.get("queries")
                if not isinstance(qs, list) or not 1 <= len(qs) <= 5:
                    raise AgentError("queries must contain 1 to 5 items.")
                qs = [_text(item, "query", 200) for item in qs]
                result = {"queries": qs, "message": "Comparison is ready."}
                message = "Compared the requested product categories."
            elif name == "get_risk_review":
                args = _args(args, ("product_ids",))
                ids = args.get("product_ids")
                if not isinstance(ids, list) or not ids or len(ids) > 10:
                    raise AgentError("product_ids must contain 1 to 10 ids.")
                candidates = [state["search"][item].candidate for item in ids if item in state["search"]]
                if len(candidates) != len(ids):
                    raise AgentError("Risk review may only use searched products.")
                reviews, available = guardian.review(candidates)
                state["reviews"].update(reviews)
                result = {"reviews": reviews, "available": available}
                message = "Reviewed product risk."
            elif name == "check_policy":
                args = _args(args, ("product_ids",))
                ids = args.get("product_ids")
                if not isinstance(ids, list) or not ids:
                    raise AgentError("product_ids must be a non-empty list.")
                items = []
                for item in ids:
                    ranked = state["search"].get(item)
                    if ranked is None:
                        raise AgentError("Policy checks require searched products.")
                    items.append({"domain": ranked.candidate.offer.domain, "price_cents": ranked.candidate.offer.price_cents})
                verdict = policy.check_approval(items, sum(item["price_cents"] for item in items), policy=active_policy)
                result = {"ok": verdict.ok, "code": verdict.code, "reason": verdict.reason}
                message = "Checked the active spending policy."
            elif name == "build_basket":
                args = _args(args, ("queries", "budget_cents"))
                qs = args.get("queries")
                tool_budget = _budget(args.get("budget_cents"))
                if tool_budget != effective or not isinstance(qs, list) or not qs:
                    raise AgentError("Invalid basket arguments.")
                outcome = shopper.search_basket([_text(item, "query", 200) for item in qs], effective, limit=10)
                state["basket"] = outcome
                result = _proposal(outcome, effective)
                message = "Built a proposed basket."
            else:
                _args(args, ())
                if state["basket"] is None:
                    outcome = shopper.search_basket(queries, effective, limit=10)
                    state["basket"] = outcome
                result = _proposal(state["basket"], effective)
                trace.append({"step": step + 1, "tool": "finish", "message": "Proposal ready."})
                return trace, result, "Gemini mode"
        except AgentError:
            raise
        trace.append({"step": step + 1, "tool": name, "message": message, "result": _safe_result(result)})
    return _fallback(goal, budget_cents, trace, started)
