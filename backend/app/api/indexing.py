"""In-memory project indexing endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import settings
from app.schemas.indexing import IndexResponse
from app.services.analysis import AnalysisInputError
from app.services.indexing import build_project_index

router = APIRouter(prefix="/api", tags=["indexing"])


@router.post("/index", response_model=IndexResponse)
async def index_project(
    files: Annotated[list[UploadFile], File()],
) -> IndexResponse:
    """Build a non-persistent index for explicitly selected safe files."""

    try:
        return await build_project_index(uploads=files, settings=settings)
    except AnalysisInputError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error

