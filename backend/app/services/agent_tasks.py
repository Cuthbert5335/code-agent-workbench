"""Durable single-Agent planning and controlled execution workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from uuid import uuid4

from fastapi import UploadFile

from app.config import Settings
from app.schemas.agents import (
    AgentMode,
    AgentTaskListResponse,
    AgentTaskResponse,
    AgentTaskStatus,
    TaskPlanStep,
    TaskQueueState,
    TaskTransition,
    ToolCallRecord,
)
from app.services.agent_tools import (
    TOOL_REGISTRY,
    ToolContext,
    execute_registered_tool,
)
from app.services.analysis import (
    AcceptedFile,
    AnalysisInputError,
    process_uploads,
    validate_question,
)
from app.services.usage import UsageContext, usage_service
from app.storage import database
from app.storage.task_queue import (
    QueueJob,
    QueueLeaseLostError,
    RecoveredJob,
    task_queue_store,
)
from app.storage.workflows import workflow_store

logger = logging.getLogger("uvicorn.error")

MAX_STORED_TASKS = 20
MAX_TASK_LIST_ITEMS = 20
RESUMABLE_STATUSES: set[AgentTaskStatus] = {
    "cancelled",
    "failed",
    "timed_out",
    "blocked",
}
CANCELLABLE_STATUSES: set[AgentTaskStatus] = {
    "created",
    "planning",
    "waiting_for_confirmation",
    "queued",
    "executing",
    "reviewing",
    "validating",
}
TERMINAL_STATUSES: set[AgentTaskStatus] = {
    "completed",
    "cancelled",
    "failed",
    "timed_out",
    "blocked",
}
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_$][\w$.-]{1,79}")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
IDENTIFIER_STOPWORDS = {
    "agent",
    "code",
    "error",
    "explain",
    "file",
    "find",
    "function",
    "project",
    "please",
    "test",
}


class AgentTaskError(Exception):
    """Base class for expected task lifecycle failures."""

    status_code = 409

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AgentTaskNotFoundError(AgentTaskError):
    """The requested durable task does not exist."""

    status_code = 404


class AgentTaskInputError(AgentTaskError):
    """A task lifecycle input is malformed."""

    status_code = 400


@dataclass
class AgentTaskRecord:
    """Mutable internal state plus private validated file content."""

    task_id: str
    project_id: str | None
    owner_user_id: str | None
    goal: str
    mode: AgentMode
    status: AgentTaskStatus
    created_at: datetime
    updated_at: datetime
    accepted_files: tuple[AcceptedFile, ...]
    plan: list[TaskPlanStep]
    warnings: list[str]
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    transitions: list[TaskTransition] = field(default_factory=list)
    final_answer: str | None = None
    cancel_requested: bool = False


class AgentTaskService:
    """Manage bounded Agent task snapshots backed by local SQLite."""

    def __init__(self) -> None:
        self._tasks: dict[str, AgentTaskRecord] = {}
        self._active_jobs: dict[str, tuple[QueueJob, Future[AgentTaskResponse]]] = {}
        self._database_generation = workflow_store.generation
        self._executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="codexxx-agent")
        self._queue_lock = Lock()
        self._queue_stop = Event()
        self._queue_wake = Event()
        self._dispatcher: Thread | None = None
        self._queue_settings: Settings | None = None
        self._worker_id = f"worker-{uuid4().hex}"

    def clear(self) -> None:
        """Clear tasks and durable workflow rows for deterministic tests."""

        self.stop_queue(wait=True)
        self._active_jobs.clear()
        self._tasks.clear()
        workflow_store.clear_workflows()

    def discard_cached_tasks(self, task_ids: set[str]) -> None:
        """Forget deleted durable tasks without disturbing active queue workers."""

        self._sync_cache_generation()
        for task_id in task_ids:
            self._tasks.pop(task_id, None)

    def use_database_for_test(self, path: str) -> None:
        """Point shared services at one isolated SQLite database."""

        self.stop_queue(wait=True)
        database.reconfigure(path)
        self._sync_cache_generation()

    def _sync_cache_generation(self) -> None:
        if self._database_generation == workflow_store.generation:
            return
        self.stop_queue(wait=True)
        self._active_jobs.clear()
        self._tasks.clear()
        self._database_generation = workflow_store.generation

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _get_record(
        self,
        task_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> AgentTaskRecord:
        self._sync_cache_generation()
        stored = workflow_store.load_task(task_id, connection=connection)
        if stored is None:
            raise AgentTaskNotFoundError("Agent 任务不存在。")
        record = self._record_from_stored(stored)
        self._tasks[task_id] = record
        return record

    def _record_from_stored(self, stored: dict[str, object]) -> AgentTaskRecord:
        return AgentTaskRecord(
            task_id=str(stored["task_id"]),
            project_id=str(stored["project_id"]) if stored["project_id"] else None,
            owner_user_id=(
                str(stored["owner_user_id"]) if stored["owner_user_id"] else None
            ),
            goal=str(stored["goal"]),
            mode=str(stored["mode"]),  # type: ignore[arg-type]
            status=str(stored["status"]),  # type: ignore[arg-type]
            created_at=datetime.fromisoformat(str(stored["created_at"])),
            updated_at=datetime.fromisoformat(str(stored["updated_at"])),
            accepted_files=tuple(
                AcceptedFile(
                    path=item["path"],
                    language=item["language"],
                    content=item["content"],
                )
                for item in stored["files"]  # type: ignore[union-attr]
            ),
            plan=[
                TaskPlanStep.model_validate(item)
                for item in stored["plan"]  # type: ignore[union-attr]
            ],
            warnings=list(stored["warnings"]),  # type: ignore[arg-type]
            tool_calls=[
                ToolCallRecord.model_validate(item)
                for item in stored["tool_calls"]  # type: ignore[union-attr]
            ],
            transitions=[
                TaskTransition.model_validate(item)
                for item in stored["transitions"]  # type: ignore[union-attr]
            ],
            final_answer=(
                str(stored["final_answer"]) if stored["final_answer"] is not None else None
            ),
            cancel_requested=bool(stored["cancel_requested"]),
        )

    def _persist(
        self,
        record: AgentTaskRecord,
        *,
        connection: sqlite3.Connection | None = None,
        job: QueueJob | None = None,
    ) -> None:
        def save(active_connection: sqlite3.Connection | None) -> None:
            workflow_store.save_task(
                {
                    "task_id": record.task_id,
                    "project_id": record.project_id,
                    "owner_user_id": record.owner_user_id,
                    "goal": record.goal,
                    "mode": record.mode,
                    "status": record.status,
                    "plan": [item.model_dump(mode="json") for item in record.plan],
                    "warnings": record.warnings,
                    "transitions": [
                        item.model_dump(mode="json") for item in record.transitions
                    ],
                    "final_answer": record.final_answer,
                    "cancel_requested": record.cancel_requested,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                },
                [
                    {"path": item.path, "language": item.language, "content": item.content}
                    for item in record.accepted_files
                ],
                [item.model_dump(mode="json") for item in record.tool_calls],
                connection=active_connection,
            )

        if job is None:
            save(connection)
            return
        if connection is not None:
            record.cancel_requested = task_queue_store.require_claim(connection, job)
            save(connection)
            return
        with database.connect() as claimed_connection:
            claimed_connection.execute("BEGIN IMMEDIATE")
            record.cancel_requested = task_queue_store.require_claim(
                claimed_connection,
                job,
            )
            save(claimed_connection)

    def _transition(
        self,
        record: AgentTaskRecord,
        to_status: AgentTaskStatus,
        reason: str,
    ) -> None:
        previous = record.status
        now = self._now()
        record.status = to_status
        record.updated_at = now
        record.transitions.append(
            TaskTransition(
                from_status=previous,
                to_status=to_status,
                at=now,
                reason=reason,
            ),
        )

    def _initial_transition(self, record: AgentTaskRecord) -> None:
        record.transitions.append(
            TaskTransition(
                from_status=None,
                to_status="created",
                at=record.created_at,
                reason="已创建请求内 Agent 任务快照。",
            ),
        )

    def _evict_if_needed(
        self,
        project_id: str | None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self._sync_cache_generation()
        if (
            workflow_store.count_tasks(project_id, connection=connection)
            < MAX_STORED_TASKS
        ):
            return
        task_id = workflow_store.oldest_terminal_task_id(
            tuple(TERMINAL_STATUSES),
            project_id,
            connection=connection,
        )
        if task_id is None:
            raise AgentTaskError(
                f"当前已有 {MAX_STORED_TASKS} 个活动任务，请先完成或取消旧任务。",
            )
        workflow_store.delete_task(task_id, connection=connection)
        self._tasks.pop(task_id, None)

    def _primary_queries(self, goal: str) -> tuple[str, str | None]:
        identifiers = [
            match.group(0)
            for match in IDENTIFIER_PATTERN.finditer(goal)
            if match.group(0).casefold() not in IDENTIFIER_STOPWORDS
        ]
        symbol_query = identifiers[0] if identifiers else None
        text_query = symbol_query or goal[:120]
        return text_query, symbol_query

    def _build_plan(self, goal: str) -> list[TaskPlanStep]:
        text_query, symbol_query = self._primary_queries(goal)
        tool_steps = (
            (
                "生成项目摘要",
                "统计任务快照中的文件、语言、行数、切片和基础符号。",
                "project_summary",
                {},
            ),
            (
                "检查项目文件",
                "列出已通过安全校验的相对路径和语言，确认分析范围。",
                "list_files",
                {},
            ),
            (
                "搜索相关文本",
                f"使用字面量关键词“{text_query}”定位可能相关的代码行。",
                "search_text",
                {"query": text_query},
            ),
            (
                "搜索基础符号",
                "定位相关函数、类、接口、类型和其他常见声明。",
                "search_symbols",
                {"query": symbol_query} if symbol_query else {},
            ),
            (
                "检查代码切片",
                "读取受限的相关代码片段，并保留文件和行号证据。",
                "inspect_chunks",
                {"query": text_query},
            ),
        )
        plan = [
            TaskPlanStep(
                id=uuid4().hex,
                position=position,
                title=title,
                description=description,
                status="pending",
                tool_name=tool_name,
                arguments=arguments,
                requires_confirmation=True,
            )
            for position, (title, description, tool_name, arguments) in enumerate(
                tool_steps,
                start=1,
            )
        ]
        plan.append(
            TaskPlanStep(
                id=uuid4().hex,
                position=len(plan) + 1,
                title="审阅并汇报",
                description="整理只读工具结果、限制和下一步建议，不修改文件或运行命令。",
                status="pending",
            ),
        )
        return plan

    def _public(self, record: AgentTaskRecord) -> AgentTaskResponse:
        queue_job = task_queue_store.get_for_task(record.task_id)
        effective_status = record.status
        if (
            queue_job is not None
            and record.status in {"failed", "timed_out"}
            and queue_job.attempts < queue_job.max_attempts
            and queue_job.status in {"queued", "running"}
        ):
            effective_status = "queued" if queue_job.status == "queued" else "executing"
        queue_state = (
            TaskQueueState(
                status=queue_job.status,
                attempts=queue_job.attempts,
                max_attempts=queue_job.max_attempts,
                available_at=queue_job.available_at,
                lease_expires_at=queue_job.lease_expires_at,
                heartbeat_at=queue_job.heartbeat_at,
                cancel_requested=queue_job.cancel_requested,
                last_error=queue_job.last_error,
            )
            if queue_job is not None
            else None
        )
        return AgentTaskResponse(
            task_id=record.task_id,
            project_id=record.project_id,
            goal=record.goal,
            mode=record.mode,
            status=effective_status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            file_count=len(record.accepted_files),
            file_paths=[file.path for file in record.accepted_files],
            plan=record.plan,
            tool_calls=record.tool_calls,
            transitions=record.transitions,
            final_answer=record.final_answer,
            warnings=record.warnings,
            queue=queue_state,
            can_confirm=effective_status == "waiting_for_confirmation",
            can_cancel=effective_status in CANCELLABLE_STATUSES
            and not record.cancel_requested,
            can_resume=effective_status in RESUMABLE_STATUSES,
        )

    def _idempotency_key(self, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(cleaned):
            raise AgentTaskInputError(
                "Idempotency-Key 必须为 1 至 128 个字母、数字、点、下划线、冒号或连字符。",
            )
        return cleaned

    def _idempotency_scope(
        self,
        *,
        project_id: str | None,
        owner_user_id: str | None,
    ) -> str:
        if project_id is None:
            return "legacy_local"
        return f"user:{owner_user_id}:project:{project_id}"

    def _request_fingerprint(
        self,
        *,
        goal: str,
        files: tuple[AcceptedFile, ...],
    ) -> str:
        payload = {
            "goal": goal,
            "files": [
                {
                    "path": item.path,
                    "language": item.language,
                    "sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                }
                for item in sorted(files, key=lambda item: item.path)
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def create_task(
        self,
        *,
        goal: str,
        uploads: list[UploadFile],
        settings: Settings,
        project_id: str | None = None,
        owner_user_id: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentTaskResponse:
        """Validate files, build a readable plan, and stop for confirmation."""

        request_key = self._idempotency_key(idempotency_key)
        cleaned_goal = validate_question(goal)
        accepted_files, file_warnings = await process_uploads(uploads, settings)
        accepted_file_tuple = tuple(accepted_files)
        idempotency_scope = self._idempotency_scope(
            project_id=project_id,
            owner_user_id=owner_user_id,
        )
        request_fingerprint = self._request_fingerprint(
            goal=cleaned_goal,
            files=accepted_file_tuple,
        )
        now = self._now()
        task_id = uuid4().hex
        record = AgentTaskRecord(
            task_id=task_id,
            project_id=project_id,
            owner_user_id=owner_user_id,
            goal=cleaned_goal,
            mode="plan",
            status="created",
            created_at=now,
            updated_at=now,
            accepted_files=accepted_file_tuple,
            plan=[],
            warnings=[
                *file_warnings,
                "阶段 4 仅允许只读工具；不会修改文件、运行代码或执行命令。",
                "任务和安全文件快照已保存到本地 SQLite，服务重启后仍可恢复。",
            ],
        )
        self._initial_transition(record)
        self._transition(record, "planning", "开始生成结构化只读执行计划。")
        record.plan = self._build_plan(cleaned_goal)
        self._transition(
            record,
            "waiting_for_confirmation",
            "计划已生成，等待用户确认后执行只读工具。",
        )
        existing_task_id: str | None = None
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if request_key is not None:
                existing = workflow_store.load_idempotency(
                    scope=idempotency_scope,
                    idempotency_key=request_key,
                    connection=connection,
                )
                if existing is not None:
                    existing_fingerprint, existing_task_id = existing
                    if existing_fingerprint != request_fingerprint:
                        raise AgentTaskError(
                            "该 Idempotency-Key 已用于不同的任务请求，请更换 Key。",
                        )
            if existing_task_id is None:
                with usage_service.task_capacity_guard(
                    context=UsageContext(
                        user_id=owner_user_id,
                        project_id=project_id,
                    ),
                    settings=settings,
                    task_id=task_id,
                    file_count=len(accepted_files),
                    request_id=request_id,
                    connection=connection,
                ) as guarded_connection:
                    self._evict_if_needed(
                        project_id,
                        connection=guarded_connection,
                    )
                    self._persist(record, connection=guarded_connection)
                    if request_key is not None:
                        workflow_store.save_idempotency(
                            scope=idempotency_scope,
                            idempotency_key=request_key,
                            request_fingerprint=request_fingerprint,
                            task_id=task_id,
                            created_at=now.isoformat(),
                            connection=guarded_connection,
                        )
        if existing_task_id is not None:
            return self.get_task(existing_task_id)
        self._tasks[record.task_id] = record
        logger.info(
            "agent_task_planned task_id=%s files=%s steps=%s",
            record.task_id,
            len(record.accepted_files),
            len(record.plan),
        )
        return self._public(record)

    def get_task(self, task_id: str) -> AgentTaskResponse:
        """Return one current task snapshot without exposing file content."""

        return self._public(self._get_record(task_id))

    def get_completed_task_record(self, task_id: str) -> AgentTaskRecord:
        """Return private task state only to trusted backend workflow services."""

        record = self._get_record(task_id)
        if record.status != "completed":
            raise AgentTaskError("只有已完成的 Agent 任务可以进入补丁阶段。")
        return record

    def get_task_access(self, task_id: str) -> tuple[str | None, str | None]:
        """Return project and creator identifiers for route authorization."""

        record = self._get_record(task_id)
        return record.project_id, record.owner_user_id

    def list_tasks(
        self,
        *,
        project_ids: set[str] | None = None,
        legacy_only: bool = False,
    ) -> AgentTaskListResponse:
        """Return newest durable tasks within the caller's visible projects."""

        task_ids = workflow_store.list_task_ids(
            project_ids=project_ids,
            legacy_only=legacy_only,
            limit=MAX_TASK_LIST_ITEMS,
        )
        records = [self._get_record(task_id) for task_id in task_ids]
        return AgentTaskListResponse(
            tasks=[self._public(record) for record in records],
            total=len(records),
        )

    def cancel_task(self, task_id: str) -> AgentTaskResponse:
        """Request cancellation and immediately cancel non-running tasks."""

        record = self._get_record(task_id)
        if record.status not in CANCELLABLE_STATUSES:
            raise AgentTaskError(f"状态 {record.status} 的任务不能取消。")
        queue_status = task_queue_store.request_cancel(task_id)
        record = self._get_record(task_id)
        if record.status in TERMINAL_STATUSES:
            return self._public(record)
        if queue_status != "running" and record.status not in {
            "executing",
            "reviewing",
            "validating",
        }:
            self._transition(record, "cancelled", "用户取消了 Agent 任务。")
            for step in record.plan:
                if step.status in {"pending", "in_progress"}:
                    step.status = "skipped"
            record.final_answer = "任务已由用户取消，未执行任何新的工具或文件操作。"
            self._persist(record)
        logger.info("agent_task_cancel_requested task_id=%s", record.task_id)
        return self._public(record)

    def resume_task(
        self,
        task_id: str,
        settings: Settings,
        request_id: str | None = None,
    ) -> AgentTaskResponse:
        """Reset incomplete read-only work and return to confirmation."""

        record = self._get_record(task_id)
        if record.status not in RESUMABLE_STATUSES:
            raise AgentTaskError(f"状态 {record.status} 的任务不能恢复。")
        with usage_service.task_capacity_guard(
            context=UsageContext(
                user_id=record.owner_user_id,
                project_id=record.project_id,
            ),
            settings=settings,
            task_id=record.task_id,
            request_id=request_id,
            exclude_task_id=record.task_id,
        ) as connection:
            record.cancel_requested = False
            record.mode = "plan"
            record.final_answer = None
            for step in record.plan:
                step.status = "pending"
            self._transition(
                record,
                "waiting_for_confirmation",
                "任务已恢复，等待用户再次确认只读执行计划。",
            )
            self._persist(record, connection=connection)
            task_queue_store.delete_for_task(
                record.task_id,
                connection=connection,
            )
        return self._public(record)

    def _mark_cancelled_during_execution(
        self,
        record: AgentTaskRecord,
        *,
        job: QueueJob,
    ) -> None:
        for step in record.plan:
            if step.status in {"in_progress", "pending"}:
                step.status = "skipped"
        self._transition(record, "cancelled", "执行过程中收到用户取消请求。")
        record.final_answer = "任务已在只读工具执行边界停止，未修改文件或运行命令。"
        self._persist(record, job=job)

    def _build_final_answer(self, record: AgentTaskRecord) -> str:
        completed_calls = [call for call in record.tool_calls if call.status == "completed"]
        failed_calls = [call for call in record.tool_calls if call.status != "completed"]
        summaries = [
            f"- {call.title}：{call.result.summary}"
            for call in completed_calls
            if call.result is not None
        ]
        failure_note = (
            f"\n\n有 {len(failed_calls)} 个工具未成功完成，详情见工具轨迹。"
            if failed_calls
            else ""
        )
        return (
            f"已完成任务“{record.goal}”的阶段 4 只读 Agent 工作流。\n\n"
            + "\n".join(summaries)
            + failure_note
            + "\n\n本次仅使用注册的只读工具，没有修改文件、执行用户代码或运行命令。"
        )

    def start_queue(self, settings: Settings) -> None:
        """Start one local dispatcher; SQLite coordinates additional processes."""

        with self._queue_lock:
            self._queue_settings = settings
            if self._dispatcher is not None and self._dispatcher.is_alive():
                self._queue_wake.set()
                return
            self._queue_stop.clear()
            self._queue_wake.clear()
            self._dispatcher = Thread(
                target=self._dispatch_loop,
                name="codexxx-queue-dispatcher",
                daemon=True,
            )
            self._dispatcher.start()

    def stop_queue(self, *, wait: bool) -> None:
        """Stop claiming jobs and optionally wait for already claimed work."""

        with self._queue_lock:
            dispatcher = self._dispatcher
            self._queue_stop.set()
            self._queue_wake.set()
        if dispatcher is not None and dispatcher.is_alive():
            dispatcher.join(timeout=5)
        if wait:
            for _job, future in list(self._active_jobs.values()):
                try:
                    future.result(timeout=30)
                except Exception:
                    logger.exception("agent_queue_worker_stop_failed")
        with self._queue_lock:
            self._dispatcher = None

    def _dispatch_loop(self) -> None:
        last_heartbeat = 0.0
        while not self._queue_stop.is_set():
            settings = self._queue_settings
            if settings is None:
                return
            self._reap_queue_workers()
            now = time.monotonic()
            heartbeat_interval = min(
                settings.task_queue_heartbeat_seconds,
                max(0.1, settings.task_queue_lease_seconds / 3),
            )
            if now - last_heartbeat >= heartbeat_interval:
                self._heartbeat_queue_workers(settings)
                last_heartbeat = now
            self._recover_expired_jobs()
            self._claim_available_jobs(settings)
            self._queue_wake.wait(settings.task_queue_poll_seconds)
            self._queue_wake.clear()

    def _reap_queue_workers(self) -> None:
        for job_id, (_job, future) in list(self._active_jobs.items()):
            if not future.done():
                continue
            self._active_jobs.pop(job_id, None)
            try:
                future.result()
            except Exception:
                logger.exception("agent_queue_worker_failed job_id=%s", job_id)

    def _heartbeat_queue_workers(self, settings: Settings) -> None:
        for job, future in list(self._active_jobs.values()):
            if future.done():
                continue
            if not task_queue_store.heartbeat(
                job,
                lease_seconds=settings.task_queue_lease_seconds,
            ):
                logger.warning("agent_queue_lease_lost job_id=%s", job.job_id)

    def _claim_available_jobs(self, settings: Settings) -> None:
        capacity = settings.task_queue_worker_concurrency - len(self._active_jobs)
        for _ in range(max(0, capacity)):
            job = task_queue_store.claim_next(
                worker_id=self._worker_id,
                lease_seconds=settings.task_queue_lease_seconds,
            )
            if job is None:
                return
            future = self._executor.submit(self._run_claimed_job, job, settings)
            self._active_jobs[job.job_id] = (job, future)

    def _mark_interrupted_work(self, record: AgentTaskRecord, reason: str) -> None:
        now = self._now()
        for call in record.tool_calls:
            if call.status in {"pending", "running"}:
                call.status = "failed"
                call.finished_at = now
                call.error = reason
        for step in record.plan:
            if step.status == "in_progress":
                step.status = "failed"

    def _prepare_retry(self, record: AgentTaskRecord, reason: str) -> None:
        self._mark_interrupted_work(record, reason)
        for step in record.plan:
            if step.status == "failed":
                step.status = "pending"
        record.cancel_requested = False
        record.final_answer = "上一次 Worker 尝试未完成，任务已安全重新入队。"
        self._transition(record, "queued", reason)
        self._persist(record)

    def _recover_expired_jobs(self) -> None:
        for recovered in task_queue_store.recover_expired():
            self._apply_queue_recovery(recovered)

    def _apply_queue_recovery(self, recovered: RecoveredJob) -> None:
        if recovered.action == "complete":
            return
        try:
            record = self._get_record(recovered.task_id)
        except AgentTaskNotFoundError:
            return
        self._mark_interrupted_work(record, recovered.reason)
        if recovered.action == "retry":
            self._prepare_retry(record, recovered.reason)
        elif recovered.action == "cancel":
            for step in record.plan:
                if step.status in {"pending", "in_progress", "failed"}:
                    step.status = "skipped"
            record.cancel_requested = True
            self._transition(record, "cancelled", recovered.reason)
            record.final_answer = "任务已取消；过期 Worker 不会继续写入结果。"
            self._persist(record)
        else:
            self._transition(record, "failed", recovered.reason)
            record.final_answer = "任务的 Worker 多次失去租约，已达到最大重试次数。"
            self._persist(record)

    def confirm_task(
        self,
        task_id: str,
        settings: Settings,
        idempotency_key: str | None = None,
    ) -> AgentTaskResponse:
        """Atomically enqueue a confirmed read-only plan."""

        request_key = self._idempotency_key(idempotency_key)
        idempotent_task_id: str | None = None
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self._get_record(task_id, connection=connection)
            existing_job = task_queue_store.get_for_task(
                task_id,
                connection=connection,
            )
            if record.status != "waiting_for_confirmation":
                if (
                    request_key is not None
                    and existing_job is not None
                    and existing_job.idempotency_key == request_key
                ):
                    idempotent_task_id = task_id
                else:
                    raise AgentTaskError(
                        f"状态 {record.status} 的任务不能确认执行。",
                    )
            else:
                record.cancel_requested = False
                record.mode = "execute"
                self._transition(record, "queued", "用户确认计划，任务已进入可靠队列。")
                self._persist(record, connection=connection)
                task_queue_store.enqueue(
                    task_id,
                    max_attempts=settings.task_queue_max_attempts,
                    idempotency_key=request_key,
                    connection=connection,
                )
        self.start_queue(settings)
        self._queue_wake.set()
        return self.get_task(idempotent_task_id or task_id)

    def _job_cancel_requested(self, job: QueueJob) -> bool:
        with database.connect() as connection:
            return task_queue_store.require_claim(connection, job)

    def _run_claimed_job(
        self,
        job: QueueJob,
        settings: Settings,
    ) -> AgentTaskResponse:
        """Run one fenced queue claim and settle it without duplicate writes."""

        try:
            record = self._get_record(job.task_id)
            if record.status == "completed":
                task_queue_store.finish(job, status="completed")
                return self._public(record)
            if record.status != "queued":
                raise AgentTaskError(
                    f"队列领取到状态 {record.status} 的任务，已拒绝执行。",
                )
            if self._job_cancel_requested(job):
                self._mark_cancelled_during_execution(record, job=job)
                task_queue_store.finish(job, status="cancelled")
                return self._public(record)
            self._transition(
                record,
                "executing",
                f"Worker 已领取任务，开始第 {job.attempts} 次执行。",
            )
            self._persist(record, job=job)
            if record.cancel_requested:
                self._mark_cancelled_during_execution(record, job=job)
                task_queue_store.finish(job, status="cancelled")
                return self._public(record)
            response = asyncio.run(self._execute_task(job))
        except QueueLeaseLostError:
            logger.warning("agent_queue_stale_worker_stopped job_id=%s", job.job_id)
            return self.get_task(job.task_id)
        except Exception as error:
            logger.exception("agent_queue_execution_failed job_id=%s", job.job_id)
            try:
                record = self._get_record(job.task_id)
                if record.status not in TERMINAL_STATUSES:
                    self._transition(record, "failed", "队列 Worker 发生未预期错误。")
                record.final_answer = "队列 Worker 执行失败，未修改文件或运行命令。"
                self._persist(record, job=job)
                response = self._public(record)
            except QueueLeaseLostError:
                return self.get_task(job.task_id)
            error_message = str(error) or "队列 Worker 执行失败。"
            return self._settle_failed_attempt(job, response, error_message, settings)

        persisted_status = self._get_record(job.task_id).status
        if persisted_status == "completed":
            task_queue_store.finish(job, status="completed")
            return response
        if persisted_status == "cancelled":
            task_queue_store.finish(job, status="cancelled")
            return response
        if persisted_status in {"failed", "timed_out"}:
            error_message = response.final_answer or "只读任务执行失败。"
            return self._settle_failed_attempt(job, response, error_message, settings)
        task_queue_store.finish(
            job,
            status="failed",
            error=response.final_answer or f"任务停留在状态 {persisted_status}。",
        )
        return response

    def _settle_failed_attempt(
        self,
        job: QueueJob,
        response: AgentTaskResponse,
        error: str,
        settings: Settings,
    ) -> AgentTaskResponse:
        delay = settings.task_queue_retry_base_seconds * (2 ** max(0, job.attempts - 1))
        if task_queue_store.retry(job, error=error, delay_seconds=delay):
            record = self._get_record(job.task_id)
            self._prepare_retry(
                record,
                f"第 {job.attempts} 次执行失败，已安排自动重试。",
            )
            self._queue_wake.set()
            return self._public(record)
        current = task_queue_store.get_for_task(job.task_id)
        if current is not None and current.cancel_requested:
            record = self._get_record(job.task_id)
            if record.status != "cancelled":
                self._mark_cancelled_during_execution(record, job=job)
            task_queue_store.finish(job, status="cancelled")
            return self._public(record)
        task_queue_store.finish(job, status="failed", error=error)
        return response

    async def _execute_task(self, job: QueueJob) -> AgentTaskResponse:
        """Execute a confirmed task and retain its complete state trace."""

        record = self._get_record(job.task_id)
        context = ToolContext(files=record.accepted_files)

        try:
            for step in record.plan:
                if step.status == "completed":
                    continue
                if record.cancel_requested or self._job_cancel_requested(job):
                    record.cancel_requested = True
                    self._mark_cancelled_during_execution(record, job=job)
                    return self._public(record)
                if step.tool_name is None:
                    continue

                definition = TOOL_REGISTRY[step.tool_name]
                step.status = "in_progress"
                call = ToolCallRecord(
                    id=uuid4().hex,
                    tool_name=step.tool_name,
                    title=definition.spec.title,
                    status="running",
                    arguments=step.arguments,
                    started_at=self._now(),
                )
                record.tool_calls.append(call)
                self._persist(record, job=job)
                if record.cancel_requested:
                    call.status = "cancelled"
                    call.finished_at = self._now()
                    step.status = "skipped"
                    self._mark_cancelled_during_execution(record, job=job)
                    return self._public(record)
                started = time.perf_counter()

                try:
                    call.result = await execute_registered_tool(
                        name=step.tool_name,
                        arguments=step.arguments,
                        context=context,
                    )
                    call.status = "completed"
                    step.status = "completed"
                except TimeoutError:
                    call.status = "timed_out"
                    call.error = f"工具 {step.tool_name} 执行超时。"
                    step.status = "failed"
                    record.final_answer = "任务在只读工具阶段超时，详情见工具轨迹。"
                    self._transition(record, "timed_out", call.error)
                except AnalysisInputError as error:
                    call.status = "failed"
                    call.error = error.detail
                    step.status = "failed"
                    record.final_answer = "任务在只读工具阶段失败，详情见工具轨迹。"
                    self._transition(record, "failed", call.error)
                    logger.exception(
                        "agent_tool_failed task_id=%s tool=%s",
                        record.task_id,
                        step.tool_name,
                    )
                except Exception:
                    call.status = "failed"
                    call.error = "只读工具执行失败。"
                    step.status = "failed"
                    record.final_answer = "任务在只读工具阶段失败，详情见工具轨迹。"
                    self._transition(record, "failed", call.error)
                    logger.exception(
                        "agent_tool_failed task_id=%s tool=%s",
                        record.task_id,
                        step.tool_name,
                    )
                finally:
                    call.finished_at = self._now()
                    call.duration_ms = round((time.perf_counter() - started) * 1000, 3)
                    self._persist(record, job=job)

                logger.info(
                    "agent_tool_completed task_id=%s tool=%s status=%s duration_ms=%.3f",
                    record.task_id,
                    step.tool_name,
                    call.status,
                    call.duration_ms,
                )
                if record.status in {"failed", "timed_out"}:
                    return self._public(record)
                if record.cancel_requested:
                    self._mark_cancelled_during_execution(record, job=job)
                    return self._public(record)
                await asyncio.sleep(0)

            if record.cancel_requested or self._job_cancel_requested(job):
                record.cancel_requested = True
                self._mark_cancelled_during_execution(record, job=job)
                return self._public(record)

            self._transition(record, "reviewing", "只读工具已完成，开始整理结果。")
            review_step = record.plan[-1]
            review_step.status = "in_progress"
            record.final_answer = self._build_final_answer(record)
            review_step.status = "completed"
            self._persist(record, job=job)
            if record.cancel_requested:
                self._mark_cancelled_during_execution(record, job=job)
                return self._public(record)
            self._transition(
                record,
                "validating",
                "检查工具均来自只读注册表，且输出满足大小限制。",
            )
            invalid_calls = [
                call for call in record.tool_calls if call.tool_name not in TOOL_REGISTRY
            ]
            if invalid_calls:
                self._transition(record, "blocked", "检测到未注册工具，任务已阻止。")
                record.final_answer = "检测到未注册工具，任务已安全阻止。"
                self._persist(record, job=job)
                return self._public(record)
            if self._job_cancel_requested(job):
                record.cancel_requested = True
                self._mark_cancelled_during_execution(record, job=job)
                return self._public(record)
            self._transition(record, "completed", "计划、只读执行、审阅和安全校验均已完成。")
            self._persist(record, job=job)
            if record.cancel_requested:
                self._mark_cancelled_during_execution(record, job=job)
            return self._public(record)
        except QueueLeaseLostError:
            raise
        except Exception:
            if record.status not in TERMINAL_STATUSES:
                self._transition(record, "failed", "Agent 工作流发生未预期错误。")
            record.final_answer = "Agent 工作流失败，未修改文件或运行命令。"
            self._persist(record, job=job)
            logger.exception("agent_task_failed task_id=%s", record.task_id)
            return self._public(record)

    def replace_workspace_contents(
        self,
        task_id: str,
        workspace: dict[str, str],
    ) -> None:
        """Persist a patch-applied workspace without changing its safe file set."""

        record = self._get_record(task_id)
        record.accepted_files = tuple(
            AcceptedFile(path=item.path, language=item.language, content=workspace[item.path])
            for item in record.accepted_files
        )
        record.updated_at = self._now()
        self._persist(record)


agent_task_service = AgentTaskService()
