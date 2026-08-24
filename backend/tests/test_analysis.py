from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers.openai_compatible import (
    ModelProviderError,
    ModelResponseError,
    ModelTimeoutError,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def use_demo_settings_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent local .env model credentials from making tests call a real API."""

    monkeypatch.setattr(
        "app.api.analysis.settings",
        Settings(model_api_key="", model_base_url="", model_name=""),
    )


def post_analysis(
    *,
    question: str = "这个函数做了什么？",
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    conversation: str | None = None,
    path: str = "/api/analyze",
):
    form_data = {"question": question}
    if conversation is not None:
        form_data["conversation"] = conversation

    return client.post(
        path,
        data=form_data,
        files=files
        or [
            (
                "files",
                ("example.py", b"def add(a, b):\n    return a + b\n", "text/x-python"),
            ),
        ],
    )


def test_analyze_returns_structured_demo_response() -> None:
    response = post_analysis(
        files=[
            (
                "files",
                ("src/example.py", b"def add(a, b):\n    return a + b\n", "text/x-python"),
            ),
            (
                "files",
                ("README.md", "# 示例项目\n".encode(), "text/markdown"),
            ),
        ],
        conversation='[{"role":"user","content":"这是一个示例项目"}]',
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "demo"
    assert "演示模式" in payload["answer"]
    assert payload["stats"] == {
        "received_files": 2,
        "accepted_files": 2,
        "skipped_files": 0,
        "context_chars": payload["stats"]["context_chars"],
        "conversation_messages": 1,
    }
    assert 0 < payload["stats"]["context_chars"] <= 60_000
    assert payload["references"] == [
        {
            "file": "src/example.py",
            "language": "Python",
            "start_line": 1,
            "end_line": 2,
            "truncated": False,
        },
        {
            "file": "README.md",
            "language": "Markdown",
            "start_line": 1,
            "end_line": 1,
            "truncated": False,
        },
    ]
    assert payload["warnings"][0].startswith("当前未调用真实模型")


def test_analysis_alias_uses_the_same_contract() -> None:
    response = post_analysis(path="/api/analysis")

    assert response.status_code == 200
    assert response.json()["mode"] == "demo"


def test_analyze_rejects_blank_question() -> None:
    response = post_analysis(question="   ")

    assert response.status_code == 422
    assert response.json()["detail"] == "问题不能为空，请输入需要分析的代码问题。"


def test_analyze_rejects_oversized_file() -> None:
    response = post_analysis(
        files=[
            (
                "files",
                ("large.py", b"x" * 1_048_577, "text/x-python"),
            ),
        ],
    )

    assert response.status_code == 413
    assert "超过单文件大小限制" in response.json()["detail"]


def test_analyze_rejects_path_traversal() -> None:
    response = post_analysis(
        files=[
            (
                "files",
                ("../../secret.py", b"print('no')\n", "text/x-python"),
            ),
        ],
    )

    assert response.status_code == 422
    assert "路径穿越" in response.json()["detail"]


def test_analyze_rejects_request_when_no_supported_file_remains() -> None:
    response = post_analysis(
        files=[
            ("files", ("photo.png", b"not-an-image", "image/png")),
        ],
    )

    assert response.status_code == 422
    assert "不支持的文件类型" in response.json()["detail"]


def test_analyze_skips_sensitive_file_when_safe_file_exists() -> None:
    response = post_analysis(
        files=[
            ("files", (".env", b"API_KEY=secret", "text/plain")),
            ("files", ("safe.py", b"value = 1\n", "text/x-python")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stats"]["accepted_files"] == 1
    assert payload["stats"]["skipped_files"] == 1
    assert any("疑似包含密钥或凭据" in warning for warning in payload["warnings"])
    assert [reference["file"] for reference in payload["references"]] == ["safe.py"]


def test_analyze_rejects_invalid_conversation_json() -> None:
    response = post_analysis(conversation="not-json")

    assert response.status_code == 422
    assert "JSON 消息数组" in response.json()["detail"]


def test_openapi_contains_both_analysis_paths() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/analyze" in paths
    assert "/api/analysis" in paths
    assert paths["/api/analysis"]["post"]["deprecated"] is True


def test_analyze_uses_real_model_and_validates_references(monkeypatch: pytest.MonkeyPatch) -> None:
    configured_settings = Settings(
        model_api_key="test-key",
        model_base_url="https://example.test/v1",
        model_name="test-model",
    )
    monkeypatch.setattr("app.api.analysis.settings", configured_settings)

    async def fake_request_chat_completion(*, context: str, settings: Settings) -> str:
        assert "文件：example.py" in context
        assert settings.model_name == "test-model"
        return "结论：函数返回两个参数的和。[引用: example.py: 1-2]"

    monkeypatch.setattr(
        "app.services.analysis.request_chat_completion",
        fake_request_chat_completion,
    )

    response = post_analysis()

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "real"
    assert payload["answer"].startswith("结论：")
    assert payload["references"] == [
        {
            "file": "example.py",
            "language": "Python",
            "start_line": 1,
            "end_line": 2,
            "truncated": False,
        },
    ]
    assert payload["warnings"] == []


@pytest.mark.parametrize(
    ("provider_error", "status_code", "detail"),
    [
        (ModelTimeoutError(), 504, "模型服务请求超时"),
        (ModelProviderError(), 502, "模型服务调用失败"),
        (ModelResponseError(), 502, "模型服务返回了无效响应"),
    ],
)
def test_analyze_maps_model_failures_to_safe_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: Exception,
    status_code: int,
    detail: str,
) -> None:
    configured_settings = Settings(
        model_api_key="test-key",
        model_base_url="https://example.test/v1",
        model_name="test-model",
    )
    monkeypatch.setattr("app.api.analysis.settings", configured_settings)

    async def failing_request_chat_completion(*, context: str, settings: Settings) -> str:
        raise provider_error

    monkeypatch.setattr(
        "app.services.analysis.request_chat_completion",
        failing_request_chat_completion,
    )

    response = post_analysis()

    assert response.status_code == status_code
    assert detail in response.json()["detail"]
