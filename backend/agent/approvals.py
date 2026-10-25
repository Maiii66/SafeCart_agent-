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


def _refuse(code, reason):
    return ApprovalResult(ok=False, code=code, reason=reason)


def create_approval(candidates, budget_cents, source, flags=None, now=None):
    """Turn the products the person chose into ONE signed, expiring approval.

    candidates: a list of models.Candidate (a Product plus the one Offer we would buy).
    source:     "channel3" or "demo": where the live price must be re-checked later.
    flags:      {product_id: ("suspicious_price", ...)} from the ranking step.
    Returns an ApprovalResult. Refusals are results, not exceptions.
    """
    # step_up and limits are imported here, not at the top, because step_up.py also
    # imports this file and Python cannot import two files into each other at load time.
    from backend.agent import limits, step_up

    now = time.time() if now is None else now
    flags = flags or {}

    if not config.signing_secret_ok():
        return _refuse("NO_SIGNING_SECRET", "The signing secret is missing or shorter than 32 characters.")

    budget = guardrails.validate_budget_cents(budget_cents)
    if not budget.ok:
        return _refuse(budget.code, budget.reason)

    if not candidates or len(candidates) > 10:
        return _refuse("BAD_REQUEST", "An approval needs between 1 and 10 products.")

    # Check every product against the spending rule, and build the items we will sign.
    items = []
    seen_ids = set()
    total_cents = 0
    for candidate in candidates:
        product, offer = candidate.product, candidate.offer
        if offer is None:
            return _refuse("NO_OFFER", f"{product.title} has no offer to buy.")
        if product.id in seen_ids:
            return _refuse("DUPLICATE_PRODUCT", f"{product.title} is in the basket twice.")
        seen_ids.add(product.id)

        verdict = guardrails.evaluate_offer(offer, budget_cents)
        if not verdict.ok:
            return _refuse(verdict.code, f"{product.title}: {verdict.reason}")

        total_cents += offer.price_cents
        items.append({
            "product_id": product.id,
            "title": product.title,
            "domain": offer.domain,
            "price_cents": offer.price_cents,
        })

    # Each item can fit the budget while the whole basket does not, so check the sum too.
    if total_cents > budget_cents:
        return _refuse("OVER_BUDGET", "The items together cost more than the budget.")

    # Rule 6 is decided NOW and written INSIDE the signed payload, so nobody can switch it off later.
    remaining_cap = limits.remaining_daily_cap(now)
    reasons = step_up.step_up_reasons(items, total_cents, remaining_cap, flags)

    approval_id = secrets.token_hex(16)
    expires_at = int(now) + int(config.APPROVAL_TTL_SECONDS)
    payload = {
        "items": items,
        "total_cents": total_cents,
        "budget_cents": budget_cents,
        "source": source,
        "nonce": secrets.token_hex(16),
        "expires_at": expires_at,
        "step_up_required": bool(reasons),
        "step_up_reasons": reasons,
    }
    payload_text = _canonical(payload)
    repository.insert_approval(approval_id, payload_text, _sign(payload_text), int(now), expires_at)
    repository.add_log("human", "approval_created",
                       f"Approval for {money.format_usd(total_cents)} across {len(items)} item(s)",
                       approval_id)
    return ApprovalResult(ok=True, code="OK", reason="created", approval_id=approval_id, payload=payload)


def verify_approval(approval_id, now=None):
    """Check an approval. ALWAYS returns a pair: (Verdict, payload or None).

    The payload is only returned when every check passed. Callers can always write
        verdict, payload = verify_approval(approval_id)
    and never need to guess which shape they got.
    """
    now = time.time() if now is None else now
    row = repository.get_approval(approval_id)
    if not row:
        return guardrails.Verdict(False, "NO_SUCH_APPROVAL", "No such approval."), None

    stored_payload = row["payload"]
    # Recompute the signature from the stored text. Any edit to the payload breaks the match.
    expected = _sign(stored_payload)
    if not hmac.compare_digest(expected, str(row["signature"])):
        return guardrails.Verdict(False, "BAD_SIGNATURE", "Signature does not match."), None
    try:
        payload = json.loads(stored_payload)
    except ValueError:
        return guardrails.Verdict(False, "BAD_SIGNATURE", "Approval payload is unreadable."), None

    if int(row["expires_at"]) < int(now):
        return guardrails.Verdict(False, "EXPIRED", "Approval expired."), None
    if row["used_at"] is not None:
        return guardrails.Verdict(False, "ALREADY_USED", "Approval already used."), None
    return guardrails.Verdict(True, "OK", "ok"), payload