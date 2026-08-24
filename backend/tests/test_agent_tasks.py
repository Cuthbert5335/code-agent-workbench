from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.agents import ToolResultSummary
from app.services.agent_tasks import agent_task_service
from app.services.analysis import AnalysisInputError

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_agent_tasks(tmp_path: Path) -> Iterator[None]:
    agent_task_service.use_database_for_test(str(tmp_path / "agent-tasks.db"))
    agent_task_service.clear()
    yield
    agent_task_service.clear()


def create_task(goal: str = "请检查 run 函数和错误处理") -> dict:
    response = client.post(
        "/api/tasks",
        data={"goal": goal},
        files=[
            (
                "files",
                (
                    "src/example.py",
                    b"class Worker:\n    pass\n\ndef run():\n    return Worker()\n",
                    "text/x-python",
                ),
            ),
            (
                "files",
                (
                    "README.md",
                    b"# Example\nUse run to start the project.\n",
                    "text/markdown",
                ),
            ),
        ],
    )
    assert response.status_code == 200
    return response.json()


def wait_for_terminal_task(task_id: str, timeout_seconds: float = 2) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {
            "completed",
            "cancelled",
            "failed",
            "timed_out",
            "blocked",
        }:
            return payload
        time.sleep(0.01)
    raise AssertionError("Agent task did not reach a terminal state in time")


def test_tool_registry_only_exposes_bounded_read_only_tools() -> None:
    response = client.get("/api/tools")

    assert response.status_code == 200
    tools = response.json()
    assert [tool["name"] for tool in tools] == [
        "list_files",
        "project_summary",
        "search_text",
        "search_symbols",
        "inspect_chunks",
    ]
    assert all(tool["permission"] == "read_only" for tool in tools)
    assert all(tool["requires_confirmation"] is True for tool in tools)
    assert all(tool["timeout_seconds"] > 0 for tool in tools)
    assert all(tool["max_output_chars"] > 0 for tool in tools)


def test_create_task_stops_in_plan_mode_before_tool_execution() -> None:
    payload = create_task()

    assert payload["mode"] == "plan"
    assert payload["status"] == "waiting_for_confirmation"
    assert payload["can_confirm"] is True
    assert payload["can_cancel"] is True
    assert payload["tool_calls"] == []
    assert len(payload["plan"]) == 6
    assert all(step["status"] == "pending" for step in payload["plan"])
    assert [transition["to_status"] for transition in payload["transitions"]] == [
        "created",
        "planning",
        "waiting_for_confirmation",
    ]
    assert payload["file_paths"] == ["src/example.py", "README.md"]
    assert any("不会修改文件" in warning for warning in payload["warnings"])


def test_confirm_task_executes_registered_tools_and_completes_trace() -> None:
    task = create_task("请分析 run 的项目结构和相关代码")
    response = client.post(f"/api/tasks/{task['task_id']}/confirm")

    assert response.status_code == 200
    assert response.json()["status"] in {"queued", "executing"}
    payload = wait_for_terminal_task(task["task_id"])
    assert payload["mode"] == "execute"
    assert payload["status"] == "completed"
    assert payload["can_confirm"] is False
    assert payload["can_cancel"] is False
    assert len(payload["tool_calls"]) == 5
    assert all(call["status"] == "completed" for call in payload["tool_calls"])
    assert all(call["duration_ms"] is not None for call in payload["tool_calls"])
    assert all(call["result"] is not None for call in payload["tool_calls"])
    assert all(step["status"] == "completed" for step in payload["plan"])
    assert [transition["to_status"] for transition in payload["transitions"]][-4:] == [
        "executing",
        "reviewing",
        "validating",
        "completed",
    ]
    assert "没有修改文件" in payload["final_answer"]
    symbol_call = next(
        call for call in payload["tool_calls"] if call["tool_name"] == "search_symbols"
    )
    assert any(item["file"] == "src/example.py" for item in symbol_call["result"]["evidence"])


def test_cancel_and_resume_task_returns_to_confirmation() -> None:
    task = create_task()
    cancelled_response = client.post(f"/api/tasks/{task['task_id']}/cancel")

    assert cancelled_response.status_code == 200
    cancelled = cancelled_response.json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["can_resume"] is True
    assert all(step["status"] == "skipped" for step in cancelled["plan"])

    resumed_response = client.post(f"/api/tasks/{task['task_id']}/resume")
    assert resumed_response.status_code == 200
    resumed = resumed_response.json()
    assert resumed["status"] == "waiting_for_confirmation"
    assert resumed["mode"] == "plan"
    assert resumed["can_confirm"] is True
    assert all(step["status"] == "pending" for step in resumed["plan"])


def test_invalid_task_actions_and_missing_task_return_safe_errors() -> None:
    task = create_task()

    resume_response = client.post(f"/api/tasks/{task['task_id']}/resume")
    assert resume_response.status_code == 409
    assert "不能恢复" in resume_response.json()["detail"]

    missing_response = client.get("/api/tasks/missing")
    assert missing_response.status_code == 404
    assert "不存在" in missing_response.json()["detail"]


def test_task_reuses_sensitive_file_filtering_and_lists_tasks() -> None:
    response = client.post(
        "/api/tasks",
        data={"goal": "检查安全文件"},
        files=[
            ("files", (".env", b"SECRET=value", "text/plain")),
            ("files", ("safe.py", b"def visible():\n    pass\n", "text/x-python")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["file_paths"] == ["safe.py"]
    assert any("已跳过 .env" in warning for warning in payload["warnings"])

    list_response = client.get("/api/tasks")
    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["total"] == 1
    assert listed["tasks"][0]["task_id"] == payload["task_id"]


def test_tool_failure_is_traced_and_task_can_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_tool(**_kwargs) -> None:
        raise AnalysisInputError("模拟的受控工具失败。")

    monkeypatch.setattr(
        "app.services.agent_tasks.execute_registered_tool",
        fail_tool,
    )
    task = create_task()
    response = client.post(f"/api/tasks/{task['task_id']}/confirm")

    assert response.status_code == 200
    payload = wait_for_terminal_task(task["task_id"])
    assert payload["status"] == "failed"
    assert payload["can_resume"] is True
    assert payload["tool_calls"][0]["status"] == "failed"
    assert payload["tool_calls"][0]["error"] == "模拟的受控工具失败。"
    assert "失败" in payload["final_answer"]


def test_running_task_can_be_cancelled_and_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_tool(**_kwargs) -> ToolResultSummary:
        await asyncio.sleep(0.1)
        return ToolResultSummary(summary="slow", item_count=0)

    monkeypatch.setattr(
        "app.services.agent_tasks.execute_registered_tool",
        slow_tool,
    )
    task = create_task()
    confirm_response = client.post(f"/api/tasks/{task['task_id']}/confirm")
    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] in {"queued", "executing"}

    cancel_response = client.post(f"/api/tasks/{task['task_id']}/cancel")
    assert cancel_response.status_code == 200
    cancelled = wait_for_terminal_task(task["task_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["can_resume"] is True
    assert len(cancelled["tool_calls"]) <= 1

    resume_response = client.post(f"/api/tasks/{task['task_id']}/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "waiting_for_confirmation"


def test_tool_timeout_marks_task_timed_out_and_resumable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timeout_tool(**_kwargs) -> None:
        raise TimeoutError

    monkeypatch.setattr(
        "app.services.agent_tasks.execute_registered_tool",
        timeout_tool,
    )
    task = create_task()
    confirm_response = client.post(f"/api/tasks/{task['task_id']}/confirm")
    assert confirm_response.status_code == 200

    payload = wait_for_terminal_task(task["task_id"])
    assert payload["status"] == "timed_out"
    assert payload["can_resume"] is True
    assert payload["tool_calls"][0]["status"] == "timed_out"
    assert "超时" in payload["final_answer"]
