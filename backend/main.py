# backend/main.py
import logging
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api import health, shop, approvals, checkout, orders, logs, status
from backend.api.auth import require_api_key

logger = logging.getLogger("safecart")

app = FastAPI(title="SafeCart API", version="1.0.0")

app.include_router(health.router)

api_dependencies = [Depends(require_api_key)]
app.include_router(shop.router, dependencies=api_dependencies)
app.include_router(approvals.router, dependencies=api_dependencies)
app.include_router(checkout.router, dependencies=api_dependencies)
app.include_router(orders.router, dependencies=api_dependencies)
app.include_router(logs.router, dependencies=api_dependencies)
app.include_router(status.router, dependencies=api_dependencies)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    error_id = uuid.uuid4().hex[:8]
    logger.exception("Unhandled error %s on %s %s", error_id, request.method, request.url.path)
    return JSONResponse(status_code=500,
                        content={"detail": "Internal error", "error_id": error_id})


@app.get("/")
def root():
    return {"name": "SafeCart API", "docs": "/docs"}
