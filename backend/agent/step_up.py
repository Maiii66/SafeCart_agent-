# backend/agent/step_up.py
# RULE 6: step-up approval. A bigger or riskier purchase needs a second, typed confirmation.
#   step_up_reasons() decides WHEN it is needed. The answer is signed into the approval.
#   confirm_step_up() checks what the person typed.

from backend import config
from backend import guardrails
from backend.money import MoneyError, parse_dollars_to_cents
from backend.agent import approvals
from database import repository


def normalise_domain(domain):
    """'  WWW.Shop.Example ' -> 'shop.example'. Empty or missing input becomes ''."""
    text = str(domain or "").strip().lower()
    return text[4:] if text.startswith("www.") else text


def is_trusted(domain):
    """A merchant is trusted if it IS a trusted domain or a sub-domain of one.

    'shop.bestbuy.com' is trusted. 'evilbestbuy.com' and 'bestbuy.com.evil.example' are not,
    because a sub-domain must be separated by a dot and the trusted name must be at the END.
    """
    name = normalise_domain(domain)
    if not name:
        return False
    return any(name == trusted or name.endswith("." + trusted) for trusted in config.TRUSTED_MERCHANTS)


def step_up_reasons(items, total_cents, remaining_cap_cents, flags=None):
    """Return the list of reasons this purchase needs a typed confirmation (empty = one click is enough).

    items:               [{"product_id", "domain", ...}] exactly as stored in the signed approval
    remaining_cap_cents: what is left of the daily spending cap
    flags:               {product_id: ("suspicious_price", ...)}
    """
    flags = flags or {}
    reasons = []

    if total_cents > config.STEP_UP_THRESHOLD_CENTS:
        reasons.append("total_above_threshold")

    if any("suspicious_price" in flags.get(item["product_id"], ()) for item in items):
        reasons.append("suspicious_price")

    if any(not is_trusted(item.get("domain")) for item in items):
        reasons.append("untrusted_merchant")

    # "More than half of what is left of the daily cap", using whole-number maths:
    # total > remaining / 2   is the same as   total * 2 > remaining.
    # If nothing is left (remaining is 0), any purchase counts.
    if total_cents * 2 > remaining_cap_cents:
        reasons.append("over_half_remaining_cap")

    return reasons


def _matches_total(typed, total_cents):
    """True if the typed text is a dollar amount equal to the signed total."""
    try:
        return parse_dollars_to_cents(typed) == total_cents
    except MoneyError:
        return False


def confirm_step_up(approval_id, typed_text, now=None):
    """Accept ONLY the word CONFIRM or the exact total from the signed approval.

    Returns a guardrails.Verdict. Anything else is refused and logged.
    """
    verdict, payload = approvals.verify_approval(approval_id, now=now)
    if not verdict.ok:
        repository.add_log("human", "step_up_refused", f"Step-up refused: {verdict.code}", approval_id)
        return verdict

    typed = str(typed_text or "").strip()
    if typed.upper() == "CONFIRM" or _matches_total(typed, payload["total_cents"]):
        repository.set_step_up_confirmed(approval_id, 1)
        repository.add_log("human", "step_up_confirmed", "Step-up confirmed", approval_id)
        return guardrails.Verdict(True, "OK", "Step-up confirmed.")

    repository.add_log("human", "step_up_refused", "The typed confirmation did not match", approval_id)
    return guardrails.Verdict(False, "STEP_UP_REFUSED",
                              "Type the exact total or the word CONFIRM to approve this purchase.")