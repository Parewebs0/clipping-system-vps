"""Authenticated diagnostic endpoint."""
from fastapi import APIRouter, Depends

from app.auth import require_bearer
from app.config import settings


router = APIRouter()

# Same version string as FastAPI(version=...) in app/main.py.
APP_VERSION = "0.3.0"


@router.get("/system/info")
def system_info(_: bool = Depends(require_bearer)) -> dict:
    return {
        "service": "clipping-api",
        "version": APP_VERSION,
        "environment": settings.environment,
        "api_host": settings.api_host,
        "api_port": settings.api_port,
        "log_level": settings.log_level,
    }
