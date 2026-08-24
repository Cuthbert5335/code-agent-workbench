"""Public contracts for the fail-closed container sandbox."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SandboxStatusResponse(BaseModel):
    """Runtime readiness without leaking host paths or daemon details."""

    available: bool
    runtime: str
    isolation: Literal["container"] = "container"
    network: Literal["disabled"] = "disabled"
    root_filesystem: Literal["read_only"] = "read_only"
    workspace: Literal["temporary"] = "temporary"
    reason: str | None = None
    allowed_commands: list[str]
