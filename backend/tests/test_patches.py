from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.patches import ValidatorSpec
from app.services import patches as patches_module
from app.services.agent_tasks import agent_task_service
from app.services.patches import (
    PATCH_SYSTEM_PROMPT,
    ValidatorDefinition,
    build_patch_context,
    parse_patch_payload,
    patch_service,
)
from app.services.sandbox import sandbox_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_state(tmp_path: Path) -> Iterator[None]:
    agent_task_service.use_database_for_test(str(tmp_path / "patches.db"))
    agent_task_service.clear()
    patch_service.clear()
    yield
    agent_task_service.clear()
    patch_service.clear()


def completed_task() -> dict:
    create_response = client.post(
        "/api/tasks",
        data={"goal": "修复 run 返回值并更新配置"},
        files=[
            (
                "files",
                ("src/example.py", b"def run():\n    return 1\n", "text/x-python"),
            ),
            (
                "files",
                ("config.json", b'{"enabled": false}\n', "application/json"),
            ),
        ],
    )
    task = create_response.json()
    confirm_response = client.post(f"/api/tasks/{task['task_id']}/confirm")
    assert confirm_response.status_code == 200
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        payload = client.get(f"/api/tasks/{task['task_id']}").json()
        if payload["status"] == "completed":
            return payload
        time.sleep(0.01)
    raise AssertionError("Task did not complete")


def create_patch(task_id: str) -> dict:
    response = client.post(
        f"/api/tasks/{task_id}/patches",
        json={
            "summary": "更新 run 和配置",
            "risk": "仅修改内存快照，需下载后由用户自行替换。",
            "changes": [
                {
                    "file": "src/example.py",
                    "updated_content": "def run():\n    return 2\n",
                    "reason": "修正返回值。",
                },
                {
                    "file": "config.json",
                    "updated_content": '{"enabled": true}\n',
                    "reason": "启用功能。",
                },
            ],
            "suggested_validators": [
                "patch_integrity",
                "conflict_check",
                "whitespace",
                "json_syntax",
                "python_syntax",
            ],
        },
    )
    assert response.status_code == 200
    return response.json()


def review_all(patch: dict) -> dict:
    current = patch
    for patch_file in patch["files"]:
        response = client.post(
            f"/api/patches/{patch['patch_id']}/review",
            json={"file": patch_file["file"], "decision": "accepted"},
        )
        assert response.status_code == 200
        current = response.json()
    return current


def test_create_patch_builds_reviewable_diff_without_full_contents() -> None:
    task = completed_task()
    patch = create_patch(task["task_id"])

    assert patch["status"] == "draft"
    assert patch["pending_files"] == 2
    assert patch["can_apply"] is False
    assert patch["files"][0]["unified_diff"].startswith("--- a/src/example.py")
    assert patch["files"][0]["additions"] == 1
    assert patch["files"][0]["deletions"] == 1
    assert "updated_content" not in patch["files"][0]
    assert len(patch["files"][0]["base_version"]) == 64


def test_patch_history_keeps_original_before_regenerated_patch() -> None:
    task = completed_task()
    first = create_patch(task["task_id"])
    second = create_patch(task["task_id"])

    listed = client.get(f"/api/tasks/{task['task_id']}/patches")

    assert listed.status_code == 200
    assert [item["patch_id"] for item in listed.json()["patches"]] == [
        first["patch_id"],
        second["patch_id"],
    ]


def test_patch_rejects_unknown_duplicate_unchanged_and_oversized_changes() -> None:
    task = completed_task()
    base_url = f"/api/tasks/{task['task_id']}/patches"

    unknown = client.post(
        base_url,
        json={
            "summary": "bad",
            "risk": "bad",
            "changes": [
                {"file": "new.py", "updated_content": "x = 1\n", "reason": "bad"},
            ],
        },
    )
    assert unknown.status_code == 409
    assert "现有文件" in unknown.json()["detail"]

    duplicate = client.post(
        base_url,
        json={
            "summary": "bad",
            "risk": "bad",
            "changes": [
                {"file": "src/example.py", "updated_content": "x = 1\n", "reason": "one"},
                {"file": "src/example.py", "updated_content": "x = 2\n", "reason": "two"},
            ],
        },
    )
    assert duplicate.status_code == 409
    assert "重复" in duplicate.json()["detail"]

    unchanged = client.post(
        base_url,
        json={
            "summary": "bad",
            "risk": "bad",
            "changes": [
                {
                    "file": "src/example.py",
                    "updated_content": "def run():\n    return 1\n",
                    "reason": "same",
                },
            ],
        },
    )
    assert unchanged.status_code == 409
    assert "相同" in unchanged.json()["detail"]

    oversized = client.post(
        base_url,
        json={
            "summary": "bad",
            "risk": "bad",
            "changes": [
                {
                    "file": "src/example.py",
                    "updated_content": "x" * 1_048_577,
                    "reason": "big",
                },
            ],
        },
    )
    assert oversized.status_code == 409
    assert "大小限制" in oversized.json()["detail"]


@pytest.mark.parametrize(
    ("payload", "extra_location"),
    [
        (
            {
                "summary": "strict",
                "risk": "strict",
                "changes": [
                    {
                        "file": "src/example.py",
                        "updated_content": "def run():\n    return 2\n",
                        "reason": "strict",
                    },
                ],
                "unexpected": True,
            },
            ["body", "unexpected"],
        ),
        (
            {
                "summary": "strict",
                "risk": "strict",
                "changes": [
                    {
                        "file": "src/example.py",
                        "updated_content": "def run():\n    return 2\n",
                        "reason": "strict",
                        "unexpected": True,
                    },
                ],
            },
            ["body", "changes", 0, "unexpected"],
        ),
    ],
)
def test_patch_json_rejects_extra_fields(
    payload: dict[str, object],
    extra_location: list[str | int],
) -> None:
    task = completed_task()

    response = client.post(f"/api/tasks/{task['task_id']}/patches", json=payload)

    assert response.status_code == 422
    assert any(error["loc"] == extra_location for error in response.json()["detail"])


def test_patch_requires_review_and_explicit_second_confirmation() -> None:
    task = completed_task()
    patch = create_patch(task["task_id"])

    apply_without_review = client.post(
        f"/api/patches/{patch['patch_id']}/apply",
        json={"confirm": True},
    )
    assert apply_without_review.status_code == 409

    approved = review_all(patch)
    assert approved["status"] == "approved"
    assert approved["can_apply"] is True

    missing_confirmation = client.post(f"/api/patches/{patch['patch_id']}/apply", json={})
    assert missing_confirmation.status_code == 422

    applied = client.post(
        f"/api/patches/{patch['patch_id']}/apply",
        json={"confirm": True},
    ).json()
    assert applied["status"] == "applied"
    assert applied["can_revert"] is True
    assert applied["can_download"] is True
    applied_event = applied["events"][-1]
    assert applied_event["actor"] == "local_user"
    assert "更新 run 和配置" in applied_event["detail"]
    assert "src/example.py" in applied_event["detail"]


def test_applied_patch_downloads_memory_snapshot_and_reverts() -> None:
    task = completed_task()
    patch = review_all(create_patch(task["task_id"]))
    patch_id = patch["patch_id"]
    client.post(f"/api/patches/{patch_id}/apply", json={"confirm": True})

    download = client.get(f"/api/patches/{patch_id}/files/src/example.py/download")
    assert download.status_code == 200
    assert download.text == "def run():\n    return 2\n"
    assert "attachment" in download.headers["content-disposition"]

    missing_confirmation = client.post(f"/api/patches/{patch_id}/revert", json={})
    assert missing_confirmation.status_code == 422
    reverted = client.post(
        f"/api/patches/{patch_id}/revert",
        json={"confirm": True},
    ).json()
    assert reverted["status"] == "reverted"
    restored = client.get(f"/api/patches/{patch_id}/files/src/example.py/download")
    assert restored.text == "def run():\n    return 1\n"


def test_stale_patch_conflict_does_not_overwrite_workspace() -> None:
    task = completed_task()
    first = review_all(create_patch(task["task_id"]))
    second = review_all(create_patch(task["task_id"]))

    first_result = client.post(
        f"/api/patches/{first['patch_id']}/apply",
        json={"confirm": True},
    ).json()
    assert first_result["status"] == "applied"

    second_result = client.post(
        f"/api/patches/{second['patch_id']}/apply",
        json={"confirm": True},
    ).json()
    assert second_result["status"] == "conflict"
    assert any(event["action"] == "conflict" for event in second_result["events"])


def test_rejected_file_is_ignored_by_conflict_validation_after_apply() -> None:
    task = completed_task()
    patch = create_patch(task["task_id"])
    first_file, rejected_file = patch["files"]
    client.post(
        f"/api/patches/{patch['patch_id']}/review",
        json={"file": first_file["file"], "decision": "accepted"},
    )
    approved = client.post(
        f"/api/patches/{patch['patch_id']}/review",
        json={"file": rejected_file["file"], "decision": "rejected"},
    ).json()
    assert approved["status"] == "approved"
    applied = client.post(
        f"/api/patches/{patch['patch_id']}/apply",
        json={"confirm": True},
    ).json()
    assert applied["status"] == "applied"
    patch_service.replace_workspace_content_for_test(
        task["task_id"],
        rejected_file["file"],
        '{"enabled": "changed elsewhere"}\n',
    )

    validated = client.post(
        f"/api/patches/{patch['patch_id']}/validate",
        json={"validators": ["conflict_check", "json_syntax"]},
    ).json()

    conflict_check, json_check = validated["validations"][-1]["checks"]
    assert conflict_check["status"] == "passed"
    assert conflict_check["exit_code"] == 0
    assert json_check["status"] == "skipped"
    assert json_check["exit_code"] is None


def test_builtin_validation_allowlist_and_syntax_results() -> None:
    task = completed_task()
    patch = review_all(create_patch(task["task_id"]))

    validators = client.get("/api/validators").json()
    assert [item["name"] for item in validators] == [
        "patch_integrity",
        "conflict_check",
        "whitespace",
        "json_syntax",
        "python_syntax",
        "sandbox_pytest",
        "sandbox_ruff",
        "sandbox_mypy",
        "sandbox_npm_test",
        "sandbox_npm_build",
    ]
    assert all(item["executes_code"] is False for item in validators[:5])
    assert all(item["executes_code"] is True for item in validators[5:])
    assert all(item["execution_kind"] == "sandbox" for item in validators[5:])

    response = client.post(
        f"/api/patches/{patch['patch_id']}/validate",
        json={"validators": []},
    )
    assert response.status_code == 200
    validated = response.json()
    assert validated["validations"][-1]["status"] == "passed"
    assert all(
        check["status"] == "passed"
        for check in validated["validations"][-1]["checks"]
    )
    assert all(
        check["exit_code"] == 0
        for check in validated["validations"][-1]["checks"]
    )

    rejected = client.post(
        f"/api/patches/{patch['patch_id']}/validate",
        json={"validators": ["pytest", "run_shell"]},
    )
    assert rejected.status_code == 409
    assert "允许列表" in rejected.json()["detail"]


def test_sandbox_validation_requires_explicit_confirmation() -> None:
    task = completed_task()
    patch = review_all(create_patch(task["task_id"]))

    response = client.post(
        f"/api/patches/{patch['patch_id']}/validate",
        json={"validators": ["sandbox_pytest"]},
    )

    assert response.status_code == 409
    assert "confirm_execution" in response.json()["detail"]


def test_confirmed_sandbox_validation_returns_503_when_runtime_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = completed_task()
    patch = review_all(create_patch(task["task_id"]))
    monkeypatch.setattr(
        sandbox_service,
        "status",
        lambda _settings: SimpleNamespace(
            available=False,
            reason="runtime unavailable",
        ),
    )

    response = client.post(
        f"/api/patches/{patch['patch_id']}/validate",
        json={
            "validators": ["sandbox_pytest"],
            "confirm_execution": True,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "runtime unavailable"


def test_invalid_python_validation_fails_without_executing_code() -> None:
    task = completed_task()
    response = client.post(
        f"/api/tasks/{task['task_id']}/patches",
        json={
            "summary": "invalid syntax",
            "risk": "test",
            "changes": [
                {
                    "file": "src/example.py",
                    "updated_content": "def run(:\n    return 2\n",
                    "reason": "test parser",
                },
            ],
            "suggested_validators": ["python_syntax"],
        },
    )
    patch = review_all(response.json())

    validated = client.post(
        f"/api/patches/{patch['patch_id']}/validate",
        json={"validators": ["python_syntax"]},
    ).json()
    check = validated["validations"][-1]["checks"][0]
    assert check["status"] == "failed"
    assert check["exit_code"] == 1
    assert "语法错误" in check["output"]


def test_validation_timeout_is_bounded_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = completed_task()
    patch = review_all(create_patch(task["task_id"]))

    def slow_validator(*args: object) -> tuple[str, str]:
        del args
        time.sleep(0.05)
        return "passed", "late"

    monkeypatch.setitem(
        patches_module.VALIDATOR_REGISTRY,
        "slow_check",
        ValidatorDefinition(
            spec=ValidatorSpec(
                name="slow_check",
                title="慢速检查",
                description="测试单检查超时。",
                timeout_seconds=0.001,
                max_output_chars=100,
            ),
            handler=slow_validator,
        ),
    )

    validated = client.post(
        f"/api/patches/{patch['patch_id']}/validate",
        json={"validators": ["slow_check"]},
    ).json()

    run = validated["validations"][-1]
    check = run["checks"][0]
    assert run["status"] == "timed_out"
    assert check["status"] == "timed_out"
    assert check["exit_code"] is None
    assert "超过" in check["output"]


def test_parse_patch_payload_accepts_json_fence_and_rejects_invalid_output() -> None:
    payload = parse_patch_payload(
        """```json
{"summary":"s","risk":"r","changes":[{"file":"a.py","updated_content":"x=1\\n","reason":"r"}]}
```""",
    )
    assert payload.changes[0].file == "a.py"

    with pytest.raises(Exception, match="结构化补丁"):
        parse_patch_payload("not json")

    with pytest.raises(Exception, match="结构化补丁"):
        parse_patch_payload(
            '{"summary":"s","risk":"r","changes":['
            '{"file":"a.py","updated_content":"x=1\\n","reason":"r",'
            '"unexpected":true}]}',
        )


def test_generate_patch_uses_patch_prompt_without_real_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = completed_task()
    captured: dict[str, str] = {}

    async def fake_request_chat_completion(
        *,
        context: str,
        settings: object,
        system_prompt: str,
    ) -> str:
        del settings
        captured["context"] = context
        captured["system_prompt"] = system_prompt
        return (
            '{"summary":"generated","risk":"review required","changes":['
            '{"file":"src/example.py","updated_content":"def run():\\n    return 3\\n",'
            '"reason":"generated fix"}]}'
        )

    monkeypatch.setattr(patches_module, "is_model_configured", lambda settings: True)
    monkeypatch.setattr(
        patches_module,
        "request_chat_completion",
        fake_request_chat_completion,
    )

    response = client.post(f"/api/tasks/{task['task_id']}/patches/generate")

    assert response.status_code == 200
    generated = response.json()
    assert generated["status"] == "draft"
    assert generated["events"][0]["actor"] == "model"
    assert captured["system_prompt"] == PATCH_SYSTEM_PROMPT
    assert "修复 run 返回值并更新配置" in captured["context"]
    assert "=== src/example.py (Python) ===" in captured["context"]


def test_patch_context_never_exposes_partial_file_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = completed_task()
    task_record = agent_task_service.get_completed_task_record(task["task_id"])
    workspace = {item.path: item.content for item in task_record.accepted_files}
    first_file = task_record.accepted_files[0]
    prefix = (
        f"用户任务：\n{task_record.goal}\n\n"
        "以下可修改文件均提供完整内容；未列出的文件不可修改：\n"
    )
    first_block = (
        f"\n=== {first_file.path} ({first_file.language}) ===\n"
        f"{first_file.content}\n"
    )
    monkeypatch.setattr(
        patches_module,
        "PATCH_GENERATION_CONTEXT_CHARS",
        len(prefix) + len(first_block),
    )

    context, included_paths = build_patch_context(task_record, workspace)

    assert included_paths == {"src/example.py"}
    assert context.endswith("def run():\n    return 1\n\n")
    assert "config.json" not in context
    assert '{"enabled": false}' not in context


def test_generate_patch_rejects_existing_file_omitted_from_bounded_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = completed_task()

    async def fake_request_chat_completion(**kwargs: object) -> str:
        del kwargs
        return (
            '{"summary":"bad","risk":"bad","changes":['
            '{"file":"config.json","updated_content":"{\\"enabled\\": true}\\n",'
            '"reason":"not in context"}]}'
        )

    monkeypatch.setattr(patches_module, "is_model_configured", lambda settings: True)
    monkeypatch.setattr(
        patches_module,
        "build_patch_context",
        lambda task, workspace: ("complete context", {"src/example.py"}),
    )
    monkeypatch.setattr(
        patches_module,
        "request_chat_completion",
        fake_request_chat_completion,
    )

    response = client.post(f"/api/tasks/{task['task_id']}/patches/generate")

    assert response.status_code == 409
    assert "完整提供上下文" in response.json()["detail"]
    assert "config.json" in response.json()["detail"]


def test_generate_patch_rejects_workspace_change_during_model_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = completed_task()

    async def fake_request_chat_completion(**kwargs: object) -> str:
        del kwargs
        patch_service.replace_workspace_content_for_test(
            task["task_id"],
            "src/example.py",
            "def run():\n    return 9\n",
        )
        return (
            '{"summary":"stale","risk":"stale","changes":['
            '{"file":"src/example.py","updated_content":"def run():\\n    return 3\\n",'
            '"reason":"stale generation"}]}'
        )

    monkeypatch.setattr(patches_module, "is_model_configured", lambda settings: True)
    monkeypatch.setattr(
        patches_module,
        "request_chat_completion",
        fake_request_chat_completion,
    )

    response = client.post(f"/api/tasks/{task['task_id']}/patches/generate")

    assert response.status_code == 409
    assert "生成期间文件版本已变化" in response.json()["detail"]
    assert "src/example.py" in response.json()["detail"]
    assert client.get(f"/api/tasks/{task['task_id']}/patches").json()["total"] == 0


@pytest.mark.parametrize(
    ("provider_output", "expected_detail"),
    [
        ("not json", "结构化补丁 JSON"),
        (
            (
                '{"summary":"bad","risk":"bad","changes":['
                '{"file":"outside.py","updated_content":"x = 1\\n","reason":"bad"}]}'
            ),
            "完整提供上下文",
        ),
    ],
)
def test_generate_patch_rejects_invalid_or_unauthorized_model_output(
    monkeypatch: pytest.MonkeyPatch,
    provider_output: str,
    expected_detail: str,
) -> None:
    task = completed_task()

    async def fake_request_chat_completion(**kwargs: object) -> str:
        del kwargs
        return provider_output

    monkeypatch.setattr(patches_module, "is_model_configured", lambda settings: True)
    monkeypatch.setattr(
        patches_module,
        "request_chat_completion",
        fake_request_chat_completion,
    )

    response = client.post(f"/api/tasks/{task['task_id']}/patches/generate")

    assert response.status_code == 409
    assert expected_detail in response.json()["detail"]


def test_generate_patch_requires_model_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = completed_task()
    provider_called = False

    async def unexpected_provider_call(**kwargs: object) -> str:
        nonlocal provider_called
        del kwargs
        provider_called = True
        return "{}"

    monkeypatch.setattr(patches_module, "is_model_configured", lambda settings: False)
    monkeypatch.setattr(
        patches_module,
        "request_chat_completion",
        unexpected_provider_call,
    )

    response = client.post(f"/api/tasks/{task['task_id']}/patches/generate")

    assert response.status_code == 409
    assert "模型配置" in response.json()["detail"]
    assert provider_called is False
