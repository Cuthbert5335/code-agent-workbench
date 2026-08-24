"""Read-only readiness endpoint for the container sandbox."""

from fastapi import APIRouter

from app.config import settings
from app.schemas.sandbox import SandboxStatusResponse
from app.services.sandbox import sandbox_service

router = APIRouter(prefix="/api", tags=["sandbox"])


@router.get("/sandbox/status", response_model=SandboxStatusResponse)
async def sandbox_status() -> SandboxStatusResponse:
    """Report whether isolated command execution is currently available."""

    return sandbox_service.status(settings)
