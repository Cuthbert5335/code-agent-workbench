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
from app.services.patches import patch_service
from app.services.security import security_service

client = TestClient(app)
PASSWORD = "Correct-Horse-42!"


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "workflows.db"
    security_service.use_database_for_test(str(path))
    agent_task_service.clear()
    patch_service.clear()
    yield path
    agent_task_service.clear()
    patch_service.clear()


def register_and_login(email: str) -> tuple[dict, str]:
    registered = client.post(
        "/api/auth/register",
        json={"email": email, "display_name": email.split("@")[0], "password": PASSWORD},
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


def create_project(token: str, name: str = "Workflow Project") -> dict:
    response = client.post(
        "/api/projects",
        headers=headers(token),
        json={"name": name, "description": "durable workflows", "run_mode": "local"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_project_task(token: str, project_id: str) -> dict:
    response = client.post(
        "/api/tasks",
        headers=headers(token),
        data={"goal": "修复 run 返回值", "project_id": project_id},
        files=[
            (
                "files",
                ("src/example.py", b"def run():\n    return 1\n", "text/x-python"),
            ),
        ],
    )
    assert response.status_code == 200, response.text
    return response.json()


def wait_for_status(task_id: str, token: str, status: str = "completed") -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}", headers=headers(token))
        assert response.status_code == 200, response.text
        if response.json()["status"] == status:
            return response.json()
        time.sleep(0.01)
    raise AssertionError(f"Task did not reach {status}")


def test_project_task_requires_authentication_and_backend_permissions() -> None:
    owner, owner_token = register_and_login("owner@example.com")
    viewer, viewer_token = register_and_login("viewer@example.com")
    _outsider, outsider_token = register_and_login("outsider@example.com")
    project = create_project(owner_token)
    added = client.post(
        f"/api/projects/{project['project_id']}/members",
        headers=headers(owner_token),
        json={"email": viewer["email"], "role": "viewer"},
    )
    assert added.status_code == 201

    unauthenticated_create = client.post(
        "/api/tasks",
        data={"goal": "denied", "project_id": project["project_id"]},
        files=[("files", ("safe.py", b"x = 1\n", "text/x-python"))],
    )
    assert unauthenticated_create.status_code == 401

    task = create_project_task(owner_token, project["project_id"])
    assert task["project_id"] == project["project_id"]
    assert client.get(f"/api/tasks/{task['task_id']}").status_code == 401
    assert client.get(
        f"/api/tasks/{task['task_id']}",
        headers=headers(outsider_token),
    ).status_code == 403
    assert client.get(
        f"/api/tasks/{task['task_id']}",
        headers=headers(viewer_token),
    ).status_code == 200
    assert client.post(
        f"/api/tasks/{task['task_id']}/confirm",
        headers=headers(viewer_token),
    ).status_code == 403

    promoted = client.patch(
        f"/api/projects/{project['project_id']}/members/{viewer['user_id']}",
        headers=headers(owner_token),
        json={"role": "editor"},
    )
    assert promoted.status_code == 200
    assert client.post(
        f"/api/tasks/{task['task_id']}/confirm",
        headers=headers(viewer_token),
    ).status_code == 200
    wait_for_status(task["task_id"], viewer_token)

    visible = client.get("/api/tasks", headers=headers(viewer_token)).json()
    assert visible["total"] == 1
    assert visible["tasks"][0]["task_id"] == task["task_id"]
    assert client.get("/api/tasks", headers=headers(outsider_token)).json()["total"] == 0
    assert owner["user_id"] != viewer["user_id"]


def test_legacy_anonymous_workflows_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "allow_legacy_local_workflows", False)
    response = client.post(
        "/api/tasks",
        data={"goal": "anonymous denied"},
        files=[("files", ("safe.py", b"x = 1\n", "text/x-python"))],
    )
    assert response.status_code == 401
    assert client.get("/api/tasks").status_code == 401


def test_task_patch_validation_and_workspace_survive_database_reconnect(
    isolated_database: Path,
) -> None:
    _owner, token = register_and_login("persistent@example.com")
    project = create_project(token)
    task = create_project_task(token, project["project_id"])
    confirmed = client.post(
        f"/api/tasks/{task['task_id']}/confirm",
        headers=headers(token),
    )
    assert confirmed.status_code == 200
    wait_for_status(task["task_id"], token)

    created = client.post(
        f"/api/tasks/{task['task_id']}/patches",
        headers=headers(token),
        json={
            "summary": "更新返回值",
            "risk": "仅更新持久化任务快照。",
            "changes": [
                {
                    "file": "src/example.py",
                    "updated_content": "def run():\n    return 2\n",
                    "reason": "修正返回值。",
                },
            ],
            "suggested_validators": ["python_syntax"],
        },
    )
    assert created.status_code == 200, created.text
    patch = created.json()
    viewer, viewer_token = register_and_login("patch-viewer@example.com")
    member = client.post(
        f"/api/projects/{project['project_id']}/members",
        headers=headers(token),
        json={"email": viewer["email"], "role": "viewer"},
    )
    assert member.status_code == 201
    reviewed = client.post(
        f"/api/patches/{patch['patch_id']}/review",
        headers=headers(token),
        json={"file": "src/example.py", "decision": "accepted"},
    )
    assert reviewed.json()["status"] == "approved"
    validated = client.post(
        f"/api/patches/{patch['patch_id']}/validate",
        headers=headers(token),
        json={"validators": ["python_syntax"]},
    )
    assert validated.json()["validations"][0]["status"] == "passed"
    denied_apply = client.post(
        f"/api/patches/{patch['patch_id']}/apply",
        headers=headers(viewer_token),
        json={"confirm": True},
    )
    assert denied_apply.status_code == 403
    applied = client.post(
        f"/api/patches/{patch['patch_id']}/apply",
        headers=headers(token),
        json={"confirm": True},
    )
    assert applied.json()["status"] == "applied"

    security_service.use_database_for_test(str(isolated_database))

    restored_task = client.get(
        f"/api/tasks/{task['task_id']}",
        headers=headers(token),
    )
    restored_patch = client.get(
        f"/api/patches/{patch['patch_id']}",
        headers=headers(token),
    )
    download = client.get(
        f"/api/patches/{patch['patch_id']}/files/src/example.py/download",
        headers=headers(token),
    )
    assert restored_task.status_code == 200
    assert restored_task.json()["status"] == "completed"
    assert len(restored_task.json()["tool_calls"]) == 5
    assert restored_patch.status_code == 200
    assert restored_patch.json()["status"] == "applied"
    assert restored_patch.json()["validations"][0]["status"] == "passed"
    assert download.content == b"def run():\n    return 2\n"

    audit = client.get(
        f"/api/projects/{project['project_id']}/audit",
        headers=headers(token),
    ).json()
    actions = {item["action"] for item in audit["events"]}
    assert {"task.created", "task.confirmed", "patch.created"} <= actions
    assert {"patch.file_reviewed", "patch.validated", "patch.apply_requested"} <= actions

    with sqlite3.connect(isolated_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM patches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM validation_runs").fetchone()[0] == 1


def test_confirmed_task_runs_through_the_durable_queue(
    isolated_database: Path,
) -> None:
    _owner, token = register_and_login("restart@example.com")
    project = create_project(token)
    task = create_project_task(token, project["project_id"])
    confirmed = client.post(
        f"/api/tasks/{task['task_id']}/confirm",
        headers=headers(token),
    )
    assert confirmed.status_code == 200
    restored = wait_for_status(task["task_id"], token)
    assert restored["status"] == "completed"
    assert restored["queue"]["status"] in {"running", "completed"}


def test_project_deletion_cascades_durable_workflow_rows(
    isolated_database: Path,
) -> None:
    _owner, token = register_and_login("delete-workflow@example.com")
    project = create_project(token, "Delete Workflow")
    task = create_project_task(token, project["project_id"])
    cancelled = client.post(
        f"/api/tasks/{task['task_id']}/cancel",
        headers=headers(token),
    )
    assert cancelled.status_code == 200
    deleted = client.request(
        "DELETE",
        f"/api/projects/{project['project_id']}",
        headers=headers(token),
        json={"confirm": True, "project_name": "Delete Workflow"},
    )
    assert deleted.status_code == 200

    with sqlite3.connect(isolated_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM task_files").fetchone()[0] == 0
    assert client.get(
        f"/api/tasks/{task['task_id']}",
        headers=headers(token),
    ).status_code == 404
