"""Authenticated usage summaries and shared HTTP 429 mapping."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.auth import CurrentAuth, security_http_error
from app.config import settings
from app.schemas.usage import UsageSummaryResponse
from app.services.security import SecurityError, security_service
from app.services.usage import UsageLimitError, usage_service

router = APIRouter(prefix="/api", tags=["usage"])


def usage_http_error(error: UsageLimitError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail(),
        headers=error.headers(),
    )


@router.get("/usage", response_model=UsageSummaryResponse)
async def get_usage(
    authenticated: CurrentAuth,
    project_id: str | None = None,
) -> UsageSummaryResponse:
    """Return the caller's rolling usage and optional visible project usage."""

    try:
        if project_id is not None:
            security_service.require_project_permission(
                project_id,
                authenticated.user,
                "read",
            )
        return usage_service.summary(
            user_id=authenticated.user.user_id,
            project_id=project_id,
            settings=settings,
        )
    except SecurityError as error:
        raise security_http_error(error) from error
