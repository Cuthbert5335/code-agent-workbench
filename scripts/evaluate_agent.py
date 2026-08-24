#!/usr/bin/env python3
"""Evaluate exported CodeXXX Agent trajectories against a small local dataset.

This script intentionally uses only the Python standard library. It does not
call a model, execute repository code, or upload trajectory data anywhere.
The input trajectory file may be a JSON array or newline-delimited JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        records = decoded if isinstance(decoded, list) else [decoded]
    if not all(isinstance(record, dict) for record in records):
        raise ValueError(f"{path} 必须包含 JSON 对象记录。")
    return records


def as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def references_from_run(run: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for reference in run.get("references", []):
        if isinstance(reference, dict) and isinstance(reference.get("file"), str):
            references.add(reference["file"])
    for call in run.get("tool_calls", []):
        if not isinstance(call, dict):
            continue
        result = call.get("result")
        if not isinstance(result, dict):
            continue
        for evidence in result.get("evidence", []):
            if isinstance(evidence, dict) and isinstance(evidence.get("file"), str):
                references.add(evidence["file"])
    return references


def tool_counts(run: dict[str, Any]) -> tuple[int, int]:
    calls = [call for call in run.get("tool_calls", []) if isinstance(call, dict)]
    completed = sum(1 for call in calls if call.get("status") == "completed")
    return completed, len(calls)


def patch_outcome(run: dict[str, Any]) -> tuple[bool, bool]:
    patches = run.get("patches", [])
    if isinstance(patches, dict):
        patches = [patches]
    patches = [patch for patch in patches if isinstance(patch, dict)]
    applied = any(patch.get("status") == "applied" for patch in patches)
    validations = [
        validation
        for patch in patches
        for validation in patch.get("validations", [])
        if isinstance(validation, dict)
    ]
    validated = any(validation.get("status") == "passed" for validation in validations)
    return applied, validated


def duration_seconds(run: dict[str, Any]) -> float | None:
    duration = run.get("duration_ms")
    if isinstance(duration, (int, float)):
        return float(duration) / 1000
    return None


def evaluate(dataset: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    run_by_id = {
        run.get("evaluation_id"): run
        for run in runs
        if isinstance(run.get("evaluation_id"), str)
    }
    rows: list[dict[str, Any]] = []
    answer_hits = reference_hits = 0
    reference_precision_sum = reference_recall_sum = 0.0
    completed_tools = total_tools = 0
    applied_patches = expected_patches = passed_validations = expected_validations = 0
    durations: list[float] = []
    cancelled = failed = 0

    for sample in dataset:
        evaluation_id = sample.get("id")
        run = run_by_id.get(evaluation_id, {})
        answer = as_text(run.get("final_answer"))
        keywords = [keyword.casefold() for keyword in sample.get("answer_keywords", [])]
        answer_ok = bool(keywords) and all(keyword in answer.casefold() for keyword in keywords)
        expected_refs = set(sample.get("expected_references", []))
        actual_refs = references_from_run(run)
        intersection = expected_refs & actual_refs
        precision = len(intersection) / len(actual_refs) if actual_refs else 0.0
        recall = len(intersection) / len(expected_refs) if expected_refs else 1.0
        tools_ok, tools_total = tool_counts(run)
        applied, validated = patch_outcome(run)
        wants_patch = bool(sample.get("allow_modification", False))
        wants_validation = bool(sample.get("expected_validation", False))
        status = as_text(run.get("status"))
        if answer_ok:
            answer_hits += 1
        if recall == 1.0:
            reference_hits += 1
        if wants_patch:
            expected_patches += 1
            applied_patches += int(applied)
        if wants_validation:
            expected_validations += 1
            passed_validations += int(validated)
        completed_tools += tools_ok
        total_tools += tools_total
        reference_precision_sum += precision
        reference_recall_sum += recall
        if (duration := duration_seconds(run)) is not None:
            durations.append(duration)
        cancelled += int(status == "cancelled")
        failed += int(status in {"failed", "timed_out", "blocked"})
        rows.append({
            "id": evaluation_id,
            "found": bool(run),
            "answer_keyword_match": answer_ok,
            "reference_precision": round(precision, 4),
            "reference_recall": round(recall, 4),
            "tool_success": f"{tools_ok}/{tools_total}",
            "patch_applied": applied if wants_patch else None,
            "validation_passed": validated if wants_validation else None,
            "status": status or "missing",
        })

    count = len(dataset)
    report = {
        "samples": count,
        "matched_runs": sum(row["found"] for row in rows),
        "answer_keyword_accuracy": round(answer_hits / count, 4) if count else 0.0,
        "reference_complete_accuracy": round(reference_hits / count, 4) if count else 0.0,
        "reference_precision": round(reference_precision_sum / count, 4) if count else 0.0,
        "reference_recall": round(reference_recall_sum / count, 4) if count else 0.0,
        "tool_success_rate": round(completed_tools / total_tools, 4) if total_tools else 0.0,
        "patch_apply_success_rate": round(applied_patches / expected_patches, 4) if expected_patches else None,
        "validation_pass_rate": round(passed_validations / expected_validations, 4) if expected_validations else None,
        "average_duration_seconds": round(sum(durations) / len(durations), 3) if durations else None,
        "cancel_rate": round(cancelled / count, 4) if count else 0.0,
        "failure_rate": round(failed / count, 4) if count else 0.0,
        "cases": rows,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="评估导出的 CodeXXX Agent 轨迹。")
    parser.add_argument("--dataset", type=Path, required=True, help="固定问题集 JSON 文件")
    parser.add_argument("--runs", type=Path, required=True, help="任务轨迹 JSON 或 JSONL 文件")
    parser.add_argument("--output", type=Path, help="可选：写入完整指标 JSON")
    args = parser.parse_args()
    try:
        report = evaluate(load_json_records(args.dataset), load_json_records(args.runs))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"评测输入错误：{error}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
