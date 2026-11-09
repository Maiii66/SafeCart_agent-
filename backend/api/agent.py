from fastapi import APIRouter

from backend.agent import agent
from backend.api.schemas import AgentRequest, AgentResponse

router = APIRouter()


@router.post("/api/agent/run", response_model=AgentResponse)
def run_agent(req: AgentRequest):
    try:
        trace, proposal, mode = agent.run(req.goal, req.budget_cents)
    except agent.AgentError as error:
        return AgentResponse(ok=False, code="BAD_AGENT_REQUEST", reason=str(error))
    return AgentResponse(ok=True, code="OK", reason="", trace=trace, proposal=proposal, mode=mode)
