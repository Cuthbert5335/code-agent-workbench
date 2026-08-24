"""Authenticated data-retention policy endpoint."""

from fastapi import APIRouter

from app.api.auth import CurrentAuth
from app.config import settings
from app.schemas.security import RetentionPolicyResponse
from app.services.retention import retention_service

router = APIRouter(prefix="/api", tags=["retention"])


@router.get("/retention", response_model=RetentionPolicyResponse)
async def get_retention_policy(_authenticated: CurrentAuth) -> RetentionPolicyResponse:
    """Return the configured durable-data expiry policy."""

    return retention_service.policy(settings)
