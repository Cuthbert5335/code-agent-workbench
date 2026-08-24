"""Public contracts for the phase-four single-Agent workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AgentMode = Literal["plan", "execute"]
AgentTaskStatus = Literal[
    "created",
    "planning",
    "waiting_for_confirmation",
    "queued",
    "executing",
    "reviewing",
    "validating",
    "completed",
    "cancelled",
    "failed",
    "timed_out",
    "blocked",
]
PlanStepStatus = Literal["pending", "in_progress", "completed", "skipped", "failed"]
ToolCallStatus = Literal["pending", "running", "completed", "failed", "cancelled", "timed_out"]
TaskQueueStatus = Literal["queued", "running", "completed", "cancelled", "failed"]


class ToolParameter(BaseModel):
    """One validated input accepted by a registered Agent tool."""

    name: str
    type: Literal["string"]
    required: bool
    description: str
    max_length: int | None = Field(default=None, ge=1)


class ToolSpec(BaseModel):
    """Safe metadata exposed for one registered tool."""

    name: str
    title: str
    description: str
    permission: Literal["read_only"] = "read_only"
    requires_confirmation: bool = True
    timeout_seconds: float = Field(gt=0)
    max_output_chars: int = Field(ge=1)
    parameters: list[ToolParameter]


class TaskPlanStep(BaseModel):
    """One readable and traceable step in an Agent task plan."""

    id: str
    position: int = Field(ge=1)
    title: str
    description: str
    status: PlanStepStatus
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False


class ToolEvidence(BaseModel):
    """A bounded, user-displayable fact produced by a read-only tool."""

    file: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    label: str
    preview: str | None = None


class ToolResultSummary(BaseModel):
    """Bounded output retained in the task trajectory."""

    summary: str
    item_count: int = Field(ge=0)
    truncated: bool = False
    evidence: list[ToolEvidence] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    """Audit-friendly lifecycle for one controlled tool call."""

    id: str
    tool_name: str
    title: str
    status: ToolCallStatus
    arguments: dict[str, Any]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    result: ToolResultSummary | None = None
    error: str | None = None


class TaskTransition(BaseModel):
    """One explicit state change retained for the task history."""

    from_status: AgentTaskStatus | None
    to_status: AgentTaskStatus
    at: datetime
    reason: str


class TaskQueueState(BaseModel):
    """Public scheduling state without exposing the private lease token."""

    status: TaskQueueStatus
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    available_at: datetime
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    cancel_requested: bool
    last_error: str | None = None


class AgentTaskResponse(BaseModel):
    """Complete public snapshot of one durable Agent task."""

    task_id: str
    project_id: str | None = None
    goal: str
    mode: AgentMode
    status: AgentTaskStatus
    created_at: datetime
    updated_at: datetime
    file_count: int = Field(ge=1)
    file_paths: list[str]
    plan: list[TaskPlanStep]
    tool_calls: list[ToolCallRecord]
    transitions: list[TaskTransition]
    final_answer: str | None = None
    warnings: list[str]
    queue: TaskQueueState | None = None
    can_confirm: bool
    can_cancel: bool
    can_resume: bool


class AgentTaskListResponse(BaseModel):
    """Bounded list of visible durable task snapshots."""

    tasks: list[AgentTaskResponse]
    total: int = Field(ge=0)
