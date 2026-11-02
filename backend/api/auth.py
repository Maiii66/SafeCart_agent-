# backend/api/auth.py
# One shared check for every /api route: the caller must send the configured key
# in the X-API-Key header. A missing or empty server key fails closed with 503.

import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from backend import config

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(provided: str = Security(api_key_header)) -> None:
    if not config.api_key_ok():
        raise HTTPException(status_code=503,
                            detail="The server is not configured with an API key.")
    if provided is None or not hmac.compare_digest(
            provided.encode("utf-8"), config.API_KEY.encode("utf-8")):
        raise HTTPException(status_code=401,
                            detail="A valid X-API-Key header is required.")
