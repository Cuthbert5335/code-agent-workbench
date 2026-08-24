from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.agent_tasks import agent_task_service
from app.services.analysis import AnalysisInputError
from app.storage import database
from app.storage.task_queue import QueueLeaseLostError, task_queue_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_queue_database(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "task-queue.db"
    agent_task_service.use_database_for_test(str(path))
    agent_task_service.clear()
    yield path
    agent_task_service.clear()


def create_task(*, goal: str = "检查 run 函数", key: str | None = None) -> dict:
    headers = {"Idempotency-Key": key} if key else None
    response = client.post(
        "/api/tasks",
        headers=headers,
        data={"goal": goal},
        files=[("files", ("main.py", b"def run():\n    return 1\n", "text/x-python"))],
    )
    assert response.status_code == 200, response.text
    return response.json()


def wait_for_terminal(task_id: str, timeout: float = 3) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200, response.text
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
    raise AssertionError("Task did not reach a terminal state")


def test_create_task_idempotency_replays_without_duplicate_rows(
    isolated_queue_database: Path,
) -> None:
    first = create_task(key="create-request-1")
    replay = create_task(key="create-request-1")
    conflict = client.post(
        "/api/tasks",
        headers={"Idempotency-Key": "create-request-1"},
        data={"goal": "另一个任务"},
        files=[("files", ("main.py", b"x = 2\n", "text/x-python"))],
    )

    assert replay["task_id"] == first["task_id"]
    assert conflict.status_code == 409
    with sqlite3.connect(isolated_queue_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM task_idempotency").fetchone()[0] == 1
        assert connection.execute("SELECT SUM(quantity) FROM usage_records").fetchone()[0] == 1


def test_confirmation_key_prevents_duplicate_execution() -> None:
    task = create_task()
    headers = {"Idempotency-Key": "execute-request-1"}

    first = client.post(f"/api/tasks/{task['task_id']}/confirm", headers=headers)
    replay = client.post(f"/api/tasks/{task['task_id']}/confirm", headers=headers)

    assert first.status_code == 200
    assert replay.status_code == 200
    completed = wait_for_terminal(task["task_id"])
    assert completed["status"] == "completed"
    assert completed["queue"]["attempts"] == 1
    assert len(completed["tool_calls"]) == 5


def test_queued_task_can_be_cancelled_without_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_task_service, "start_queue", lambda _settings: None)
    task = create_task()
    confirmed = client.post(f"/api/tasks/{task['task_id']}/confirm")

    assert confirmed.json()["status"] == "queued"
    cancelled = client.post(f"/api/tasks/{task['task_id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["queue"]["status"] == "cancelled"
    assert cancelled["tool_calls"] == []


def test_expired_lease_is_requeued_and_completed_after_restart(
    monkeypatch: pytest.MonkeyPatch,
    isolated_queue_database: Path,
) -> None:
    start_queue = agent_task_service.start_queue
    monkeypatch.setattr(agent_task_service, "start_queue", lambda _settings: None)
    task = create_task()
    client.post(f"/api/tasks/{task['task_id']}/confirm")
    claimed = task_queue_store.claim_next(worker_id="crashed-worker", lease_seconds=0.01)
    assert claimed is not None
    with sqlite3.connect(isolated_queue_database) as connection:
        connection.execute(
            "UPDATE agent_tasks SET status = 'executing' WHERE task_id = ?",
            (task["task_id"],),
        )
        connection.commit()
    time.sleep(0.02)

    recovered = task_queue_store.recover_expired()
    assert recovered[0].action == "retry"
    with database.connect() as connection, pytest.raises(QueueLeaseLostError):
        task_queue_store.require_claim(connection, claimed)
    agent_task_service._apply_queue_recovery(recovered[0])
    assert client.get(f"/api/tasks/{task['task_id']}").json()["status"] == "queued"

    start_queue(settings)
    completed = wait_for_terminal(task["task_id"])
    assert completed["status"] == "completed"
    assert completed["queue"]["attempts"] == 2


def test_worker_heartbeat_extends_the_active_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_task_service, "start_queue", lambda _settings: None)
    task = create_task()
    client.post(f"/api/tasks/{task['task_id']}/confirm")
    claimed = task_queue_store.claim_next(worker_id="live-worker", lease_seconds=0.02)
    assert claimed is not None
    time.sleep(0.01)

    assert task_queue_store.heartbeat(claimed, lease_seconds=0.2) is True
    time.sleep(0.02)
    assert task_queue_store.recover_expired() == []
    current = task_queue_store.get_for_task(task["task_id"])
    assert current is not None
    assert current.status == "running"
    assert current.heartbeat_at is not None


def test_worker_failure_retries_only_to_configured_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_tool(**_kwargs) -> None:
        raise AnalysisInputError("模拟队列工具失败。")

    monkeypatch.setattr(
        "app.services.agent_tasks.execute_registered_tool",
        fail_tool,
    )
    monkeypatch.setattr(settings, "task_queue_max_attempts", 2)
    monkeypatch.setattr(settings, "task_queue_retry_base_seconds", 0)
    task = create_task()
    client.post(f"/api/tasks/{task['task_id']}/confirm")

    failed = wait_for_terminal(task["task_id"])
    assert failed["status"] == "failed"
    assert failed["queue"]["status"] in {"running", "failed"}
    assert failed["queue"]["attempts"] == 2
    assert len(failed["tool_calls"]) == 2
