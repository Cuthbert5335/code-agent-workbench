from __future__ import annotations

from app.schemas.analysis import ConversationMessage
from app.services.analysis import AcceptedFile, build_context, get_skip_reason


def test_build_context_enforces_character_limit_and_reports_truncation() -> None:
    result = build_context(
        question="解释这段代码",
        files=[
            AcceptedFile(
                path="example.py",
                language="Python",
                content="\n".join(f"value_{index} = {index}" for index in range(100)),
            ),
        ],
        conversation=[ConversationMessage(role="user", content="请保留行号" * 30)],
        max_chars=240,
    )

    assert len(result.text) <= 240
    assert result.references[0].file == "example.py"
    assert result.references[0].truncated is True
    assert any("被截断" in warning for warning in result.warnings)
    assert result.text.index("文件：example.py") < result.text.find("最近对话") or "最近对话" not in result.text


def test_sensitive_and_irrelevant_paths_are_skipped() -> None:
    assert get_skip_reason(".env.local") == "疑似包含密钥或凭据"
    assert get_skip_reason("node_modules/package/index.js") == "位于默认忽略的目录中"
    assert get_skip_reason("certificate.pem") == "密钥或证书文件不允许载入"
    assert get_skip_reason("image.png") == "不支持的文件类型"
    assert get_skip_reason("src/main.py") is None
