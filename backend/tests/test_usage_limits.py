from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.main import app
from app.services.agent_tasks import agent_task_service
from app.services.patches import patch_service
from app.services.security import security_service

client = TestClient(app)
PASSWORD = "Correct-Horse-42!"


@pytest.fixture(autouse=True)
def isolated_usage_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    path = tmp_path / "usage.db"
    security_service.use_database_for_test(str(path))
    agent_task_service.clear()
    patch_service.clear()
    for name, value in {
        "usage_window_seconds": 60,
        "max_active_tasks_per_user": 10,
        "max_active_tasks_per_project": 10,
        "max_model_calls_per_user_window": 100,
        "max_model_calls_per_project_window": 100,
        "max_files_per_user_window": 100,
        "max_files_per_project_window": 100,
        "max_patches_per_user_window": 100,
        "max_patches_per_project_window": 100,
        "max_validations_per_user_window": 100,
        "max_validations_per_project_window": 100,
    }.items():
        monkeypatch.setattr(settings, name, value)
    yield path
    agent_task_service.clear()
    patch_service.clear()


def register_and_login(email: str) -> tuple[dict, str]:
    registered = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "display_name": email.split("@")[0],
            "password": PASSWORD,
        },
    )
    assert registered.status_code == 201, registered.text
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    return registered.json(), login.json()["access_token"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_project(token: str, name: str) -> dict:
    response = client.post(
        "/api/projects",
        headers=headers(token),
        json={"name": name, "description": "usage limits", "run_mode": "local"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_project_task(token: str, project_id: str, goal: str = "检查 run"):
    return client.post(
        "/api/tasks",
        headers=headers(token),
        data={"goal": goal, "project_id": project_id},
        files=[
            (
                "files",
                ("src/example.py", b"def run():\n    return 1\n", "text/x-python"),
            ),
        ],
    )


def wait_for_completed(task_id: str, token: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}", headers=headers(token))
        assert response.status_code == 200, response.text
        if response.json()["status"] == "completed":
            return response.json()
        time.sleep(0.01)
    raise AssertionError("Task did not complete")


def complete_project_task(token: str, project_id: str) -> dict:
    created = create_project_task(token, project_id)
    assert created.status_code == 200, created.text
    task = created.json()
    confirmed = client.post(
        f"/api/tasks/{task['task_id']}/confirm",
        headers=headers(token),
    )
    assert confirmed.status_code == 200, confirmed.text
    return wait_for_completed(task["task_id"], token)


def create_manual_patch(token: str, task_id: str, value: int = 2):
    return client.post(
        f"/api/tasks/{task_id}/patches",
        headers=headers(token),
        json={
            "summary": f"返回 {value}",
            "risk": "仅修改持久化任务快照。",
            "changes": [
                {
                    "file": "src/example.py",
                    "updated_content": f"def run():\n    return {value}\n",
                    "reason": "更新返回值。",
                },
            ],
            "suggested_validators": ["python_syntax"],
        },
    )


def assert_rate_limit(response, *, resource: str, scope: str) -> dict:
    assert response.status_code == 429, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "usage_limit_exceeded"
    assert detail["resource"] == resource
    assert detail["scope"] == scope
    assert detail["retry_after_seconds"] >= 1
    assert detail["retry_at"].endswith("+00:00")
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["X-RateLimit-Remaining"] == "0"
    return detail


def metric(summary: dict, resource: str) -> dict:
    return next(item for item in summary["metrics"] if item["resource"] == resource)


def test_usage_summary_requires_auth_and_reports_schema_v5(
    isolated_usage_database: Path,
) -> None:
    assert client.get("/api/usage").status_code == 401
    _user, token = register_and_login("summary@example.com")
    project = create_project(token, "Summary")
    created = create_project_task(token, project["project_id"])
    assert created.status_code == 200, created.text

    response = client.get(
        "/api/usage",
        params={"project_id": project["project_id"]},
        headers=headers(token),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user"]["active_tasks"] == 1
    assert payload["project"]["active_tasks"] == 1
    assert metric(payload["user"], "files")["used"] == 1
    assert metric(payload["project"], "files")["used"] == 1

    with sqlite3.connect(isolated_usage_database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0] == 1


def test_user_active_task_limit_releases_on_cancel_and_applies_to_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_active_tasks_per_user", 1)
    _user, token = register_and_login("active-user@example.com")
    first_project = create_project(token, "First")
    second_project = create_project(token, "Second")
    first = create_project_task(token, first_project["project_id"])
    assert first.status_code == 200

    denied = create_project_task(token, second_project["project_id"])
    detail = assert_rate_limit(denied, resource="active_tasks", scope="user")
    assert detail["used"] == detail["limit"] == 1

    cancelled = client.post(
        f"/api/tasks/{first.json()['task_id']}/cancel",
        headers=headers(token),
    )
    assert cancelled.status_code == 200
    second = create_project_task(token, second_project["project_id"])
    assert second.status_code == 200

    denied_resume = client.post(
        f"/api/tasks/{first.json()['task_id']}/resume",
        headers=headers(token),
    )
    assert_rate_limit(denied_resume, resource="active_tasks", scope="user")
    assert client.post(
        f"/api/tasks/{second.json()['task_id']}/cancel",
        headers=headers(token),
    ).status_code == 200
    assert client.post(
        f"/api/tasks/{first.json()['task_id']}/resume",
        headers=headers(token),
    ).status_code == 200


def test_project_active_task_limit_combines_different_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_active_tasks_per_project", 1)
    owner, owner_token = register_and_login("project-owner@example.com")
    editor, editor_token = register_and_login("project-editor@example.com")
    project = create_project(owner_token, "Shared")
    added = client.post(
        f"/api/projects/{project['project_id']}/members",
        headers=headers(owner_token),
        json={"email": editor["email"], "role": "editor"},
    )
    assert added.status_code == 201
    assert owner["user_id"] != editor["user_id"]
    assert create_project_task(owner_token, project["project_id"]).status_code == 200

    denied = create_project_task(editor_token, project["project_id"])
    assert_rate_limit(denied, resource="active_tasks", scope="project")


def test_project_file_quota_is_isolated_and_survives_reconnect(
    isolated_usage_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_files_per_project_window", 1)
    _user, token = register_and_login("files@example.com")
    first_project = create_project(token, "Files One")
    second_project = create_project(token, "Files Two")
    assert create_project_task(token, first_project["project_id"]).status_code == 200

    denied = create_project_task(token, first_project["project_id"], "再次检查")
    assert_rate_limit(denied, resource="files", scope="project")
    assert create_project_task(token, second_project["project_id"]).status_code == 200

    security_service.use_database_for_test(str(isolated_usage_database))
    response = client.get(
        "/api/usage",
        params={"project_id": first_project["project_id"]},
        headers=headers(token),
    )
    assert response.status_code == 200, response.text
    files = metric(response.json()["project"], "files")
    assert files["used"] == files["limit"] == 1
    assert files["remaining"] == 0
    assert files["next_reset_at"] is not None


def test_quota_rejection_does_not_evict_terminal_task_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_files_per_project_window", 1)
    monkeypatch.setattr("app.services.agent_tasks.MAX_STORED_TASKS", 1)
    _user, token = register_and_login("history@example.com")
    project = create_project(token, "History")
    first = create_project_task(token, project["project_id"])
    assert first.status_code == 200
    task_id = first.json()["task_id"]
    assert client.post(
        f"/api/tasks/{task_id}/cancel",
        headers=headers(token),
    ).status_code == 200

    denied = create_project_task(token, project["project_id"], "第二个任务")
    assert_rate_limit(denied, resource="files", scope="project")
    restored = client.get(f"/api/tasks/{task_id}", headers=headers(token))
    assert restored.status_code == 200
    assert restored.json()["status"] == "cancelled"


def test_patch_and_validation_quotas_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_patches_per_user_window", 1)
    monkeypatch.setattr(settings, "max_patches_per_project_window", 1)
    monkeypatch.setattr(settings, "max_validations_per_user_window", 1)
    monkeypatch.setattr(settings, "max_validations_per_project_window", 1)
    _user, token = register_and_login("patch-usage@example.com")
    project = create_project(token, "Patch Usage")
    task = complete_project_task(token, project["project_id"])

    first_patch = create_manual_patch(token, task["task_id"])
    assert first_patch.status_code == 200, first_patch.text
    denied_patch = create_manual_patch(token, task["task_id"], value=3)
    assert_rate_limit(denied_patch, resource="patches", scope="user")

    patch_id = first_patch.json()["patch_id"]
    validated = client.post(
        f"/api/patches/{patch_id}/validate",
        headers=headers(token),
        json={"validators": ["python_syntax"]},
    )
    assert validated.status_code == 200, validated.text
    denied_validation = client.post(
        f"/api/patches/{patch_id}/validate",
        headers=headers(token),
        json={"validators": ["python_syntax"]},
    )
    assert_rate_limit(denied_validation, resource="validations", scope="user")


def test_real_analysis_model_call_limit_counts_provider_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = Settings(
        model_api_key="test-key",
        model_base_url="https://example.test/v1",
        model_name="test-model",
        usage_window_seconds=60,
        max_model_calls_per_user_window=1,
        max_files_per_user_window=10,
    )
    monkeypatch.setattr("app.api.analysis.settings", configured)
    call_count = 0

    async def fake_request_chat_completion(*, context: str, settings: Settings) -> str:
        nonlocal call_count
        call_count += 1
        assert "文件：example.py" in context
        assert settings.model_name == "test-model"
        return "结论：run 返回 1。[引用: example.py: 1-2]"

    monkeypatch.setattr(
        "app.services.analysis.request_chat_completion",
        fake_request_chat_completion,
    )

    def post_analysis():
        return client.post(
            "/api/analyze",
            data={"question": "分析 run"},
            files=[
                (
                    "files",
                    ("example.py", b"def run():\n    return 1\n", "text/x-python"),
                ),
            ],
        )

    assert post_analysis().status_code == 200
    denied = post_analysis()
    assert_rate_limit(denied, resource="model_calls", scope="legacy_local")
    assert call_count == 1
