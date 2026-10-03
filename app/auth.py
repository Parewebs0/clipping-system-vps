"""Bearer token authentication for protected endpoints.

Two tokens (issue #11):

* ``API_TOKEN`` — the historical token, shared with the Windows worker.
* ``API_WRITE_TOKEN`` (optional) — dashboard/operator token. When it is set:
    - read endpoints (``require_bearer``) accept either token;
    - dashboard write endpoints (``require_write_bearer``: create / PATCH /
      status / DELETE on /campaigns and approve / reject on /candidates,
      issue #17) accept ONLY the write token, so the worker's token can no
      longer edit campaigns or approve clips. The worker only calls
      /worker/* (``require_bearer``), so it keeps working with API_TOKEN.
  When it is empty, behaviour is unchanged: API_TOKEN does everything.
"""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings


security = HTTPBearer(auto_error=True)


def _matches(expected: str, provided: str) -> bool:
    return bool(expected) and secrets.compare_digest(expected.encode(), provided.encode())


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_bearer(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> bool:
    provided = credentials.credentials
    if _matches(settings.api_token, provided) or _matches(settings.api_write_token, provided):
        return True
    raise _unauthorized()


def require_write_bearer(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> bool:
    provided = credentials.credentials
    write_token = settings.api_write_token
    if not write_token:
        if _matches(settings.api_token, provided):
            return True
        raise _unauthorized()
    if _matches(write_token, provided):
        return True
    if _matches(settings.api_token, provided):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this token is read-only for dashboard writes; use API_WRITE_TOKEN",
        )
    raise _unauthorized()
