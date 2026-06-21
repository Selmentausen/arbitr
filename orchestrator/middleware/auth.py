"""
API key authentication middleware.

Workers and the dashboard authenticate via a Bearer token in the Authorization header.
"""

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from orchestrator.config import get_config

security = HTTPBearer()


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> str:
    """
    Verify the API key from the Authorization header.

    Returns the API key if valid, raises 401 if not.
    """
    config = get_config()
    if credentials.credentials != config.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return credentials.credentials
