"""Syntax-light code-symbol extraction endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.schemas.symbols import SymbolResponse
from app.services.analysis import AnalysisInputError
from app.services.symbols import find_code_symbols

router = APIRouter(prefix="/api", tags=["symbols"])


@router.post("/symbols", response_model=SymbolResponse)
async def list_symbols(
    files: Annotated[list[UploadFile], File()],
    query: Annotated[str | None, Form()] = None,
) -> SymbolResponse:
    """Return bounded declarations from explicitly selected source files."""

    try:
        return await find_code_symbols(
            uploads=files,
            settings=settings,
            query=query,
        )
    except AnalysisInputError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error

