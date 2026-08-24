#!/usr/bin/env python3
"""Export non-content Agent trajectory records from a local CodeXXX SQLite DB.

The export deliberately excludes uploaded file contents, patch file bodies,
model configuration, and session data. It is intended for local learning and
regression evaluation only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def decode(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def export(database: Path, output: Path) -> int:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        tasks = connection.execute(
            """
            SELECT task_id, project_id, goal, mode, status, plan_json,
                   warnings_json, transitions_json, final_answer,
                   created_at, updated_at
            FROM agent_tasks
            ORDER BY created_at, task_id
            """,
        ).fetchall()
        records: list[dict[str, Any]] = []
        for task in tasks:
            task_id = str(task["task_id"])
            calls = connection.execute(
                """
                SELECT sequence, tool_name, status, record_json
                FROM tool_calls WHERE task_id = ? ORDER BY sequence
                """,
                (task_id,),
            ).fetchall()
            patches = connection.execute(
                """
                SELECT patch_id, status, summary, risk, suggested_validators_json,
                       events_json, created_at, updated_at
                FROM patches WHERE task_id = ? ORDER BY created_at, patch_id
                """,
                (task_id,),
            ).fetchall()
            patch_records = []
            for patch in patches:
                validations = connection.execute(
                    """
                    SELECT validation_id, status, created_at, run_json
                    FROM validation_runs WHERE patch_id = ? ORDER BY created_at
                    """,
                    (patch["patch_id"],),
                ).fetchall()
                patch_records.append(
                    {
                        "patch_id": patch["patch_id"],
                        "status": patch["status"],
                        "summary": patch["summary"],
                        "risk": patch["risk"],
                        "suggested_validators": decode(patch["suggested_validators_json"], []),
                        "events": decode(patch["events_json"], []),
                        "created_at": patch["created_at"],
                        "updated_at": patch["updated_at"],
                        "validations": [
                            {
                                "validation_id": validation["validation_id"],
                                "status": validation["status"],
                                "created_at": validation["created_at"],
                                "checks": (
                                    decode(validation["run_json"], {}).get("checks", [])
                                    if isinstance(decode(validation["run_json"], {}), dict)
                                    else []
                                ),
                            }
                            for validation in validations
                        ],
                    },
                )
            records.append(
                {
                    "evaluation_id": task_id,
                    "task_id": task_id,
                    "project_id": task["project_id"],
                    "goal": task["goal"],
                    "mode": task["mode"],
                    "status": task["status"],
                    "plan": decode(task["plan_json"], []),
                    "warnings": decode(task["warnings_json"], []),
                    "transitions": decode(task["transitions_json"], []),
                    "final_answer": task["final_answer"],
                    "created_at": task["created_at"],
                    "updated_at": task["updated_at"],
                    "tool_calls": [],
                    "patches": patch_records,
                },
            )
            for call in calls:
                call_record = decode(call["record_json"], {})
                if not isinstance(call_record, dict):
                    call_record = {}
                records[-1]["tool_calls"].append(
                    {
                        "sequence": call["sequence"],
                        "tool_name": call["tool_name"],
                        "status": call["status"],
                        **call_record,
                    },
                )
    finally:
        connection.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 CodeXXX Agent 轨迹（不含文件内容）。")
    parser.add_argument("--database", type=Path, default=Path("backend/data/codexxx.db"))
    parser.add_argument("--output", type=Path, default=Path("agent-trajectories.json"))
    args = parser.parse_args()
    count = export(args.database, args.output)
    print(f"已导出 {count} 条轨迹到 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
