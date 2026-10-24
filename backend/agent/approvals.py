# backend/agent/approvals.py
# Creates and verifies signed approvals. All amounts come only from the signed payload.

import json
import hmac
import hashlib
import secrets
import time
from dataclasses import dataclass

from backend import config
from backend import guardrails
from backend import money
from database import repository


@dataclass(frozen=True)
class ApprovalResult:
    ok: bool
    code: str = "OK"
    reason: str = ""
    approval_id: str = ""
    payload: dict = None


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sign(payload_str):
    sig = hmac.new(config.SIGNING_SECRET.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return sig


def create_approval(candidates, budget_cents, source, flags=None, now=None):
    now = time.time() if now is None else now
    flags = flags or {}

    if not config.signing_secret_ok():
        return ApprovalResult(ok=False, code="NO_SIGNING_SECRET", reason="Signing secret not configured.")

    v = guardrails.validate_budget_cents(budget_cents)
    if not v.ok:
        return ApprovalResult(ok=False, code=v.code, reason=v.reason)

    if not candidates or len(candidates) > 10:
        return ApprovalResult(ok=False, code="BAD_REQUEST", reason="Must have between 1 and 10 candidates.")

    seen = set()
    for c in candidates:
        pid = getattr(c, "product_id")
        if pid in seen:
            return ApprovalResult(ok=False, code="DUPLICATE_PRODUCT", reason="Duplicate product id.")
        seen.add(pid)
        offer = getattr(c, "offer")
        if offer is None:
            return ApprovalResult(ok=False, code="NO_OFFER", reason="Missing offer.")
        ev = guardrails.evaluate_offer(offer, budget_cents)
        if not ev.ok:
            return ApprovalResult(ok=False, code=ev.code, reason=ev.reason)

    total_cents = 0
    items = []
    for c in candidates:
        offer = c.offer
        total_cents += int(offer.price_cents)
        items.append({
            "product_id": c.product_id,
            "title": offer.title,
            "domain": getattr(offer, "domain", "") or "",
            "price_cents": int(offer.price_cents),
        })

    if total_cents > budget_cents:
        return ApprovalResult(ok=False, code="OVER_BUDGET", reason="Sum of prices over budget.")

    from backend.agent import step_up

    remaining_cap = max(0, config.DAILY_CAP_CENTS - 0)  # approximated; remaining cap at creation context
    # compute remaining cap based on current spent
    from backend.agent import limits
    remaining_cap = limits.remaining_daily_cap(now)
    step_up_reasons_list = step_up.step_up_reasons(items, total_cents, remaining_cap, flags)

    approval_id = secrets.token_hex(16)
    expires_at = int(now) + int(config.APPROVAL_TTL_SECONDS)
    payload = {
        "items": items,
        "total_cents": int(total_cents),
        "budget_cents": int(budget_cents),
        "source": source,
        "nonce": secrets.token_hex(16),
        "expires_at": int(expires_at),
        "step_up_required": bool(step_up_reasons_list),
        "step_up_reasons": step_up_reasons_list,
    }
    payload_str = _canonical(payload)
    sig = _sign(payload_str)
    repository.insert_approval(approval_id, payload_str, sig, int(now), int(expires_at))
    repository.add_log("human", "approval_created", f"Created approval {approval_id} for {total_cents} cents", approval_id)
    return ApprovalResult(ok=True, code="OK", reason="created", approval_id=approval_id, payload=payload)
def verify_approval(approval_id, now=None):
    from backend import guardrails
    now = time.time() if now is None else now
    row = repository.get_approval(approval_id)
    if not row:
        return guardrails.Verdict(False, "NO_SUCH_APPROVAL", "No such approval.")
    stored_payload = row["payload"]
    stored_sig = row["signature"]
    expected = _sign(stored_payload)
    if not hmac.compare_digest(expected, str(stored_sig)):
        return guardrails.Verdict(False, "BAD_SIGNATURE", "Signature does not match.")
    try:
        payload = json.loads(stored_payload)
    except Exception:
        return guardrails.Verdict(False, "BAD_SIGNATURE", "Invalid payload.")
    if int(row["expires_at"]) < int(now):
        return guardrails.Verdict(False, "EXPIRED", "Approval expired.")
    if row["used_at"] is not None:
        return guardrails.Verdict(False, "ALREADY_USED", "Approval already used.")
    return guardrails.Verdict(True, "OK", "ok"), payload