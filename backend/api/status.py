# backend/api/status.py
from fastapi import APIRouter

from backend import config
from backend.agent import limits
from backend.api.schemas import StatusResponse

router = APIRouter()


def _status():
    spent = limits.spent_last_24h()
    return StatusResponse(
        paused=limits.is_paused(),
        sandbox=config.is_sandbox(),
        paypal_env=config.PAYPAL_ENV,
        paypal_enabled=config.paypal_enabled(),
        channel3_enabled=config.channel3_enabled(),
        gemini_enabled=bool(config.GEMINI_API_KEY),
        currency=config.CURRENCY,
        max_budget_cents=config.MAX_BUDGET_CENTS,
        daily_cap_cents=config.DAILY_CAP_CENTS,
        spent_last_24h_cents=spent,
        remaining_daily_cap_cents=max(0, config.DAILY_CAP_CENTS - spent),
        max_orders_per_hour=config.MAX_ORDERS_PER_HOUR,
        step_up_threshold_cents=config.STEP_UP_THRESHOLD_CENTS,
    )


@router.get("/api/status", response_model=StatusResponse)
def get_status():
    return _status()


@router.post("/api/pause", response_model=StatusResponse)
def pause():
    limits.set_paused(True)
    return _status()


@router.post("/api/resume", response_model=StatusResponse)
def resume():
    limits.set_paused(False)
    return _status()
