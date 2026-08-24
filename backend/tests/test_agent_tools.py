from __future__ import annotations

import asyncio

import pytest

from app.schemas.agents import ToolEvidence, ToolResultSummary
from app.services.agent_tools import (
    TOOL_REGISTRY,
    ToolContext,
    bound_result,
    execute_registered_tool,
    validate_arguments,
)
from app.services.analysis import AcceptedFile, AnalysisInputError


def tool_context() -> ToolContext:
    return ToolContext(
        files=(
            AcceptedFile(
                path="src/example.py",
                language="Python",
                content="def run():\n    return 1\n",
            ),
        ),
    )


def test_validate_tool_arguments_rejects_unknown_missing_and_oversized_values() -> None:
    search_spec = TOOL_REGISTRY["search_text"].spec

    with pytest.raises(AnalysisInputError, match="未知参数"):
        validate_arguments(search_spec, {"query": "run", "command": "rm"})
    with pytest.raises(AnalysisInputError, match="缺少必填参数"):
        validate_arguments(search_spec, {})
    with pytest.raises(AnalysisInputError, match="不能超过"):
        validate_arguments(search_spec, {"query": "x" * 501})


def test_execute_registered_tool_rejects_unregistered_name() -> None:
    with pytest.raises(AnalysisInputError, match="未注册"):
        asyncio.run(
            execute_registered_tool(
                name="run_shell",
                arguments={},
                context=tool_context(),
            ),
        )


def test_bound_result_enforces_character_and_evidence_limits() -> None:
    result = ToolResultSummary(
        summary="s" * 1_000,
        item_count=40,
        evidence=[
            ToolEvidence(
                file=f"file_{index}.py",
                label="label" * 40,
                preview="preview" * 80,
            )
            for index in range(40)
        ],
    )

    bounded = bound_result(result, max_chars=500)

    assert len(bounded.summary) <= 501
    assert len(bounded.evidence) < 40
    assert bounded.truncated is True


def test_execute_registered_tool_applies_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = TOOL_REGISTRY["list_files"]

    async def slow_handler(
        _context: ToolContext,
        _arguments: dict,
    ) -> ToolResultSummary:
        await asyncio.sleep(0.05)
        return ToolResultSummary(summary="done", item_count=0)

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "list_files",
        definition.__class__(
            spec=definition.spec.model_copy(update={"timeout_seconds": 0.001}),
            handler=slow_handler,
        ),
    )

    with pytest.raises(TimeoutError):
        asyncio.run(
            execute_registered_tool(
                name="list_files",
                arguments={},
                context=tool_context(),
            ),
        )
