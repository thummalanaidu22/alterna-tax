from fastapi import Security, HTTPException, status
from fastapi.security.api_key import APIKeyHeader

from .config import settings

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str = Security(_API_KEY_HEADER)) -> None:
    """Reject requests with a wrong or missing API key.
    If API_KEY is not set in the environment, all requests pass (local dev mode)."""
    if not settings.api_key:
        return
    if api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key. Provide it via the X-API-Key header.",
        )
