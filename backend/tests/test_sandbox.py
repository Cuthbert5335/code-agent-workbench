from __future__ import annotations

import io
import tarfile

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.sandbox import SANDBOX_COMMANDS, SandboxError, sandbox_service

client = TestClient(app)


def test_sandbox_status_is_fail_closed_and_lists_only_allowed_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sandbox_service,
        "_runtime_status",
        lambda _settings: (False, "runtime unavailable"),
    )

    response = client.get("/api/sandbox/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["network"] == "disabled"
    assert payload["root_filesystem"] == "read_only"
    assert payload["workspace"] == "temporary"
    assert payload["allowed_commands"] == list(SANDBOX_COMMANDS)


def test_docker_command_enforces_resource_and_isolation_flags() -> None:
    command = SANDBOX_COMMANDS["sandbox_pytest"]
    argv = sandbox_service._docker_command(command, settings, "codexxx-sandbox-test")

    assert ["--network", "none"] == argv[argv.index("--network") : argv.index("--network") + 2]
    assert "--read-only" in argv
    assert ["--cap-drop", "ALL"] == argv[
        argv.index("--cap-drop") : argv.index("--cap-drop") + 2
    ]
    assert "no-new-privileges:true" in argv
    assert "--cpus" in argv
    assert "--memory" in argv
    assert "--pids-limit" in argv
    assert "--tmpfs" in argv
    assert "--pull" in argv and "never" in argv
    assert argv[-len(command.argv) :] == list(command.argv)


def test_workspace_archive_is_bounded_and_rejects_path_traversal() -> None:
    archive = sandbox_service._archive(
        {"src/main.py": "print('safe')\n", "README.md": "# Safe\n"},
        settings,
    )
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r") as tar:
        assert tar.getnames() == ["src", "src/main.py", "README.md"]
        assert all(member.uid == 65534 for member in tar.getmembers())

    with pytest.raises(SandboxError, match="路径不安全"):
        sandbox_service._archive({"../escape.py": "x = 1\n"}, settings)


def test_unknown_command_never_reaches_the_container_runtime() -> None:
    with pytest.raises(SandboxError, match="允许列表"):
        sandbox_service.run("shell", files={"a.py": "x = 1\n"}, settings=settings)
