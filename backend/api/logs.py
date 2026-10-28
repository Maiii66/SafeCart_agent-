# backend/api/logs.py
from fastapi import APIRouter, HTTPException

from backend.api.schemas import LogsResponse, LogSchema
from database import repository

router = APIRouter()


@router.get("/api/logs", response_model=LogsResponse)
def list_logs(limit: int = 200):
    try:
        logs = repository.list_logs(limit=limit)
        return LogsResponse(logs=[LogSchema(**log) for log in logs])
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to list logs")
