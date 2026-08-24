"""Structured models for deterministic source-code chunks."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CodeChunk(BaseModel):
    """One bounded source fragment with stable file and line metadata."""

    file: str
    language: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content: str
    truncated: bool = False


class ChunkStats(BaseModel):
    """Non-sensitive counts describing one chunking request."""

    received_files: int = Field(ge=0)
    accepted_files: int = Field(ge=0)
    skipped_files: int = Field(ge=0)
    chunked_files: int = Field(ge=0)
    chunks: int = Field(ge=0)
    content_chars: int = Field(ge=0)


class ChunkResponse(BaseModel):
    """Bounded chunks and processing metadata returned by the API."""

    chunks: list[CodeChunk]
    warnings: list[str]
    truncated: bool = False
    stats: ChunkStats

