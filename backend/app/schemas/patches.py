"""Structured patch, review, application, and validation contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PatchStatus = Literal[
    "draft",
    "in_review",
    "approved",
    "rejected",
    "applied",
    "conflict",
    "reverted",
]
PatchFileDecision = Literal["pending", "accepted", "rejected"]
ValidationStatus = Literal["passed", "failed", "timed_out", "skipped"]
PatchActor = Literal["local_user", "model", "system"]


class ProposedPatchChange(BaseModel):
    """One full-content replacement proposed for an existing safe file."""

    model_config = ConfigDict(extra="forbid")

    file: str
    updated_content: str
    reason: str = Field(min_length=1, max_length=1_000)


class CreatePatchRequest(BaseModel):
    """Validated manual or test-facing patch draft input."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    risk: str = Field(min_length=1, max_length=2_000)
    changes: list[ProposedPatchChange] = Field(min_length=1, max_length=10)
    suggested_validators: list[str] = Field(default_factory=list, max_length=10)


class PatchFile(BaseModel):
    """One reviewable file replacement with a stable unified Diff."""

    file: str
    language: str
    reason: str
    base_version: str
    proposed_version: str
    unified_diff: str
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    decision: PatchFileDecision = "pending"


class PatchEvent(BaseModel):
    """One append-only, non-sensitive patch lifecycle event."""

    action: str
    actor: PatchActor
    at: datetime
    detail: str


class ValidatorSpec(BaseModel):
    """One validator from either the built-in or isolated command allowlist."""

    name: str
    title: str
    description: str
    executes_code: bool = False
    execution_kind: Literal["builtin", "sandbox"] = "builtin"
    available: bool = True
    unavailable_reason: str | None = None
    timeout_seconds: float = Field(gt=0)
    max_output_chars: int = Field(ge=1)


class ValidationCheck(BaseModel):
    """Result of one built-in validator."""

    validator: str
    title: str
    status: ValidationStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    exit_code: int | None = None
    output: str


class ValidationRun(BaseModel):
    """One persisted validation run for a patch draft or applied snapshot."""

    validation_id: str
    patch_id: str
    status: ValidationStatus
    created_at: datetime
    checks: list[ValidationCheck]


class PatchResponse(BaseModel):
    """Public snapshot of one durable structured patch."""

    patch_id: str
    task_id: str
    status: PatchStatus
    summary: str
    risk: str
    created_at: datetime
    updated_at: datetime
    files: list[PatchFile]
    suggested_validators: list[str]
    validations: list[ValidationRun]
    events: list[PatchEvent]
    accepted_files: int = Field(ge=0)
    rejected_files: int = Field(ge=0)
    pending_files: int = Field(ge=0)
    can_apply: bool
    can_reject: bool
    can_revert: bool
    can_validate: bool
    can_download: bool


class PatchListResponse(BaseModel):
    """Bounded patches belonging to one task."""

    patches: list[PatchResponse]
    total: int = Field(ge=0)


class ReviewPatchFileRequest(BaseModel):
    """Accept or reject one file in a patch draft."""

    model_config = ConfigDict(extra="forbid")

    file: str
    decision: Literal["accepted", "rejected"]


class ConfirmPatchActionRequest(BaseModel):
    """Explicit second confirmation for apply and revert operations."""

    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


class RunValidationRequest(BaseModel):
    """Optional subset of the fixed validator allowlist."""

    model_config = ConfigDict(extra="forbid")

    validators: list[str] = Field(default_factory=list, max_length=10)
    confirm_execution: bool = False
