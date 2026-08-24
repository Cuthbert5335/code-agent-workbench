from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.security import security_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_security_database(tmp_path: Path) -> Iterator[Path]:
    database_path = tmp_path / "security.db"
    security_service.use_database_for_test(str(database_path))
    yield database_path


def register(
    email: str,
    password: str = "Correct-Horse-42!",
    display_name: str = "Test User",
) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "display_name": display_name, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(email: str, password: str = "Correct-Horse-42!") -> dict:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_project(token: str, name: str = "Persistent Project") -> dict:
    response = client.post(
        "/api/projects",
        headers=auth_headers(token),
        json={
            "name": name,
            "description": "Project metadata only",
            "run_mode": "local",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_registration_and_login_store_only_password_hash_and_token_digest(
    isolated_security_database: Path,
) -> None:
    user = register("User@Example.com")
    auth = login("user@example.com")

    assert user["email"] == "user@example.com"
    assert auth["token_type"] == "bearer"
    assert auth["access_token"]
    assert client.get(
        "/api/auth/me",
        headers=auth_headers(auth["access_token"]),
    ).json()["user_id"] == user["user_id"]

    with sqlite3.connect(isolated_security_database) as connection:
        password_hash = connection.execute("SELECT password_hash FROM users").fetchone()[0]
        stored_token = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
        raw_database = " ".join(
            str(value)
            for row in connection.execute("SELECT * FROM users").fetchall()
            for value in row
        )

    assert password_hash.startswith("pbkdf2_sha256$600000$")
    assert "Correct-Horse-42!" not in raw_database
    assert stored_token != auth["access_token"]
    assert auth["access_token"] not in stored_token


def test_registration_rejects_invalid_duplicate_and_extra_fields() -> None:
    invalid_email = client.post(
        "/api/auth/register",
        json={
            "email": "not-an-email",
            "display_name": "Invalid",
            "password": "Correct-Horse-42!",
        },
    )
    assert invalid_email.status_code == 400

    weak_password = client.post(
        "/api/auth/register",
        json={
            "email": "weak@example.com",
            "display_name": "Weak",
            "password": "short",
        },
    )
    assert weak_password.status_code == 422

    eight_char_password = client.post(
        "/api/auth/register",
        json={
            "email": "eight@example.com",
            "display_name": "Eight",
            "password": "abc12345",
        },
    )
    assert eight_char_password.status_code == 201

    register("duplicate@example.com")
    duplicate = client.post(
        "/api/auth/register",
        json={
            "email": "duplicate@example.com",
            "display_name": "Duplicate",
            "password": "Another-Secure-42!",
        },
    )
    assert duplicate.status_code == 409

    extra = client.post(
        "/api/auth/login",
        json={
            "email": "duplicate@example.com",
            "password": "Correct-Horse-42!",
            "remember_forever": True,
        },
    )
    assert extra.status_code == 422


def test_login_is_rate_limited_and_uses_generic_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register("limited@example.com")
    monkeypatch.setattr(security_service.settings, "login_max_failures", 2)

    for _ in range(2):
        response = client.post(
            "/api/auth/login",
            json={"email": "limited@example.com", "password": "wrong"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "邮箱或密码不正确。"

    limited = client.post(
        "/api/auth/login",
        json={"email": "limited@example.com", "password": "Correct-Horse-42!"},
    )
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 10

    unknown = client.post(
        "/api/auth/login",
        json={"email": "unknown@example.com", "password": "wrong"},
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == "邮箱或密码不正确。"


def test_logout_expiry_and_password_change_revoke_sessions(
    isolated_security_database: Path,
) -> None:
    register("session@example.com")
    first = login("session@example.com")
    second = login("session@example.com")

    logout = client.post(
        "/api/auth/logout",
        headers=auth_headers(first["access_token"]),
    )
    assert logout.status_code == 200
    assert client.get(
        "/api/auth/me",
        headers=auth_headers(first["access_token"]),
    ).status_code == 401

    changed = client.post(
        "/api/auth/change-password",
        headers=auth_headers(second["access_token"]),
        json={
            "current_password": "Correct-Horse-42!",
            "new_password": "Replacement-Password-84!",
        },
    )
    assert changed.status_code == 200
    assert client.get(
        "/api/auth/me",
        headers=auth_headers(second["access_token"]),
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "session@example.com", "password": "Correct-Horse-42!"},
    ).status_code == 401

    replacement = login("session@example.com", "Replacement-Password-84!")
    expired_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(isolated_security_database) as connection:
        connection.execute("UPDATE sessions SET expires_at = ?", (expired_at,))
        connection.commit()
    assert client.get(
        "/api/auth/me",
        headers=auth_headers(replacement["access_token"]),
    ).status_code == 401


def test_users_only_list_projects_they_can_access() -> None:
    owner = register("owner@example.com", display_name="Owner")
    viewer = register("viewer@example.com", display_name="Viewer")
    outsider = register("outsider@example.com", display_name="Outsider")
    owner_auth = login(owner["email"])
    viewer_auth = login(viewer["email"])
    outsider_auth = login(outsider["email"])
    project = create_project(owner_auth["access_token"])

    added = client.post(
        f"/api/projects/{project['project_id']}/members",
        headers=auth_headers(owner_auth["access_token"]),
        json={"email": viewer["email"], "role": "viewer"},
    )
    assert added.status_code == 201

    viewer_projects = client.get(
        "/api/projects",
        headers=auth_headers(viewer_auth["access_token"]),
    ).json()
    assert viewer_projects["total"] == 1
    assert viewer_projects["projects"][0]["role"] == "viewer"
    assert viewer_projects["projects"][0]["permissions"] == ["read"]

    denied = client.get(
        f"/api/projects/{project['project_id']}",
        headers=auth_headers(outsider_auth["access_token"]),
    )
    assert denied.status_code == 403
    assert client.get(
        "/api/projects",
        headers=auth_headers(outsider_auth["access_token"]),
    ).json()["total"] == 0


def test_project_permissions_are_enforced_by_backend() -> None:
    owner = register("owner@example.com")
    member = register("member@example.com")
    owner_token = login(owner["email"])["access_token"]
    member_token = login(member["email"])["access_token"]
    project = create_project(owner_token)
    project_url = f"/api/projects/{project['project_id']}"
    client.post(
        f"{project_url}/members",
        headers=auth_headers(owner_token),
        json={"email": member["email"], "role": "viewer"},
    )

    viewer_update = client.patch(
        project_url,
        headers=auth_headers(member_token),
        json={"name": "Forbidden Rename"},
    )
    assert viewer_update.status_code == 403
    assert client.get(
        f"{project_url}/audit",
        headers=auth_headers(member_token),
    ).status_code == 403

    promoted = client.patch(
        f"{project_url}/members/{member['user_id']}",
        headers=auth_headers(owner_token),
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    renamed = client.patch(
        project_url,
        headers=auth_headers(member_token),
        json={"name": "Admin Rename", "archived": True},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Admin Rename"
    assert renamed.json()["archived_at"] is not None

    members = client.get(
        f"{project_url}/members",
        headers=auth_headers(member_token),
    ).json()
    assert {item["role"] for item in members["members"]} == {"owner", "admin"}


def test_project_delete_requires_explicit_matching_confirmation() -> None:
    register("delete@example.com")
    token = login("delete@example.com")["access_token"]
    project = create_project(token, "精确删除项目")
    project_url = f"/api/projects/{project['project_id']}"

    assert client.request(
        "DELETE",
        project_url,
        headers=auth_headers(token),
        json={"confirm": True, "project_name": "Wrong Name"},
    ).status_code == 400
    assert client.get(project_url, headers=auth_headers(token)).status_code == 200

    deleted = client.request(
        "DELETE",
        project_url,
        headers=auth_headers(token),
        json={"confirm": True, "project_name": "精确删除项目"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_project_id"] == project["project_id"]
    assert client.get(project_url, headers=auth_headers(token)).status_code == 404

    audit = client.get("/api/audit", headers=auth_headers(token)).json()
    assert any(event["action"] == "project.deleted" for event in audit["events"])


def test_project_delete_rejects_active_tasks(
    isolated_security_database: Path,
) -> None:
    register("active-delete@example.com")
    token = login("active-delete@example.com")["access_token"]
    project = create_project(token, "Active Project")
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(isolated_security_database) as connection:
        connection.execute(
            """
            INSERT INTO agent_tasks (
                task_id, project_id, owner_user_id, goal, mode, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'active', 'plan', 'waiting_for_confirmation', ?, ?)
            """,
            ("active-task", project["project_id"], project["owner_user_id"], now, now),
        )
        connection.commit()

    response = client.request(
        "DELETE",
        f"/api/projects/{project['project_id']}",
        headers=auth_headers(token),
        json={"confirm": True, "project_name": "Active Project"},
    )

    assert response.status_code == 409
    assert "活动任务" in response.json()["detail"]


def test_account_delete_requires_credentials_and_cascades_owned_data(
    isolated_security_database: Path,
) -> None:
    user = register("account-delete@example.com")
    token = login(user["email"])["access_token"]
    project = create_project(token, "Owned Data")
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(isolated_security_database) as connection:
        connection.execute(
            """
            INSERT INTO agent_tasks (
                task_id, project_id, owner_user_id, goal, mode, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'done', 'plan', 'completed', ?, ?)
            """,
            ("deleted-task", project["project_id"], user["user_id"], now, now),
        )
        connection.execute(
            """
            INSERT INTO usage_records (
                usage_id, user_id, project_id, resource, quantity, occurred_at
            ) VALUES ('deleted-usage', ?, ?, 'files', 1, ?)
            """,
            (user["user_id"], project["project_id"], now),
        )
        connection.commit()

    wrong_email = client.request(
        "DELETE",
        "/api/auth/account",
        headers=auth_headers(token),
        json={
            "confirm": True,
            "email": "other@example.com",
            "current_password": "Correct-Horse-42!",
        },
    )
    assert wrong_email.status_code == 400
    wrong_password = client.request(
        "DELETE",
        "/api/auth/account",
        headers=auth_headers(token),
        json={
            "confirm": True,
            "email": user["email"],
            "current_password": "wrong",
        },
    )
    assert wrong_password.status_code == 401

    deleted = client.request(
        "DELETE",
        "/api/auth/account",
        headers=auth_headers(token),
        json={
            "confirm": True,
            "email": user["email"],
            "current_password": "Correct-Horse-42!",
        },
    )

    assert deleted.status_code == 200
    assert deleted.json()["deleted_user_id"] == user["user_id"]
    assert client.get("/api/auth/me", headers=auth_headers(token)).status_code == 401
    with sqlite3.connect(isolated_security_database) as connection:
        for table in ("users", "sessions", "projects", "agent_tasks", "usage_records"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        audit = connection.execute(
            """
            SELECT actor_user_id, action, resource_id FROM audit_logs
            WHERE action = 'account.deleted'
            """,
        ).fetchone()
    assert audit == (None, "account.deleted", user["user_id"])


def test_account_project_session_and_audit_survive_service_reconfiguration(
    isolated_security_database: Path,
) -> None:
    register("persistent@example.com")
    auth = login("persistent@example.com")
    project = create_project(auth["access_token"])

    security_service.use_database_for_test(str(isolated_security_database))

    me = client.get(
        "/api/auth/me",
        headers=auth_headers(auth["access_token"]),
    )
    projects = client.get(
        "/api/projects",
        headers=auth_headers(auth["access_token"]),
    )
    audit = client.get(
        f"/api/projects/{project['project_id']}/audit",
        headers=auth_headers(auth["access_token"]),
    )
    assert me.status_code == 200
    assert projects.json()["projects"][0]["project_id"] == project["project_id"]
    assert audit.status_code == 200
    assert audit.json()["total"] >= 1


def test_project_routes_require_authentication() -> None:
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/audit").status_code == 401
