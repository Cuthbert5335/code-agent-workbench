"""Durable rolling-window usage accounting and active-task limits."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from app.config import Settings
from app.schemas.usage import (
    UsageMetric,
    UsageResource,
    UsageScopeKind,
    UsageScopeSummary,
    UsageSummaryResponse,
)
from app.storage import database

ACTIVE_TASK_STATUSES = (
    "created",
    "planning",
    "waiting_for_confirmation",
    "queued",
    "executing",
    "reviewing",
    "validating",
)
RESOURCE_LABELS: dict[UsageResource, str] = {
    "model_calls": "模型调用",
    "files": "文件处理",
    "patches": "补丁创建",
    "validations": "验证运行",
}
ActiveScope = Literal["user", "project", "legacy_local"]


@dataclass(frozen=True)
class UsageContext:
    """Identify the user and project charged for one operation."""

    user_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.project_id is not None and self.user_id is None:
            raise ValueError("项目用量必须关联用户。")


@dataclass(frozen=True)
class _Scope:
    kind: UsageScopeKind
    scope_id: str | None
    limit: int


class UsageLimitError(Exception):
    """A persistent quota or active-task limit rejected an operation."""

    status_code = 429

    def __init__(
        self,
        *,
        message: str,
        resource: str,
        scope: ActiveScope,
        limit: int,
        used: int,
        requested: int,
        retry_after_seconds: int,
        retry_at: datetime,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.resource = resource
        self.scope = scope
        self.limit = limit
        self.used = used
        self.requested = requested
        self.retry_after_seconds = max(1, retry_after_seconds)
        self.retry_at = retry_at

    def detail(self) -> dict[str, str | int]:
        return {
            "code": "usage_limit_exceeded",
            "message": self.message,
            "resource": self.resource,
            "scope": self.scope,
            "limit": self.limit,
            "used": self.used,
            "requested": self.requested,
            "retry_after_seconds": self.retry_after_seconds,
            "retry_at": self.retry_at.isoformat(),
        }

    def headers(self) -> dict[str, str]:
        return {
            "Retry-After": str(self.retry_after_seconds),
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(math.ceil(self.retry_at.timestamp())),
            "X-RateLimit-Scope": self.scope,
        }


class UsageService:
    """Check and record limits in SQLite so restarts do not reset quotas."""

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _limits(
        self,
        resource: UsageResource,
        settings: Settings,
    ) -> tuple[int, int]:
        return {
            "model_calls": (
                settings.max_model_calls_per_user_window,
                settings.max_model_calls_per_project_window,
            ),
            "files": (
                settings.max_files_per_user_window,
                settings.max_files_per_project_window,
            ),
            "patches": (
                settings.max_patches_per_user_window,
                settings.max_patches_per_project_window,
            ),
            "validations": (
                settings.max_validations_per_user_window,
                settings.max_validations_per_project_window,
            ),
        }[resource]

    def _scopes(
        self,
        context: UsageContext,
        resource: UsageResource,
        settings: Settings,
    ) -> tuple[_Scope, ...]:
        user_limit, project_limit = self._limits(resource, settings)
        if context.user_id is None:
            return (_Scope("legacy_local", None, user_limit),)
        scopes = [_Scope("user", context.user_id, user_limit)]
        if context.project_id is not None:
            scopes.append(_Scope("project", context.project_id, project_limit))
        return tuple(scopes)

    def _scope_filter(self, scope: _Scope) -> tuple[str, tuple[object, ...]]:
        if scope.kind == "user":
            return "user_id = ?", (scope.scope_id,)
        if scope.kind == "project":
            return "project_id = ?", (scope.scope_id,)
        return "user_id IS NULL AND project_id IS NULL", ()

    def _window_rows(
        self,
        connection: sqlite3.Connection,
        *,
        scope: _Scope,
        resource: UsageResource,
        cutoff: datetime,
    ) -> list[sqlite3.Row]:
        where, parameters = self._scope_filter(scope)
        return connection.execute(
            f"""
            SELECT quantity, occurred_at FROM usage_records
            WHERE {where} AND resource = ? AND occurred_at > ?
            ORDER BY occurred_at ASC, usage_id ASC
            """,
            (*parameters, resource, cutoff.isoformat()),
        ).fetchall()

    def _retry_at(
        self,
        rows: list[sqlite3.Row],
        *,
        excess: int,
        now: datetime,
        window_seconds: int,
    ) -> datetime:
        released = 0
        for row in rows:
            released += int(row["quantity"])
            if released >= excess:
                occurred_at = datetime.fromisoformat(str(row["occurred_at"]))
                return occurred_at + timedelta(seconds=window_seconds)
        return now + timedelta(seconds=window_seconds)

    def _check_resource(
        self,
        connection: sqlite3.Connection,
        *,
        context: UsageContext,
        resource: UsageResource,
        quantity: int,
        settings: Settings,
        now: datetime,
    ) -> None:
        cutoff = now - timedelta(seconds=settings.usage_window_seconds)
        for scope in self._scopes(context, resource, settings):
            rows = self._window_rows(
                connection,
                scope=scope,
                resource=resource,
                cutoff=cutoff,
            )
            used = sum(int(row["quantity"]) for row in rows)
            if used + quantity <= scope.limit:
                continue
            retry_at = self._retry_at(
                rows,
                excess=used + quantity - scope.limit,
                now=now,
                window_seconds=settings.usage_window_seconds,
            )
            retry_after = max(1, math.ceil((retry_at - now).total_seconds()))
            scope_label = {
                "user": "当前账号",
                "project": "当前项目",
                "legacy_local": "本地匿名模式",
            }[scope.kind]
            raise UsageLimitError(
                message=(
                    f"{scope_label}的{RESOURCE_LABELS[resource]}已达到滚动窗口上限 "
                    f"{scope.limit}；可在 {retry_at.isoformat()} 后重试。"
                ),
                resource=resource,
                scope=scope.kind,
                limit=scope.limit,
                used=used,
                requested=quantity,
                retry_after_seconds=retry_after,
                retry_at=retry_at,
            )

    def _record_many(
        self,
        connection: sqlite3.Connection,
        *,
        context: UsageContext,
        usages: Mapping[UsageResource, int],
        resource_id: str | None,
        request_id: str | None,
        now: datetime,
    ) -> None:
        connection.executemany(
            """
            INSERT INTO usage_records (
                usage_id, user_id, project_id, resource, quantity,
                resource_id, request_id, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    uuid4().hex,
                    context.user_id,
                    context.project_id,
                    resource,
                    quantity,
                    resource_id,
                    request_id,
                    now.isoformat(),
                )
                for resource, quantity in usages.items()
                if quantity > 0
            ],
        )

    def _check_many(
        self,
        connection: sqlite3.Connection,
        *,
        context: UsageContext,
        usages: Mapping[UsageResource, int],
        settings: Settings,
        now: datetime,
    ) -> None:
        for resource, quantity in usages.items():
            if quantity < 1:
                raise ValueError("用量数量必须大于零。")
            self._check_resource(
                connection,
                context=context,
                resource=resource,
                quantity=quantity,
                settings=settings,
                now=now,
            )

    def consume_many(
        self,
        *,
        context: UsageContext,
        usages: Mapping[UsageResource, int],
        settings: Settings,
        resource_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Atomically check every applicable scope and append usage rows."""

        if not usages:
            return
        now = self._now()
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_many(
                connection,
                context=context,
                usages=usages,
                settings=settings,
                now=now,
            )
            self._record_many(
                connection,
                context=context,
                usages=usages,
                resource_id=resource_id,
                request_id=request_id,
                now=now,
            )

    def ensure_available(
        self,
        *,
        context: UsageContext,
        usages: Mapping[UsageResource, int],
        settings: Settings,
    ) -> None:
        """Check capacity without charging it, for preflight before expensive work."""

        if not usages:
            return
        with database.connect() as connection:
            self._check_many(
                connection,
                context=context,
                usages=usages,
                settings=settings,
                now=self._now(),
            )

    def _active_scopes(
        self,
        context: UsageContext,
        settings: Settings,
    ) -> tuple[_Scope, ...]:
        if context.user_id is None:
            return (
                _Scope(
                    "legacy_local",
                    None,
                    settings.max_active_tasks_per_user,
                ),
            )
        scopes = [
            _Scope("user", context.user_id, settings.max_active_tasks_per_user),
        ]
        if context.project_id is not None:
            scopes.append(
                _Scope(
                    "project",
                    context.project_id,
                    settings.max_active_tasks_per_project,
                ),
            )
        return tuple(scopes)

    def _active_count(
        self,
        connection: sqlite3.Connection,
        scope: _Scope,
        *,
        exclude_task_id: str | None = None,
    ) -> int:
        placeholders = ",".join("?" for _ in ACTIVE_TASK_STATUSES)
        parameters: list[object] = list(ACTIVE_TASK_STATUSES)
        if scope.kind == "user":
            where = "owner_user_id = ?"
            parameters.append(scope.scope_id)
        elif scope.kind == "project":
            where = "project_id = ?"
            parameters.append(scope.scope_id)
        else:
            where = "owner_user_id IS NULL AND project_id IS NULL"
        if exclude_task_id is not None:
            where += " AND task_id <> ?"
            parameters.append(exclude_task_id)
        return int(
            connection.execute(
                f"""
                SELECT COUNT(*) FROM agent_tasks
                WHERE status IN ({placeholders}) AND {where}
                """,
                parameters,
            ).fetchone()[0],
        )

    def _check_active_tasks(
        self,
        connection: sqlite3.Connection,
        *,
        context: UsageContext,
        settings: Settings,
        now: datetime,
        exclude_task_id: str | None = None,
    ) -> None:
        for scope in self._active_scopes(context, settings):
            active = self._active_count(
                connection,
                scope,
                exclude_task_id=exclude_task_id,
            )
            if active < scope.limit:
                continue
            retry_after = min(30, settings.usage_window_seconds)
            retry_at = now + timedelta(seconds=retry_after)
            scope_label = {
                "user": "当前账号",
                "project": "当前项目",
                "legacy_local": "本地匿名模式",
            }[scope.kind]
            raise UsageLimitError(
                message=(
                    f"{scope_label}已有 {active} 个活动任务，达到上限 {scope.limit}；"
                    "请先完成或取消旧任务，再重试。"
                ),
                resource="active_tasks",
                scope=scope.kind,
                limit=scope.limit,
                used=active,
                requested=1,
                retry_after_seconds=retry_after,
                retry_at=retry_at,
            )

    @contextmanager
    def task_capacity_guard(
        self,
        *,
        context: UsageContext,
        settings: Settings,
        task_id: str,
        file_count: int = 0,
        request_id: str | None = None,
        exclude_task_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> Iterator[sqlite3.Connection]:
        """Hold a SQLite write transaction through task persistence."""

        now = self._now()
        connection_context = (
            database.connect() if connection is None else nullcontext(connection)
        )
        with connection_context as active_connection:
            if connection is None:
                active_connection.execute("BEGIN IMMEDIATE")
            self._check_active_tasks(
                active_connection,
                context=context,
                settings=settings,
                now=now,
                exclude_task_id=exclude_task_id,
            )
            if file_count:
                usages: dict[UsageResource, int] = {"files": file_count}
                self._check_many(
                    active_connection,
                    context=context,
                    usages=usages,
                    settings=settings,
                    now=now,
                )
                self._record_many(
                    active_connection,
                    context=context,
                    usages=usages,
                    resource_id=task_id,
                    request_id=request_id,
                    now=now,
                )
            yield active_connection

    def _scope_summary(
        self,
        connection: sqlite3.Connection,
        *,
        scope: UsageScopeKind,
        scope_id: str | None,
        settings: Settings,
        now: datetime,
    ) -> UsageScopeSummary:
        active_limit = (
            settings.max_active_tasks_per_project
            if scope == "project"
            else settings.max_active_tasks_per_user
        )
        scope_record = _Scope(scope, scope_id, active_limit)
        cutoff = now - timedelta(seconds=settings.usage_window_seconds)
        metrics: list[UsageMetric] = []
        for resource in RESOURCE_LABELS:
            user_limit, project_limit = self._limits(resource, settings)
            limit = project_limit if scope == "project" else user_limit
            rows = self._window_rows(
                connection,
                scope=_Scope(scope, scope_id, limit),
                resource=resource,
                cutoff=cutoff,
            )
            used = sum(int(row["quantity"]) for row in rows)
            next_reset_at = (
                datetime.fromisoformat(str(rows[0]["occurred_at"]))
                + timedelta(seconds=settings.usage_window_seconds)
                if rows
                else None
            )
            metrics.append(
                UsageMetric(
                    resource=resource,
                    used=used,
                    limit=limit,
                    remaining=max(0, limit - used),
                    next_reset_at=next_reset_at,
                ),
            )
        return UsageScopeSummary(
            scope=scope,
            scope_id=scope_id,
            window_seconds=settings.usage_window_seconds,
            active_tasks=self._active_count(connection, scope_record),
            active_task_limit=active_limit,
            metrics=metrics,
        )

    def summary(
        self,
        *,
        user_id: str,
        project_id: str | None,
        settings: Settings,
    ) -> UsageSummaryResponse:
        now = self._now()
        with database.connect() as connection:
            user = self._scope_summary(
                connection,
                scope="user",
                scope_id=user_id,
                settings=settings,
                now=now,
            )
            project = (
                self._scope_summary(
                    connection,
                    scope="project",
                    scope_id=project_id,
                    settings=settings,
                    now=now,
                )
                if project_id is not None
                else None
            )
        return UsageSummaryResponse(user=user, project=project)


usage_service = UsageService()
