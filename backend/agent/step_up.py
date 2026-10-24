# backend/agent/step_up.py
# Step-up rules and confirmation.

import time
from backend import config
from backend import guardrails
from backend import money
from database import repository
from backend.agent import approvals


def normalise_domain(domain):
    if not domain:
        return ""
    d = str(domain).strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d


def is_trusted(domain):
    d = normalise_domain(domain)
    if not d:
        return False
    if d in config.TRUSTED_MERCHANTS:
        return True
    for td in config.TRUSTED_MERCHANTS:
        if td and d.endswith("." + td):
            return True
    return False


def step_up_reasons(items, total_cents, remaining_cap, flags):
    flags = flags or {}
    reasons = []
    if int(total_cents) > int(config.STEP_UP_THRESHOLD_CENTS):
        reasons.append("total_above_threshold")
    # suspicious price flag on any item
    for it in items:
        pid = it.get("product_id") if isinstance(it, dict) else getattr(it, "product_id", None)
        f = flags.get(pid) if flags else None
        if f:
            # check for suspicious_price
            try:
                if isinstance(f, (tuple, list, set)) and "suspicious_price" in f:
                    if "suspicious_price" not in reasons:
                        reasons.append("suspicious_price")
                elif f == "suspicious_price":
                    if "suspicious_price" not in reasons:
                        reasons.append("suspicious_price")
            except Exception:
                pass
    # any merchant not trusted
    for it in items:
        dom = it.get("domain") if isinstance(it, dict) else getattr(it, "domain", "")
        if not is_trusted(dom):
            reasons.append("untrusted_merchant")
            break
    # total more than half of remaining_cap
    try:
        if remaining_cap is not None and int(remaining_cap) > 0:
            if int(total_cents) > int(remaining_cap) / 2.0:
                reasons.append("over_half_remaining_cap")
    except Exception:
        pass
    # deduplicate while preserving order of first appearance? easier to just return as built
    # but tests just check presence - build unique preserving order
    out = []
    seen = set()
    for r in reasons:
        if r not in seen:
            out.append(r)
            seen.add(r)
    return out


def confirm_step_up(approval_id, typed_text, now=None):
    now = time.time() if now is None else now
    res = approvals.verify_approval(approval_id, now=now)
    if isinstance(res, tuple):
        verdict, payload = res[0], res[1]
    else:
        verdict = res
        payload = None
    if not verdict.ok:
        repository.add_log("human", "step_up_refused", f"Step-up refused: {verdict.code}", approval_id)
        return verdict
    if payload is None:
        try:
            row = repository.get_approval(approval_id)
            if row:
                payload = __import__('json').loads(row['payload'])
        except Exception:
            pass
    # check typed text
    text = str(typed_text or "").strip()
    accepted = False
    if text.upper() == "CONFIRM":
        accepted = True
    else:
        # try to parse as amount equal to signed total
        try:
            parsed = money.parse_dollars_to_cents(text)
            if payload and int(parsed) == int(payload.get("total_cents", -1)):
                accepted = True
        except Exception:
            accepted = False
    if not accepted:
        repository.add_log("human", "step_up_refused", "Step-up confirmation rejected", approval_id)
        return guardrails.Verdict(False, "STEP_UP_REFUSED", "Step-up confirmation failed.")
    repository.set_step_up_confirmed(approval_id, 1)
    repository.add_log("human", "step_up_confirmed", "Step-up confirmed", approval_id)
    return guardrails.Verdict(True, "OK", "step_up_confirmed")