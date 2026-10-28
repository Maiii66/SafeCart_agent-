# backend/api/health.py
from fastapi import APIRouter

from backend.api.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok")
