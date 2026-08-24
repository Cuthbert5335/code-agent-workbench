"""Public contracts for rolling usage and concurrency limits."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

UsageResource = Literal["model_calls", "files", "patches", "validations"]
UsageScopeKind = Literal["user", "project", "legacy_local"]


class UsageMetric(BaseModel):
    resource: UsageResource
    used: int = Field(ge=0)
    limit: int = Field(ge=1)
    remaining: int = Field(ge=0)
    next_reset_at: datetime | None


class UsageScopeSummary(BaseModel):
    scope: UsageScopeKind
    scope_id: str | None
    window_seconds: int = Field(ge=60)
    active_tasks: int = Field(ge=0)
    active_task_limit: int = Field(ge=1)
    metrics: list[UsageMetric]


class UsageSummaryResponse(BaseModel):
    user: UsageScopeSummary
    project: UsageScopeSummary | None = None
