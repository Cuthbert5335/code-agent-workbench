from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_symbols_extract_common_multilanguage_declarations() -> None:
    response = client.post(
        "/api/symbols",
        files=[
            (
                "files",
                (
                    "src/example.py",
                    b"class Worker:\n    pass\n\nasync def load_data():\n    return []\n",
                    "text/x-python",
                ),
            ),
            (
                "files",
                (
                    "src/app.ts",
                    b"export interface Config {}\nexport const runTask = async () => {}\n",
                    "text/typescript",
                ),
            ),
            (
                "files",
                (
                    "src/main.go",
                    b"type Server struct {}\nfunc (s *Server) Start() {}\n",
                    "text/plain",
                ),
            ),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert [(item["name"], item["kind"], item["line_number"]) for item in payload["symbols"]] == [
        ("Worker", "class", 1),
        ("load_data", "function", 4),
        ("Config", "interface", 1),
        ("runTask", "function", 2),
        ("Server", "struct", 1),
        ("Start", "function", 2),
    ]


def test_symbols_filter_names_case_insensitively_and_keep_duplicates() -> None:
    response = client.post(
        "/api/symbols",
        data={"query": "LOAD"},
        files=[
            ("files", ("a.py", b"def load_data():\n    pass\n", "text/x-python")),
            ("files", ("b.py", b"def preload():\n    pass\n", "text/x-python")),
            ("files", ("c.py", b"def other():\n    pass\n", "text/x-python")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "LOAD"
    assert [item["name"] for item in payload["symbols"]] == ["load_data", "preload"]


def test_symbols_return_empty_for_files_without_supported_declarations() -> None:
    response = client.post(
        "/api/symbols",
        files=[("files", ("README.md", b"# Project\nText only\n", "text/markdown"))],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbols"] == []
    assert payload["stats"]["symbol_files"] == 0


def test_symbols_reuse_sensitive_file_filtering() -> None:
    response = client.post(
        "/api/symbols",
        files=[
            ("files", (".env", b"def secret():\n    pass\n", "text/plain")),
            ("files", ("safe.py", b"def visible():\n    pass\n", "text/x-python")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload["symbols"]] == ["visible"]
    assert payload["stats"]["skipped_files"] == 1


def test_symbols_enforce_per_file_limit_and_continue_other_files() -> None:
    crowded_file = "".join(
        f"def crowded_{index}():\n" for index in range(201)
    ).encode()
    response = client.post(
        "/api/symbols",
        files=[
            ("files", ("crowded.py", crowded_file, "text/x-python")),
            ("files", ("next.py", b"def visible_after_limit():\n", "text/x-python")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["truncated"] is True
    assert payload["stats"]["symbols"] == 201
    assert payload["symbols"][-1]["name"] == "visible_after_limit"
    assert any("单文件 200 条限制" in warning for warning in payload["warnings"])


def test_symbols_enforce_total_result_limit() -> None:
    files = []
    for file_index in range(3):
        content = "".join(
            f"def file_{file_index}_symbol_{symbol_index}():\n"
            for symbol_index in range(201)
        ).encode()
        files.append(
            ("files", (f"file_{file_index}.py", content, "text/x-python")),
        )

    response = client.post("/api/symbols", files=files)

    assert response.status_code == 200
    payload = response.json()
    assert payload["truncated"] is True
    assert payload["stats"]["symbols"] == 500
    assert any("符号总结果达到 500 条限制" in warning for warning in payload["warnings"])
