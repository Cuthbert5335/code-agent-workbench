from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_index_returns_file_metadata_chunks_and_symbols() -> None:
    response = client.post(
        "/api/index",
        files=[
            (
                "files",
                (
                    "src/example.py",
                    b"class Worker:\n    pass\n\ndef run():\n    return 1\n",
                    "text/x-python",
                ),
            ),
            ("files", ("README.md", b"# Project\nDescription\n", "text/markdown")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["stats"] == {
        "received_files": 2,
        "accepted_files": 2,
        "skipped_files": 0,
        "indexed_files": 2,
        "chunks": 3,
        "symbols": 2,
        "content_chars": payload["stats"]["content_chars"],
    }
    assert payload["files"][0] == {
        "file": "src/example.py",
        "language": "Python",
        "size_chars": 48,
        "lines": 5,
        "chunks": 2,
        "symbols": 2,
    }


def test_index_reports_partial_when_files_are_skipped() -> None:
    response = client.post(
        "/api/index",
        files=[
            ("files", (".env", b"SECRET=value", "text/plain")),
            ("files", ("safe.py", b"def visible():\n    pass\n", "text/x-python")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["stats"]["skipped_files"] == 1
    assert payload["stats"]["indexed_files"] == 1
    assert any("疑似包含密钥或凭据" in warning for warning in payload["warnings"])


def test_index_marks_empty_safe_file_partial() -> None:
    response = client.post(
        "/api/index",
        files=[("files", ("empty.py", b"", "text/x-python"))],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["files"][0]["chunks"] == 0
    assert payload["files"][0]["symbols"] == 0
    assert any("文件内容为空" in warning for warning in payload["warnings"])
