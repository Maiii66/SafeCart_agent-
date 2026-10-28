# backend/main.py
from fastapi import FastAPI

from backend.api import health, shop, approvals, checkout, orders, logs

app = FastAPI(title="SafeCart API", version="1.0.0")

app.include_router(health.router)
app.include_router(shop.router)
app.include_router(approvals.router)
app.include_router(checkout.router)
app.include_router(orders.router)
app.include_router(logs.router)


@app.get("/")
def root():
    return {"name": "SafeCart API", "docs": "/docs"}
