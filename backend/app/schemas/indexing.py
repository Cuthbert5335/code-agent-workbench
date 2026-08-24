"""Structured models for an in-memory project index summary."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IndexStatus = Literal["completed", "partial"]


class IndexedFile(BaseModel):
    """Metadata and derived counts for one accepted project file."""

    file: str
    language: str
    size_chars: int = Field(ge=0)
    lines: int = Field(ge=0)
    chunks: int = Field(ge=0)
    symbols: int = Field(ge=0)


class IndexStats(BaseModel):
    """Non-sensitive totals for one in-memory indexing request."""

    received_files: int = Field(ge=0)
    accepted_files: int = Field(ge=0)
    skipped_files: int = Field(ge=0)
    indexed_files: int = Field(ge=0)
    chunks: int = Field(ge=0)
    symbols: int = Field(ge=0)
    content_chars: int = Field(ge=0)


class IndexResponse(BaseModel):
    """Current in-memory index snapshot for explicitly selected files."""

    status: IndexStatus
    files: list[IndexedFile]
    warnings: list[str]
    stats: IndexStats

