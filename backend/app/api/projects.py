"""Authenticated project ownership, membership, and audit endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.auth import CurrentAuth, request_id, security_http_error
from app.schemas.security import (
    AddProjectMemberRequest,
    AuditListResponse,
    CreateProjectRequest,
    DeleteProjectRequest,
    DeleteStatusResponse,
    ProjectListResponse,
    ProjectMemberListResponse,
    ProjectMemberResponse,
    ProjectResponse,
    UpdateProjectMemberRequest,
    UpdateProjectRequest,
)
from app.services.security import SecurityError, security_service

router = APIRouter(prefix="/api", tags=["projects"])


def raise_project_error(error: SecurityError) -> HTTPException:
    return security_http_error(error)


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(authenticated: CurrentAuth) -> ProjectListResponse:
    return security_service.list_projects(authenticated.user)


@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: CreateProjectRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> ProjectResponse:
    try:
        return security_service.create_project(
            authenticated.user,
            payload,
            request_id(request),
        )
    except SecurityError as error:
        raise raise_project_error(error) from error


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, authenticated: CurrentAuth) -> ProjectResponse:
    try:
        return security_service.get_project(project_id, authenticated.user)
    except SecurityError as error:
        raise raise_project_error(error) from error


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    payload: UpdateProjectRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> ProjectResponse:
    try:
        return security_service.update_project(
            project_id,
            authenticated.user,
            payload,
            request_id(request),
        )
    except SecurityError as error:
        raise raise_project_error(error) from error


@router.delete("/projects/{project_id}", response_model=DeleteStatusResponse)
async def delete_project(
    project_id: str,
    payload: DeleteProjectRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> DeleteStatusResponse:
    try:
        security_service.delete_project(
            project_id,
            authenticated.user,
            payload.project_name,
            request_id(request),
        )
    except SecurityError as error:
        raise raise_project_error(error) from error
    return DeleteStatusResponse(deleted_project_id=project_id)


@router.get(
    "/projects/{project_id}/members",
    response_model=ProjectMemberListResponse,
)
async def list_project_members(
    project_id: str,
    authenticated: CurrentAuth,
) -> ProjectMemberListResponse:
    try:
        return security_service.list_members(project_id, authenticated.user)
    except SecurityError as error:
        raise raise_project_error(error) from error


@router.post(
    "/projects/{project_id}/members",
    response_model=ProjectMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_member(
    project_id: str,
    payload: AddProjectMemberRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> ProjectMemberResponse:
    try:
        return security_service.add_member(
            project_id,
            authenticated.user,
            payload,
            request_id(request),
        )
    except SecurityError as error:
        raise raise_project_error(error) from error


@router.patch(
    "/projects/{project_id}/members/{member_user_id}",
    response_model=ProjectMemberResponse,
)
async def update_project_member(
    project_id: str,
    member_user_id: str,
    payload: UpdateProjectMemberRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> ProjectMemberResponse:
    try:
        return security_service.update_member(
            project_id,
            member_user_id,
            authenticated.user,
            payload.role,
            request_id(request),
        )
    except SecurityError as error:
        raise raise_project_error(error) from error


@router.delete(
    "/projects/{project_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_project_member(
    project_id: str,
    member_user_id: str,
    authenticated: CurrentAuth,
    request: Request,
) -> Response:
    try:
        security_service.remove_member(
            project_id,
            member_user_id,
            authenticated.user,
            request_id(request),
        )
    except SecurityError as error:
        raise raise_project_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit", response_model=AuditListResponse)
async def list_my_audit(authenticated: CurrentAuth) -> AuditListResponse:
    return security_service.list_user_audit(authenticated.user)


@router.get("/projects/{project_id}/audit", response_model=AuditListResponse)
async def list_project_audit(
    project_id: str,
    authenticated: CurrentAuth,
) -> AuditListResponse:
    try:
        return security_service.list_project_audit(project_id, authenticated.user)
    except SecurityError as error:
        raise raise_project_error(error) from error
