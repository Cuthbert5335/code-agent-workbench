from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post_search(
    *,
    query: str = "return",
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
):
    return client.post(
        "/api/search",
        data={"query": query},
        files=files
        or [
            (
                "files",
                (
                    "src/example.py",
                    b"def add(a, b):\n    return a + b\n",
                    "text/x-python",
                ),
            ),
        ],
    )


def test_search_returns_paths_lines_and_context() -> None:
    response = post_search(
        query="value",
        files=[
            (
                "files",
                (
                    "src/example.py",
                    b"before = 1\nvalue = before + 1\nprint(value)\n",
                    "text/x-python",
                ),
            ),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "value"
    assert payload["stats"]["matched_files"] == 1
    assert payload["stats"]["matched_lines"] == 2
    assert payload["results"][0] == {
        "file": "src/example.py",
        "language": "Python",
        "line_number": 2,
        "column": 1,
        "match_count": 1,
        "line": "value = before + 1",
        "before": ["before = 1"],
        "after": ["print(value)"],
        "line_truncated": False,
    }


def test_search_is_case_insensitive_and_literal() -> None:
    response = post_search(
        query="A+B",
        files=[
            ("files", ("example.txt", b"a+b\naab\n", "text/plain")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stats"]["matched_lines"] == 1
    assert payload["results"][0]["line"] == "a+b"


def test_search_rejects_blank_query() -> None:
    response = post_search(query="   ")

    assert response.status_code == 422
    assert response.json()["detail"] == "搜索关键词不能为空。"


def test_search_reuses_sensitive_file_filtering() -> None:
    response = post_search(
        files=[
            ("files", (".env", b"return=secret", "text/plain")),
            ("files", ("safe.py", b"return_value = 1\n", "text/x-python")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stats"]["accepted_files"] == 1
    assert payload["stats"]["skipped_files"] == 1
    assert [result["file"] for result in payload["results"]] == ["safe.py"]
    assert any("疑似包含密钥或凭据" in warning for warning in payload["warnings"])

