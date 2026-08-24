"""Structured models for safe literal text search."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchMatch(BaseModel):
    """One matching source line with a small amount of surrounding context."""

    file: str
    language: str
    line_number: int = Field(ge=1)
    column: int = Field(ge=1)
    match_count: int = Field(ge=1)
    line: str
    before: list[str]
    after: list[str]
    line_truncated: bool = False


class SearchStats(BaseModel):
    """Non-sensitive file and match counts for one search request."""

    received_files: int = Field(ge=0)
    accepted_files: int = Field(ge=0)
    skipped_files: int = Field(ge=0)
    matched_files: int = Field(ge=0)
    matched_lines: int = Field(ge=0)


class SearchResponse(BaseModel):
    """Stable response returned by the literal text search endpoint."""

    query: str
    results: list[SearchMatch]
    warnings: list[str]
    truncated: bool = False
    stats: SearchStats

