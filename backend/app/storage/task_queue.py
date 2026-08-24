"""SQLite-backed task queue with leases, heartbeats, retries, and cancellation."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from app.storage import database

QueueStatus = Literal["queued", "running", "completed", "cancelled", "failed"]
RecoveryAction = Literal["retry", "cancel", "fail", "complete"]


@dataclass(frozen=True)
class QueueJob:
    job_id: str
    task_id: str
    status: QueueStatus
    attempts: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    idempotency_key: str | None
    cancel_requested: bool
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class RecoveredJob:
    task_id: str
    action: RecoveryAction
    reason: str


class QueueLeaseLostError(RuntimeError):
    """The caller no longer owns the claimed queue job."""


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _job(row: sqlite3.Row) -> QueueJob:
    return QueueJob(
        job_id=str(row["job_id"]),
        task_id=str(row["task_id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        available_at=datetime.fromisoformat(str(row["available_at"])),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] else None,
        lease_token=str(row["lease_token"]) if row["lease_token"] else None,
        lease_expires_at=_datetime(row["lease_expires_at"]),
        heartbeat_at=_datetime(row["heartbeat_at"]),
        idempotency_key=(
            str(row["idempotency_key"]) if row["idempotency_key"] else None
        ),
        cancel_requested=bool(row["cancel_requested"]),
        last_error=str(row["last_error"]) if row["last_error"] else None,
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        completed_at=_datetime(row["completed_at"]),
    )


class TaskQueueStore:
    """Coordinate queue ownership across processes through SQLite transactions."""

    def enqueue(
        self,
        task_id: str,
        *,
        max_attempts: int,
        idempotency_key: str | None,
        connection: sqlite3.Connection,
    ) -> QueueJob:
        now = datetime.now(UTC).isoformat()
        job_id = uuid4().hex
        connection.execute("DELETE FROM task_queue WHERE task_id = ?", (task_id,))
        connection.execute(
            """
            INSERT INTO task_queue (
                job_id, task_id, status, attempts, max_attempts, available_at,
                idempotency_key, cancel_requested, created_at, updated_at
            ) VALUES (?, ?, 'queued', 0, ?, ?, ?, 0, ?, ?)
            """,
            (job_id, task_id, max_attempts, now, idempotency_key, now, now),
        )
        row = connection.execute(
            "SELECT * FROM task_queue WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return _job(row)

    def get_for_task(
        self,
        task_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> QueueJob | None:
        if connection is not None:
            row = connection.execute(
                "SELECT * FROM task_queue WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return _job(row) if row else None
        with database.connect() as active_connection:
            row = active_connection.execute(
                "SELECT * FROM task_queue WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return _job(row) if row else None

    def delete_for_task(
        self,
        task_id: str,
        *,
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute("DELETE FROM task_queue WHERE task_id = ?", (task_id,))

    def claim_next(self, *, worker_id: str, lease_seconds: float) -> QueueJob | None:
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        lease_token = uuid4().hex
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT queue.* FROM task_queue AS queue
                JOIN agent_tasks AS task ON task.task_id = queue.task_id
                WHERE queue.status = 'queued'
                    AND queue.available_at <= ?
                    AND queue.cancel_requested = 0
                    AND task.cancel_requested = 0
                    AND task.status = 'queued'
                ORDER BY queue.available_at ASC, queue.created_at ASC
                LIMIT 1
                """,
                (now.isoformat(),),
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """
                UPDATE task_queue SET
                    status = 'running',
                    attempts = attempts + 1,
                    lease_owner = ?,
                    lease_token = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (
                    worker_id,
                    lease_token,
                    lease_expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    row["job_id"],
                ),
            ).rowcount
            if changed != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM task_queue WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
        return _job(claimed)

    def heartbeat(self, job: QueueJob, *, lease_seconds: float) -> bool:
        if job.lease_token is None:
            return False
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=lease_seconds)
        with database.connect() as connection:
            changed = connection.execute(
                """
                UPDATE task_queue SET
                    heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_token = ?
                """,
                (
                    now.isoformat(),
                    expires_at.isoformat(),
                    now.isoformat(),
                    job.job_id,
                    job.lease_token,
                ),
            ).rowcount
        return changed == 1

    def require_claim(self, connection: sqlite3.Connection, job: QueueJob) -> bool:
        if job.lease_token is None:
            raise QueueLeaseLostError("队列任务缺少租约令牌。")
        row = connection.execute(
            """
            SELECT cancel_requested FROM task_queue
            WHERE job_id = ? AND status = 'running' AND lease_token = ?
            """,
            (job.job_id, job.lease_token),
        ).fetchone()
        if row is None:
            raise QueueLeaseLostError("队列任务租约已失效。")
        return bool(row["cancel_requested"])

    def request_cancel(self, task_id: str) -> QueueStatus | None:
        now = datetime.now(UTC).isoformat()
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE agent_tasks SET cancel_requested = 1, updated_at = ?
                WHERE task_id = ?
                """,
                (now, task_id),
            )
            row = connection.execute(
                "SELECT status FROM task_queue WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            status = str(row["status"])
            if status == "queued":
                connection.execute(
                    """
                    UPDATE task_queue SET
                        status = 'cancelled', cancel_requested = 1,
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        updated_at = ?, completed_at = ?
                    WHERE task_id = ? AND status = 'queued'
                    """,
                    (now, now, task_id),
                )
                return "cancelled"
            if status == "running":
                connection.execute(
                    """
                    UPDATE task_queue SET cancel_requested = 1, updated_at = ?
                    WHERE task_id = ? AND status = 'running'
                    """,
                    (now, task_id),
                )
            return status  # type: ignore[return-value]

    def finish(
        self,
        job: QueueJob,
        *,
        status: Literal["completed", "cancelled", "failed"],
        error: str | None = None,
    ) -> bool:
        if job.lease_token is None:
            return False
        now = datetime.now(UTC).isoformat()
        with database.connect() as connection:
            changed = connection.execute(
                """
                UPDATE task_queue SET
                    status = ?, last_error = ?, lease_owner = NULL,
                    lease_token = NULL, lease_expires_at = NULL,
                    heartbeat_at = NULL, updated_at = ?, completed_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_token = ?
                """,
                (status, error, now, now, job.job_id, job.lease_token),
            ).rowcount
        return changed == 1

    def retry(
        self,
        job: QueueJob,
        *,
        error: str,
        delay_seconds: float,
    ) -> bool:
        if job.lease_token is None or job.attempts >= job.max_attempts:
            return False
        now = datetime.now(UTC)
        available_at = now + timedelta(seconds=delay_seconds)
        with database.connect() as connection:
            changed = connection.execute(
                """
                UPDATE task_queue SET
                    status = 'queued', available_at = ?, last_error = ?,
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_token = ?
                    AND cancel_requested = 0
                """,
                (
                    available_at.isoformat(),
                    error,
                    now.isoformat(),
                    job.job_id,
                    job.lease_token,
                ),
            ).rowcount
        return changed == 1

    def recover_expired(self) -> list[RecoveredJob]:
        now = datetime.now(UTC)
        recovered: list[RecoveredJob] = []
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT queue.*, task.cancel_requested AS task_cancel_requested,
                    task.status AS task_status
                FROM task_queue AS queue
                JOIN agent_tasks AS task ON task.task_id = queue.task_id
                WHERE queue.status = 'running' AND queue.lease_expires_at <= ?
                ORDER BY queue.lease_expires_at ASC
                """,
                (now.isoformat(),),
            ).fetchall()
            for row in rows:
                task_status = str(row["task_status"])
                cancelled = bool(row["cancel_requested"]) or bool(
                    row["task_cancel_requested"],
                )
                if task_status == "completed":
                    status = "completed"
                    action = "complete"
                    reason = "任务结果已持久化，过期租约已按完成状态收敛。"
                elif cancelled or task_status == "cancelled":
                    status: QueueStatus = "cancelled"
                    action: RecoveryAction = "cancel"
                    reason = "任务取消请求在 Worker 租约过期后生效。"
                elif int(row["attempts"]) >= int(row["max_attempts"]):
                    status = "failed"
                    action = "fail"
                    reason = "Worker 租约过期且已达到最大重试次数。"
                else:
                    status = "queued"
                    action = "retry"
                    reason = "Worker 租约过期，任务已重新入队。"
                connection.execute(
                    """
                    UPDATE task_queue SET
                        status = ?, available_at = ?, last_error = ?,
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        updated_at = ?, completed_at = ?
                    WHERE job_id = ? AND status = 'running'
                    """,
                    (
                        status,
                        now.isoformat(),
                        reason,
                        now.isoformat(),
                        now.isoformat()
                        if status in {"completed", "cancelled", "failed"}
                        else None,
                        row["job_id"],
                    ),
                )
                recovered.append(
                    RecoveredJob(task_id=str(row["task_id"]), action=action, reason=reason),
                )
        return recovered


task_queue_store = TaskQueueStore()
