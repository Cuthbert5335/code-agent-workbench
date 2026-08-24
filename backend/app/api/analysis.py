"""Code analysis HTTP endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.api.auth import OptionalAuth, request_id
from app.api.usage import usage_http_error
from app.config import settings
from app.providers.openai_compatible import ModelProviderError
from app.schemas.analysis import AnalysisResponse
from app.services.analysis import AnalysisInputError, analyze_code_request
from app.services.usage import UsageContext, UsageLimitError

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/analyze", response_model=AnalysisResponse)
@router.post(
    "/analysis",
    response_model=AnalysisResponse,
    deprecated=True,
    description="Compatibility alias for POST /api/analyze.",
)
async def analyze_code(
    question: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    request: Request,
    authenticated: OptionalAuth,
    conversation: Annotated[str | None, Form()] = None,
) -> AnalysisResponse:
    """Validate uploaded code and analyze it in real or demo mode."""

    try:
        return await analyze_code_request(
            question=question,
            uploads=files,
            raw_conversation=conversation,
            settings=settings,
            usage_context=UsageContext(
                user_id=authenticated.user.user_id if authenticated else None,
            ),
            request_id=request_id(request),
        )
    except AnalysisInputError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
    except ModelProviderError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
    except UsageLimitError as error:
        raise usage_http_error(error) from error
