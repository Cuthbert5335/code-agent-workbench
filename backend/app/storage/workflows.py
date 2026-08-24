"""SQLite repository for durable Agent tasks, patches, and validations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import nullcontext
from typing import Any

from app.storage import database


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_value(value: str) -> Any:
    return json.loads(value)


class WorkflowStore:
    """Persist complete bounded snapshots while keeping relational ownership keys."""

    @property
    def generation(self) -> int:
        return database.generation

    def save_task(
        self,
        task: dict[str, Any],
        files: Sequence[dict[str, str]],
        tool_calls: Sequence[dict[str, Any]],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        connection_context = (
            database.connect() if connection is None else nullcontext(connection)
        )
        with connection_context as active_connection:
            active_connection.execute(
                """
                INSERT INTO agent_tasks (
                    task_id, project_id, owner_user_id, goal, mode, status,
                    plan_json, warnings_json, transitions_json, final_answer,
                    cancel_requested, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    owner_user_id = excluded.owner_user_id,
                    goal = excluded.goal,
                    mode = excluded.mode,
                    status = excluded.status,
                    plan_json = excluded.plan_json,
                    warnings_json = excluded.warnings_json,
                    transitions_json = excluded.transitions_json,
                    final_answer = excluded.final_answer,
                    cancel_requested = excluded.cancel_requested,
                    updated_at = excluded.updated_at
                """,
                (
                    task["task_id"],
                    task["project_id"],
                    task["owner_user_id"],
                    task["goal"],
                    task["mode"],
                    task["status"],
                    json_text(task["plan"]),
                    json_text(task["warnings"]),
                    json_text(task["transitions"]),
                    task["final_answer"],
                    int(task["cancel_requested"]),
                    task["created_at"],
                    task["updated_at"],
                ),
            )
            active_connection.execute(
                "DELETE FROM task_files WHERE task_id = ?",
                (task["task_id"],),
            )
            active_connection.executemany(
                """
                INSERT INTO task_files (task_id, path, language, content)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (task["task_id"], item["path"], item["language"], item["content"])
                    for item in files
                ],
            )
            active_connection.execute(
                "DELETE FROM tool_calls WHERE task_id = ?",
                (task["task_id"],),
            )
            active_connection.executemany(
                """
                INSERT INTO tool_calls (
                    tool_call_id, task_id, sequence, tool_name, status, record_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        task["task_id"],
                        sequence,
                        item["tool_name"],
                        item["status"],
                        json_text(item),
                    )
                    for sequence, item in enumerate(tool_calls)
                ],
            )

    def load_task(
        self,
        task_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        connection_context = (
            database.connect() if connection is None else nullcontext(connection)
        )
        with connection_context as active_connection:
            row = active_connection.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            files = active_connection.execute(
                "SELECT path, language, content FROM task_files WHERE task_id = ? ORDER BY rowid",
                (task_id,),
            ).fetchall()
            tool_calls = active_connection.execute(
                "SELECT record_json FROM tool_calls WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
        return {
            **dict(row),
            "plan": json_value(row["plan_json"]),
            "warnings": json_value(row["warnings_json"]),
            "transitions": json_value(row["transitions_json"]),
            "files": [dict(item) for item in files],
            "tool_calls": [json_value(item["record_json"]) for item in tool_calls],
        }

    def list_task_ids(
        self,
        *,
        project_ids: Iterable[str] | None = None,
        legacy_only: bool = False,
        limit: int = 100,
    ) -> list[str]:
        parameters: list[object] = []
        where = ""
        if legacy_only:
            where = "WHERE project_id IS NULL AND owner_user_id IS NULL"
        elif project_ids is not None:
            ids = tuple(project_ids)
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            where = f"WHERE project_id IN ({placeholders})"
            parameters.extend(ids)
        parameters.append(limit)
        with database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT task_id FROM agent_tasks {where}
                ORDER BY updated_at DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [row["task_id"] for row in rows]

    def count_tasks(
        self,
        project_id: str | None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        where = "project_id = ?" if project_id is not None else "project_id IS NULL"
        parameters: tuple[object, ...] = (project_id,) if project_id is not None else ()
        connection_context = (
            database.connect() if connection is None else nullcontext(connection)
        )
        with connection_context as active_connection:
            return int(
                active_connection.execute(
                    f"SELECT COUNT(*) FROM agent_tasks WHERE {where}",
                    parameters,
                ).fetchone()[0],
            )

    def oldest_terminal_task_id(
        self,
        statuses: Sequence[str],
        project_id: str | None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> str | None:
        placeholders = ",".join("?" for _ in statuses)
        project_filter = "project_id = ?" if project_id is not None else "project_id IS NULL"
        parameters: tuple[object, ...] = (
            (*statuses, project_id) if project_id is not None else tuple(statuses)
        )
        connection_context = (
            database.connect() if connection is None else nullcontext(connection)
        )
        with connection_context as active_connection:
            row = active_connection.execute(
                f"""
                SELECT task_id FROM agent_tasks
                WHERE status IN ({placeholders}) AND {project_filter}
                ORDER BY updated_at ASC LIMIT 1
                """,
                parameters,
            ).fetchone()
        return row["task_id"] if row else None

    def delete_task(
        self,
        task_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        connection_context = (
            database.connect() if connection is None else nullcontext(connection)
        )
        with connection_context as active_connection:
            active_connection.execute(
                "DELETE FROM agent_tasks WHERE task_id = ?",
                (task_id,),
            )

    def save_patch(
        self,
        patch: dict[str, Any],
        validations: Sequence[dict[str, Any]],
    ) -> None:
        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO patches (
                    patch_id, task_id, status, summary, risk, files_json,
                    base_contents_json, proposed_contents_json,
                    suggested_validators_json, events_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(patch_id) DO UPDATE SET
                    status = excluded.status,
                    summary = excluded.summary,
                    risk = excluded.risk,
                    files_json = excluded.files_json,
                    base_contents_json = excluded.base_contents_json,
                    proposed_contents_json = excluded.proposed_contents_json,
                    suggested_validators_json = excluded.suggested_validators_json,
                    events_json = excluded.events_json,
                    updated_at = excluded.updated_at
                """,
                (
                    patch["patch_id"],
                    patch["task_id"],
                    patch["status"],
                    patch["summary"],
                    patch["risk"],
                    json_text(patch["files"]),
                    json_text(patch["base_contents"]),
                    json_text(patch["proposed_contents"]),
                    json_text(patch["suggested_validators"]),
                    json_text(patch["events"]),
                    patch["created_at"],
                    patch["updated_at"],
                ),
            )
            connection.execute(
                "DELETE FROM validation_runs WHERE patch_id = ?",
                (patch["patch_id"],),
            )
            connection.executemany(
                """
                INSERT INTO validation_runs (
                    validation_id, patch_id, status, created_at, run_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["validation_id"],
                        patch["patch_id"],
                        item["status"],
                        item["created_at"],
                        json_text(item),
                    )
                    for item in validations
                ],
            )

    def load_patch(self, patch_id: str) -> dict[str, Any] | None:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM patches WHERE patch_id = ?",
                (patch_id,),
            ).fetchone()
            if row is None:
                return None
            validations = connection.execute(
                """
                SELECT run_json FROM validation_runs
                WHERE patch_id = ? ORDER BY created_at
                """,
                (patch_id,),
            ).fetchall()
        return {
            **dict(row),
            "files": json_value(row["files_json"]),
            "base_contents": json_value(row["base_contents_json"]),
            "proposed_contents": json_value(row["proposed_contents_json"]),
            "suggested_validators": json_value(row["suggested_validators_json"]),
            "events": json_value(row["events_json"]),
            "validations": [json_value(item["run_json"]) for item in validations],
        }

    def list_patch_ids(self, task_id: str) -> list[str]:
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT patch_id FROM patches
                WHERE task_id = ? ORDER BY created_at ASC, rowid ASC
                """,
                (task_id,),
            ).fetchall()
        return [row["patch_id"] for row in rows]

    def load_idempotency(
        self,
        *,
        scope: str,
        idempotency_key: str,
        connection: sqlite3.Connection,
    ) -> tuple[str, str] | None:
        """Return the fingerprint and task bound to one scoped request key."""

        row = connection.execute(
            """
            SELECT request_fingerprint, task_id FROM task_idempotency
            WHERE scope = ? AND idempotency_key = ?
            """,
            (scope, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        return str(row["request_fingerprint"]), str(row["task_id"])

    def save_idempotency(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_fingerprint: str,
        task_id: str,
        created_at: str,
        connection: sqlite3.Connection,
    ) -> None:
        """Bind a validated request key in the same transaction as task creation."""

        connection.execute(
            """
            INSERT INTO task_idempotency (
                scope, idempotency_key, request_fingerprint, task_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (scope, idempotency_key, request_fingerprint, task_id, created_at),
        )

    def clear_workflows(self) -> None:
        with database.connect() as connection:
            connection.execute("DELETE FROM agent_tasks")

    def clear_patches(self) -> None:
        with database.connect() as connection:
            connection.execute("DELETE FROM patches")


workflow_store = WorkflowStore()
