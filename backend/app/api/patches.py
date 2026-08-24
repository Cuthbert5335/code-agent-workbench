"""HTTP API for durable, project-authorized structured patch workflows."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response

from app.api.auth import OptionalAuth, request_id, security_http_error
from app.api.usage import usage_http_error
from app.config import settings
from app.providers.openai_compatible import ModelProviderError
from app.schemas.patches import (
    ConfirmPatchActionRequest,
    CreatePatchRequest,
    PatchListResponse,
    PatchResponse,
    ReviewPatchFileRequest,
    RunValidationRequest,
    ValidatorSpec,
)
from app.services.agent_tasks import AgentTaskError, agent_task_service
from app.services.patches import PatchError, patch_service
from app.services.sandbox import SandboxError
from app.services.security import (
    AuthenticatedUser,
    AuthenticationError,
    PermissionRequirement,
    SecurityError,
    security_service,
)
from app.services.usage import UsageContext, UsageLimitError

router = APIRouter(prefix="/api", tags=["patches"])


def patch_http_error(error: PatchError | AgentTaskError | SandboxError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


def authorize_task_patch_access(
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
        raise AuthenticationError("访问项目补丁前请先登录。")
    security_service.require_project_permission(project_id, authenticated.user, permission)
    return project_id


def authorize_patch_access(
    patch_id: str,
    authenticated: AuthenticatedUser | None,
    permission: PermissionRequirement,
) -> tuple[str, str | None]:
    task_id = patch_service.get_patch_task_id(patch_id)
    return task_id, authorize_task_patch_access(task_id, authenticated, permission)


def audit_patch_action(
    *,
    authenticated: AuthenticatedUser | None,
    project_id: str | None,
    patch_id: str,
    action: str,
    request: Request,
) -> None:
    if authenticated is None or project_id is None:
        return
    security_service.record_audit_event(
        actor_user_id=authenticated.user.user_id,
        action=action,
        resource_type="patch",
        resource_id=patch_id,
        project_id=project_id,
        request_id=request_id(request),
    )


@router.get("/validators", response_model=list[ValidatorSpec])
async def list_validators() -> list[ValidatorSpec]:
    """List the fixed built-in validation allowlist."""

    return patch_service.list_validators(settings)


@router.get("/tasks/{task_id}/patches", response_model=PatchListResponse)
async def list_task_patches(
    task_id: str,
    authenticated: OptionalAuth,
) -> PatchListResponse:
    """List structured patches belonging to one completed task."""

    try:
        authorize_task_patch_access(task_id, authenticated, "read")
        return patch_service.list_patches(task_id)
    except SecurityError as error:
        raise security_http_error(error) from error
    except (PatchError, AgentTaskError) as error:
        raise patch_http_error(error) from error


@router.post("/tasks/{task_id}/patches", response_model=PatchResponse)
async def create_manual_patch(
    task_id: str,
    payload: CreatePatchRequest,
    authenticated: OptionalAuth,
    request: Request,
) -> PatchResponse:
    """Create a validated patch draft from structured content."""

    try:
        project_id = authorize_task_patch_access(task_id, authenticated, "write")
        response = patch_service.create_patch(
            task_id=task_id,
            request=payload,
            settings=settings,
            usage_context=UsageContext(
                user_id=authenticated.user.user_id if authenticated else None,
                project_id=project_id,
            ),
            request_id=request_id(request),
        )
        audit_patch_action(
            authenticated=authenticated,
            project_id=project_id,
            patch_id=response.patch_id,
            action="patch.created",
            request=request,
        )
        return response
    except SecurityError as error:
        raise security_http_error(error) from error
    except UsageLimitError as error:
        raise usage_http_error(error) from error
    except (PatchError, AgentTaskError, SandboxError) as error:
        raise patch_http_error(error) from error


@router.post("/tasks/{task_id}/patches/generate", response_model=PatchResponse)
async def generate_patch(
    task_id: str,
    authenticated: OptionalAuth,
    request: Request,
) -> PatchResponse:
    """Ask the configured model for a strict, review-only patch draft."""

    try:
        project_id = authorize_task_patch_access(task_id, authenticated, "write")
        response = await patch_service.generate_patch(
            task_id,
            settings,
            usage_context=UsageContext(
                user_id=authenticated.user.user_id if authenticated else None,
                project_id=project_id,
            ),
            request_id=request_id(request),
        )
        audit_patch_action(
            authenticated=authenticated,
            project_id=project_id,
            patch_id=response.patch_id,
            action="patch.generated",
            request=request,
        )
        return response
    except SecurityError as error:
        raise security_http_error(error) from error
    except UsageLimitError as error:
        raise usage_http_error(error) from error
    except (PatchError, AgentTaskError, SandboxError) as error:
        raise patch_http_error(error) from error
    except ModelProviderError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get("/patches/{patch_id}", response_model=PatchResponse)
async def get_patch(patch_id: str, authenticated: OptionalAuth) -> PatchResponse:
    """Return one public patch snapshot without full file contents."""

    try:
        authorize_patch_access(patch_id, authenticated, "read")
        return patch_service.get_patch(patch_id)
    except (PatchError, AgentTaskError) as error:
        raise patch_http_error(error) from error
    except SecurityError as error:
        raise security_http_error(error) from error


@router.post("/patches/{patch_id}/review", response_model=PatchResponse)
async def review_patch_file(
    patch_id: str,
    payload: ReviewPatchFileRequest,
    authenticated: OptionalAuth,
    request: Request,
) -> PatchResponse:
    """Accept or reject one file in a patch draft."""

    try:
        _task_id, project_id = authorize_patch_access(patch_id, authenticated, "write")
        response = patch_service.review_file(patch_id, payload.file, payload.decision)
        audit_patch_action(
            authenticated=authenticated,
            project_id=project_id,
            patch_id=patch_id,
            action="patch.file_reviewed",
            request=request,
        )
        return response
    except (PatchError, AgentTaskError) as error:
        raise patch_http_error(error) from error
    except SecurityError as error:
        raise security_http_error(error) from error


@router.post("/patches/{patch_id}/reject", response_model=PatchResponse)
async def reject_patch(
    patch_id: str,
    authenticated: OptionalAuth,
    request: Request,
) -> PatchResponse:
    """Reject the entire unapplied patch."""

    try:
        _task_id, project_id = authorize_patch_access(patch_id, authenticated, "write")
        response = patch_service.reject_patch(patch_id)
        audit_patch_action(
            authenticated=authenticated,
            project_id=project_id,
            patch_id=patch_id,
            action="patch.rejected",
            request=request,
        )
        return response
    except (PatchError, AgentTaskError) as error:
        raise patch_http_error(error) from error
    except SecurityError as error:
        raise security_http_error(error) from error


@router.post("/patches/{patch_id}/apply", response_model=PatchResponse)
async def apply_patch(
    patch_id: str,
    payload: ConfirmPatchActionRequest,
    authenticated: OptionalAuth,
    request: Request,
) -> PatchResponse:
    """Apply accepted files to the private task snapshot after confirmation."""

    del payload
    try:
        _task_id, project_id = authorize_patch_access(
            patch_id,
            authenticated,
            "apply_patch",
        )
        response = patch_service.apply_patch(patch_id)
        audit_patch_action(
            authenticated=authenticated,
            project_id=project_id,
            patch_id=patch_id,
            action="patch.apply_requested",
            request=request,
        )
        return response
    except (PatchError, AgentTaskError) as error:
        raise patch_http_error(error) from error
    except SecurityError as error:
        raise security_http_error(error) from error


@router.post("/patches/{patch_id}/revert", response_model=PatchResponse)
async def revert_patch(
    patch_id: str,
    payload: ConfirmPatchActionRequest,
    authenticated: OptionalAuth,
    request: Request,
) -> PatchResponse:
    """Revert an applied patch after explicit second confirmation."""

    del payload
    try:
        _task_id, project_id = authorize_patch_access(
            patch_id,
            authenticated,
            "apply_patch",
        )
        response = patch_service.revert_patch(patch_id)
        audit_patch_action(
            authenticated=authenticated,
            project_id=project_id,
            patch_id=patch_id,
            action="patch.revert_requested",
            request=request,
        )
        return response
    except (PatchError, AgentTaskError) as error:
        raise patch_http_error(error) from error
    except SecurityError as error:
        raise security_http_error(error) from error


@router.post("/patches/{patch_id}/validate", response_model=PatchResponse)
async def validate_patch(
    patch_id: str,
    payload: RunValidationRequest,
    authenticated: OptionalAuth,
    request: Request,
) -> PatchResponse:
    """Run built-in checks or explicitly confirmed isolated sandbox checks."""

    try:
        _task_id, project_id = authorize_patch_access(patch_id, authenticated, "write")
        response = patch_service.run_validation(
            patch_id,
            payload.validators,
            settings=settings,
            usage_context=UsageContext(
                user_id=authenticated.user.user_id if authenticated else None,
                project_id=project_id,
            ),
            request_id=request_id(request),
            confirm_execution=payload.confirm_execution,
        )
        audit_patch_action(
            authenticated=authenticated,
            project_id=project_id,
            patch_id=patch_id,
            action="patch.validated",
            request=request,
        )
        return response
    except (PatchError, AgentTaskError, SandboxError) as error:
        raise patch_http_error(error) from error
    except SecurityError as error:
        raise security_http_error(error) from error
    except UsageLimitError as error:
        raise usage_http_error(error) from error


@router.get("/patches/{patch_id}/files/{file_path:path}/download")
async def download_patch_file(
    patch_id: str,
    file_path: str,
    authenticated: OptionalAuth,
) -> Response:
    """Download one current file from the private in-memory task snapshot."""

    try:
        authorize_patch_access(patch_id, authenticated, "read")
        content = patch_service.download_content(patch_id, file_path)
    except (PatchError, AgentTaskError) as error:
        raise patch_http_error(error) from error
    except SecurityError as error:
        raise security_http_error(error) from error
    filename = file_path.rsplit("/", maxsplit=1)[-1]
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
