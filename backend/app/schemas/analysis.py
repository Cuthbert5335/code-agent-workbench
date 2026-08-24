"""Structured data models for code analysis requests and responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ConversationMessage(BaseModel):
    """One recent user or assistant message supplied by the browser."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10_000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        cleaned_value = value.strip()
        if not cleaned_value:
            raise ValueError("conversation message content cannot be blank")
        return cleaned_value


class FileReference(BaseModel):
    """A file range that was included in the generated model context."""

    file: str
    language: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    truncated: bool = False


class AnalysisStats(BaseModel):
    """Non-sensitive processing statistics returned to the browser."""

    received_files: int = Field(ge=0)
    accepted_files: int = Field(ge=0)
    skipped_files: int = Field(ge=0)
    context_chars: int = Field(ge=0)
    conversation_messages: int = Field(ge=0)


class AnalysisResponse(BaseModel):
    """Stable response contract shared by demo and real model modes."""

    answer: str
    references: list[FileReference]
    mode: Literal["demo", "real"]
    warnings: list[str]
    stats: AnalysisStats

