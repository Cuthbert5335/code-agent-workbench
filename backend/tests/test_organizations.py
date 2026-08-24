from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.security import security_service

client = TestClient(app)
PASSWORD = "Correct-Horse-42!"


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "organizations.db"
    security_service.use_database_for_test(str(path))
    yield path


def account(email: str) -> tuple[dict, str]:
    registered = client.post(
        "/api/auth/register",
        json={"email": email, "display_name": email.split("@")[0], "password": PASSWORD},
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert logged_in.status_code == 200, logged_in.text
    return registered.json(), logged_in.json()["access_token"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_registration_creates_default_organization_and_schema_v5(
    isolated_database: Path,
) -> None:
    user, token = account("owner@example.com")
    organizations = client.get("/api/organizations", headers=headers(token))
    assert organizations.status_code == 200
    assert organizations.json()["total"] == 1
    assert organizations.json()["organizations"][0]["role"] == "owner"
    with sqlite3.connect(isolated_database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM organization_members").fetchone()[0] == 1
        assert connection.execute("SELECT owner_user_id FROM organizations").fetchone()[0] == user["user_id"]


def test_organization_membership_shares_project_read_access_but_not_write() -> None:
    _owner, owner_token = account("owner@example.com")
    _member, member_token = account("member@example.com")
    organization = client.post(
        "/api/organizations",
        headers=headers(owner_token),
        json={"name": "Shared Team", "description": "collaboration"},
    ).json()
    organization_id = organization["organization_id"]
    added = client.post(
        f"/api/organizations/{organization_id}/members",
        headers=headers(owner_token),
        json={"email": "member@example.com", "role": "member"},
    )
    assert added.status_code == 201, added.text
    project = client.post(
        "/api/projects",
        headers=headers(owner_token),
        json={"name": "Shared Project", "organization_id": organization_id},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]
    visible = client.get("/api/projects", headers=headers(member_token)).json()
    assert [item["project_id"] for item in visible["projects"]] == [project_id]
    assert client.get(f"/api/projects/{project_id}", headers=headers(member_token)).status_code == 200
    denied = client.post(
        "/api/tasks",
        headers=headers(member_token),
        data={"goal": "write", "project_id": project_id},
        files=[("files", ("main.py", b"print(1)\n", "text/x-python"))],
    )
    assert denied.status_code == 403


def test_project_member_invite_adds_organization_membership_and_removal_revokes_access() -> None:
    _owner, owner_token = account("owner@example.com")
    member, member_token = account("member@example.com")
    organization = client.post(
        "/api/organizations", headers=headers(owner_token), json={"name": "Team"}
    ).json()
    organization_id = organization["organization_id"]
    project = client.post(
        "/api/projects",
        headers=headers(owner_token),
        json={"name": "Project", "organization_id": organization_id},
    ).json()
    project_id = project["project_id"]
    invited = client.post(
        f"/api/projects/{project_id}/members",
        headers=headers(owner_token),
        json={"email": "member@example.com", "role": "editor"},
    )
    assert invited.status_code == 201, invited.text
    assert client.get(f"/api/projects/{project_id}", headers=headers(member_token)).status_code == 200
    removed = client.delete(
        f"/api/organizations/{organization_id}/members/{member['user_id']}",
        headers=headers(owner_token),
    )
    assert removed.status_code == 204, removed.text
    assert client.get(f"/api/projects/{project_id}", headers=headers(member_token)).status_code == 403


def test_organization_isolation_denies_outsider_and_member_management() -> None:
    _owner, owner_token = account("owner@example.com")
    _outsider, outsider_token = account("outsider@example.com")
    organization = client.post(
        "/api/organizations", headers=headers(owner_token), json={"name": "Private"}
    ).json()
    organization_id = organization["organization_id"]
    assert client.get(
        f"/api/organizations/{organization_id}", headers=headers(outsider_token)
    ).status_code == 403
    assert client.get(
        f"/api/organizations/{organization_id}/members", headers=headers(outsider_token)
    ).status_code == 403
    assert client.post(
        f"/api/organizations/{organization_id}/members",
        headers=headers(outsider_token),
        json={"email": "owner@example.com", "role": "member"},
    ).status_code == 403
