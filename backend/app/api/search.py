"""Literal code-search HTTP endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.schemas.search import SearchResponse
from app.services.analysis import AnalysisInputError
from app.services.search import search_code_files

router = APIRouter(prefix="/api", tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search_code(
    query: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
) -> SearchResponse:
    """Search explicitly uploaded source files using a bounded literal query."""

    try:
        return await search_code_files(
            query=query,
            uploads=files,
            settings=settings,
        )
    except AnalysisInputError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error

