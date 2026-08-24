"""HTTP endpoints for the durable, project-aware single-Agent workflow."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)

from app.api.auth import OptionalAuth, request_id, security_http_error
from app.api.usage import usage_http_error
from app.config import settings
from app.schemas.agents import AgentTaskListResponse, AgentTaskResponse, ToolSpec
from app.services.agent_tasks import AgentTaskError, agent_task_service
from app.services.agent_tools import list_tool_specs
from app.services.analysis import AnalysisInputError
from app.services.security import (
    AuthenticatedUser,
    AuthenticationError,
    PermissionRequirement,
    SecurityError,
    security_service,
)
from app.services.usage import UsageLimitError

router = APIRouter(prefix="/api", tags=["agent"])


@router.get("/tools", response_model=list[ToolSpec])
async def get_tools() -> list[ToolSpec]:
    """List the fixed allowlist of read-only Agent tools."""

    return list_tool_specs()


@router.get("/tasks", response_model=AgentTaskListResponse)
async def list_tasks(
    authenticated: OptionalAuth,
    project_id: Annotated[str | None, Query()] = None,
) -> AgentTaskListResponse:
    """List only legacy local tasks or authenticated visible project tasks."""

    if authenticated is None:
        if project_id is not None:
            raise security_http_error(AuthenticationError("查询项目任务前请先登录。"))
        if not settings.allow_legacy_local_workflows:
            raise security_http_error(AuthenticationError("本地匿名工作流已禁用，请先登录。"))
        return agent_task_service.list_tasks(legacy_only=True)
    if project_id is not None:
        try:
            security_service.require_project_permission(
                project_id,
                authenticated.user,
                "read",
            )
        except SecurityError as error:
            raise security_http_error(error) from error
        return agent_task_service.list_tasks(project_ids={project_id})
    project_ids = security_service.list_accessible_project_ids(authenticated.user)
    return agent_task_service.list_tasks(project_ids=project_ids)


@router.post("/tasks", response_model=AgentTaskResponse)
async def create_task(
    goal: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    request: Request,
    authenticated: OptionalAuth,
    project_id: Annotated[str | None, Form()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AgentTaskResponse:
    """Create a plan-mode task and stop before any tool execution."""

    try:
        if project_id is not None:
            if authenticated is None:
                raise AuthenticationError("创建项目任务前请先登录。")
            security_service.require_project_permission(
                project_id,
                authenticated.user,
                "write",
            )
        elif authenticated is not None:
            raise AgentTaskError("已登录用户创建任务时必须选择项目。")
        elif not settings.allow_legacy_local_workflows:
            raise AuthenticationError("本地匿名工作流已禁用，请先登录并选择项目。")
        response = await agent_task_service.create_task(
            goal=goal,
            uploads=files,
            settings=settings,
            project_id=project_id,
            owner_user_id=authenticated.user.user_id if authenticated else None,
            request_id=request_id(request),
            idempotency_key=idempotency_key,
        )
        if project_id is not None and authenticated is not None:
            security_service.record_audit_event(
                actor_user_id=authenticated.user.user_id,
                action="task.created",
                resource_type="agent_task",
                resource_id=response.task_id,
                project_id=project_id,
                request_id=request_id(request),
                detail={"file_count": response.file_count},
            )
        return response
    except AnalysisInputError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except SecurityError as error:
        raise security_http_error(error) from error
    except UsageLimitError as error:
        raise usage_http_error(error) from error
    except AgentTaskError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


def task_action_error(error: AgentTaskError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


def authorize_task(
    task_id: str,
    authenticated: AuthenticatedUser | None,
    permission: PermissionRequirement,
) -> str | None:
    project_id, _owner_user_id = agent_task_service.get_task_access(task_id)
    if project_id is None:
        if not settings.allow_legacy_local_workflows:
            raise AuthenticationError("本地匿名工作流已禁用，请先登录。")
        return None
    if authenticated is None:
        raise AuthenticationError("访问项目任务前请先登录。")
    security_service.require_project_permission(
        project_id,
        authenticated.user,
        permission,
    )
    return project_id


def audit_task_action(
    *,
    authenticated: AuthenticatedUser | None,
    project_id: str | None,
    task_id: str,
    action: str,
    request: Request,
) -> None:
    if project_id is None or authenticated is None:
        return
    security_service.record_audit_event(
        actor_user_id=authenticated.user.user_id,
        action=action,
        resource_type="agent_task",
        resource_id=task_id,
        project_id=project_id,
        request_id=request_id(request),
    )


@router.get("/tasks/{task_id}", response_model=AgentTaskResponse)
async def get_task(task_id: str, authenticated: OptionalAuth) -> AgentTaskResponse:
    """Return one current task snapshot."""

    try:
        authorize_task(task_id, authenticated, "read")
        return agent_task_service.get_task(task_id)
    except SecurityError as error:
        raise security_http_error(error) from error
    except AgentTaskError as error:
        raise task_action_error(error) from error


@router.post("/tasks/{task_id}/confirm", response_model=AgentTaskResponse)
async def confirm_task(
    task_id: str,
    authenticated: OptionalAuth,
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AgentTaskResponse:
    """Confirm and start the registered read-only plan."""

    try:
        project_id = authorize_task(task_id, authenticated, "write")
        response = agent_task_service.confirm_task(
            task_id,
            settings,
            idempotency_key,
        )
        audit_task_action(
            authenticated=authenticated,
            project_id=project_id,
            task_id=task_id,
            action="task.confirmed",
            request=request,
        )
        return response
    except SecurityError as error:
        raise security_http_error(error) from error
    except AgentTaskError as error:
        raise task_action_error(error) from error


@router.post("/tasks/{task_id}/cancel", response_model=AgentTaskResponse)
async def cancel_task(
    task_id: str,
    authenticated: OptionalAuth,
    request: Request,
) -> AgentTaskResponse:
    """Cancel a waiting or running task."""

    try:
        project_id = authorize_task(task_id, authenticated, "write")
        response = agent_task_service.cancel_task(task_id)
        audit_task_action(
            authenticated=authenticated,
            project_id=project_id,
            task_id=task_id,
            action="task.cancel_requested",
            request=request,
        )
        return response
    except SecurityError as error:
        raise security_http_error(error) from error
    except AgentTaskError as error:
        raise task_action_error(error) from error


@router.post("/tasks/{task_id}/resume", response_model=AgentTaskResponse)
async def resume_task(
    task_id: str,
    authenticated: OptionalAuth,
    request: Request,
) -> AgentTaskResponse:
    """Restore a cancelled or failed task to confirmation."""

    try:
        project_id = authorize_task(task_id, authenticated, "write")
        response = agent_task_service.resume_task(
            task_id,
            settings,
            request_id(request),
        )
        audit_task_action(
            authenticated=authenticated,
            project_id=project_id,
            task_id=task_id,
            action="task.resumed",
            request=request,
        )
        return response
    except SecurityError as error:
        raise security_http_error(error) from error
    except UsageLimitError as error:
        raise usage_http_error(error) from error
    except AgentTaskError as error:
        raise task_action_error(error) from error
