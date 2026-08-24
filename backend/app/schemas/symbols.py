"""Structured models for syntax-light source symbol extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SymbolKind = Literal[
    "function",
    "class",
    "interface",
    "type",
    "enum",
    "struct",
    "trait",
    "module",
]


class CodeSymbol(BaseModel):
    """One recognized declaration in an explicitly selected source file."""

    name: str
    kind: SymbolKind
    file: str
    language: str
    line_number: int = Field(ge=1)
    declaration: str


class SymbolStats(BaseModel):
    """Non-sensitive counts for one symbol extraction request."""

    received_files: int = Field(ge=0)
    accepted_files: int = Field(ge=0)
    skipped_files: int = Field(ge=0)
    symbol_files: int = Field(ge=0)
    symbols: int = Field(ge=0)


class SymbolResponse(BaseModel):
    """Filtered, bounded symbols returned by the API."""

    query: str | None
    symbols: list[CodeSymbol]
    warnings: list[str]
    truncated: bool = False
    stats: SymbolStats
