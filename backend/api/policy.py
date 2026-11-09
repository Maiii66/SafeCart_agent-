# backend/api/policy.py
# Plain-English spending rules are proposed by the AI, reviewed by the person, then
# clamped and saved by plain Python. The person's confirmation is the only thing that saves it.

from fastapi import APIRouter

from backend.agent import policy as policy_rules
from backend.agent import policy_parser
from backend.api.schemas import (
    PolicyDeleteResponse,
    PolicyProposalResponse,
    PolicyProposeRequest,
    PolicySchema,
    PolicyStateResponse,
)
from database import repository

router = APIRouter()


def _state(policy, notices=None, code="OK", reason=""):
    return PolicyStateResponse(
        ok=True,
        code=code,
        reason=reason,
        policy=PolicySchema(**policy) if isinstance(policy, dict) else None,
        description=policy_rules.describe(policy),
        summary=policy_rules.summary(policy),
        clamped=list(notices or []),
    )


@router.get("/api/policy", response_model=PolicyStateResponse)
def get_policy():
    return _state(policy_rules.get_policy())


@router.post("/api/policy/propose", response_model=PolicyProposalResponse)
def propose_policy(req: PolicyProposeRequest):
    try:
        proposal = policy_parser.propose_policy(req.text)
    except policy_parser.PolicyParseError as error:
        repository.add_log(
            "guardrail",
            "policy_propose_refused",
            f"{error.code}: {error}",
            None,
        )
        return PolicyProposalResponse(ok=False, code=error.code, reason=str(error))

    proposed = proposal["policy"]
    return PolicyProposalResponse(
        ok=True,
        code="OK",
        reason="Review these rules, then confirm them.",
        policy=PolicySchema(**proposed),
        clamped=proposal["clamped"],
        description=policy_rules.describe(proposed),
        summary=policy_rules.summary(proposed),
        source=proposal["source"],
    )


@router.post("/api/policy/confirm", response_model=PolicyStateResponse)
def confirm_policy(req: PolicySchema):
    existing = policy_rules.get_policy()
    try:
        clamped, notices = policy_rules.clamp_policy(req.model_dump(exclude_none=True))
    except policy_rules.PolicyError as error:
        return PolicyStateResponse(ok=False, code="BAD_POLICY", reason=str(error))

    if not policy_rules.has_rules(clamped):
        return PolicyStateResponse(
            ok=False,
            code="BAD_POLICY",
            reason="A policy must include at least one spending rule.",
        )

    policy_rules.save_policy(clamped)
    action = "policy_replaced" if policy_rules.has_rules(existing) else "policy_confirmed"
    repository.add_log("human", action, policy_rules.summary(clamped), None)
    return _state(clamped, notices)


@router.delete("/api/policy", response_model=PolicyDeleteResponse)
def delete_policy():
    deleted = policy_rules.delete_policy()
    if deleted:
        repository.add_log("human", "policy_deleted", "Spending policy deleted", None)
    return PolicyDeleteResponse(
        ok=True,
        code="OK",
        reason="Spending policy deleted." if deleted else "There was no spending policy to delete.",
        deleted=bool(deleted),
    )
