# backend/api/logs.py
from fastapi import APIRouter, Query

from backend.api.schemas import LogsResponse, LogSchema
from database import repository

router = APIRouter()


@router.get("/api/logs", response_model=LogsResponse)
def list_logs(limit: int = Query(200, ge=1, le=500)):
    logs = repository.list_logs(limit=limit)
    return LogsResponse(logs=[LogSchema(**log) for log in logs])
