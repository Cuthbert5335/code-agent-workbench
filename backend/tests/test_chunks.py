from __future__ import annotations

import asyncio
from io import BytesIO

from fastapi import UploadFile
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.services.analysis import AcceptedFile
from app.services.chunks import build_code_chunks, chunk_accepted_file

client = TestClient(app)


def test_chunking_prefers_declaration_boundaries() -> None:
    result = chunk_accepted_file(
        AcceptedFile(
            path="src/example.py",
            language="Python",
            content=(
                "import os\n\n"
                "def first():\n"
                "    return 1\n\n"
                "class Second:\n"
                "    pass\n"
            ),
        ),
    )

    assert [(chunk.start_line, chunk.end_line) for chunk in result.chunks] == [
        (1, 2),
        (3, 5),
        (6, 7),
    ]
    assert result.chunks[1].content.startswith("def first")
    assert result.chunks[2].content.startswith("class Second")


def test_long_file_chunks_respect_line_and_character_limits() -> None:
    result = chunk_accepted_file(
        AcceptedFile(
            path="large.py",
            language="Python",
            content="\n".join(f"value_{index} = {index}" for index in range(20)),
        ),
        max_lines=5,
        max_chars=70,
    )

    assert len(result.chunks) > 1
    assert all(chunk.end_line - chunk.start_line + 1 <= 5 for chunk in result.chunks)
    assert all(len(chunk.content) <= 70 for chunk in result.chunks)
    assert result.chunks[0].start_line == 1
    assert result.chunks[-1].end_line == 20


def test_empty_file_returns_warning_without_chunks() -> None:
    result = chunk_accepted_file(
        AcceptedFile(path="empty.py", language="Python", content=""),
    )

    assert result.chunks == []
    assert result.warnings == ["已跳过 empty.py 的切片：文件内容为空。"]


def test_total_output_limit_marks_response_truncated() -> None:
    uploads = [
        UploadFile(
            filename="src/example.py",
            file=BytesIO(b"def first():\n    return 1\n\ndef second():\n    return 2\n"),
        ),
    ]

    response = asyncio.run(
        build_code_chunks(
            uploads=uploads,
            settings=Settings(model_api_key="", model_base_url="", model_name=""),
            max_chunks=1,
            max_total_chars=10_000,
        ),
    )

    assert response.truncated is True
    assert response.stats.chunks == 1
    assert any("总输出限制" in warning for warning in response.warnings)


def test_chunks_endpoint_reuses_file_safety_and_contract() -> None:
    response = client.post(
        "/api/chunks",
        files=[
            ("files", (".env", b"SECRET=value", "text/plain")),
            (
                "files",
                ("src/example.py", b"def add(a, b):\n    return a + b\n", "text/x-python"),
            ),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stats"]["accepted_files"] == 1
    assert payload["stats"]["skipped_files"] == 1
    assert payload["chunks"][0] == {
        "file": "src/example.py",
        "language": "Python",
        "start_line": 1,
        "end_line": 2,
        "content": "def add(a, b):\n    return a + b",
        "truncated": False,
    }

