"""Deterministic source-code chunking endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import settings
from app.schemas.chunks import ChunkResponse
from app.services.analysis import AnalysisInputError
from app.services.chunks import build_code_chunks

router = APIRouter(prefix="/api", tags=["chunks"])


@router.post("/chunks", response_model=ChunkResponse)
async def chunk_code(
    files: Annotated[list[UploadFile], File()],
) -> ChunkResponse:
    """Return bounded chunks from explicitly uploaded safe source files."""

    try:
        return await build_code_chunks(uploads=files, settings=settings)
    except AnalysisInputError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error

