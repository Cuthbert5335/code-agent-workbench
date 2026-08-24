"""Configurable deletion policy for expired and terminal durable data."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread, current_thread

from app.config import Settings, settings
from app.schemas.security import RetentionPolicyResponse
from app.storage import database

logger = logging.getLogger("uvicorn.error")

TERMINAL_TASK_STATUSES = ("completed", "cancelled", "failed", "timed_out")


@dataclass(frozen=True)
class RetentionCleanupResult:
    expired_sessions: int
    login_attempts: int
    terminal_tasks: int
    audit_logs: int
    usage_records: int


def discard_workflow_caches(task_ids: set[str]) -> None:
    """Drop deleted file and patch snapshots from process-local caches."""

    if not task_ids:
        return
    from app.services.agent_tasks import agent_task_service
    from app.services.patches import patch_service

    agent_task_service.discard_cached_tasks(task_ids)
    patch_service.discard_cached_tasks(task_ids)


class RetentionService:
    """Periodically remove data after its documented retention period."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None

    def policy(self, app_settings: Settings = settings) -> RetentionPolicyResponse:
        task_days = app_settings.terminal_task_retention_days
        return RetentionPolicyResponse(
            expired_sessions_days=app_settings.expired_session_retention_days,
            login_attempts_days=app_settings.login_attempt_retention_days,
            terminal_tasks_days=task_days,
            patches_days=task_days,
            validations_days=task_days,
            audit_logs_days=app_settings.audit_log_retention_days,
            usage_records_days=app_settings.usage_record_retention_days,
            cleanup_interval_seconds=app_settings.retention_cleanup_interval_seconds,
        )

    def cleanup(
        self,
        app_settings: Settings = settings,
        *,
        now: datetime | None = None,
    ) -> RetentionCleanupResult:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        session_cutoff = current - timedelta(
            days=app_settings.expired_session_retention_days,
        )
        login_cutoff = current - timedelta(
            days=app_settings.login_attempt_retention_days,
        )
        task_cutoff = current - timedelta(
            days=app_settings.terminal_task_retention_days,
        )
        audit_cutoff = current - timedelta(days=app_settings.audit_log_retention_days)
        usage_retention = max(
            timedelta(days=app_settings.usage_record_retention_days),
            timedelta(seconds=app_settings.usage_window_seconds),
        )
        usage_cutoff = current - usage_retention
        placeholders = ", ".join("?" for _status in TERMINAL_TASK_STATUSES)

        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task_rows = connection.execute(
                f"""
                SELECT task_id FROM agent_tasks
                WHERE status IN ({placeholders}) AND updated_at < ?
                """,
                (*TERMINAL_TASK_STATUSES, task_cutoff.isoformat()),
            ).fetchall()
            task_ids = {str(row["task_id"]) for row in task_rows}
            expired_sessions = connection.execute(
                """
                DELETE FROM sessions
                WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)
                """,
                (session_cutoff.isoformat(), session_cutoff.isoformat()),
            ).rowcount
            login_attempts = connection.execute(
                "DELETE FROM login_attempts WHERE attempted_at < ?",
                (login_cutoff.isoformat(),),
            ).rowcount
            terminal_tasks = connection.execute(
                f"""
                DELETE FROM agent_tasks
                WHERE status IN ({placeholders}) AND updated_at < ?
                """,
                (*TERMINAL_TASK_STATUSES, task_cutoff.isoformat()),
            ).rowcount
            audit_logs = connection.execute(
                "DELETE FROM audit_logs WHERE created_at < ?",
                (audit_cutoff.isoformat(),),
            ).rowcount
            usage_records = connection.execute(
                "DELETE FROM usage_records WHERE occurred_at < ?",
                (usage_cutoff.isoformat(),),
            ).rowcount

        discard_workflow_caches(task_ids)
        return RetentionCleanupResult(
            expired_sessions=expired_sessions,
            login_attempts=login_attempts,
            terminal_tasks=terminal_tasks,
            audit_logs=audit_logs,
            usage_records=usage_records,
        )

    def start(self, app_settings: Settings = settings) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(
                target=self._run,
                args=(app_settings,),
                name="codexxx-retention-cleanup",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, wait: bool) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
        if wait and thread is not None and thread is not current_thread():
            thread.join(timeout=5)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _run(self, app_settings: Settings) -> None:
        while not self._stop.is_set():
            try:
                result = self.cleanup(app_settings)
                logger.info("retention_cleanup_completed result=%s", result)
            except Exception:
                logger.exception("retention_cleanup_failed")
            if self._stop.wait(app_settings.retention_cleanup_interval_seconds):
                return


retention_service = RetentionService()
