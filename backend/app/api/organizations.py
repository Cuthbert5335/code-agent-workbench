"""Organization and organization-member management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.auth import CurrentAuth, request_id, security_http_error
from app.schemas.security import (
    AddOrganizationMemberRequest,
    CreateOrganizationRequest,
    OrganizationListResponse,
    OrganizationMemberListResponse,
    OrganizationMemberResponse,
    OrganizationResponse,
    UpdateOrganizationMemberRequest,
    UpdateOrganizationRequest,
)
from app.services.security import SecurityError, security_service

router = APIRouter(prefix="/api", tags=["organizations"])


def organization_error(error: SecurityError) -> HTTPException:
    return security_http_error(error)


@router.get("/organizations", response_model=OrganizationListResponse)
async def list_organizations(authenticated: CurrentAuth) -> OrganizationListResponse:
    return security_service.list_organizations(authenticated.user)


@router.post(
    "/organizations",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    payload: CreateOrganizationRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> OrganizationResponse:
    try:
        return security_service.create_organization(
            authenticated.user, payload, request_id(request)
        )
    except SecurityError as error:
        raise organization_error(error) from error


@router.get("/organizations/{organization_id}", response_model=OrganizationResponse)
async def get_organization(
    organization_id: str, authenticated: CurrentAuth
) -> OrganizationResponse:
    try:
        return security_service.get_organization(organization_id, authenticated.user)
    except SecurityError as error:
        raise organization_error(error) from error


@router.patch("/organizations/{organization_id}", response_model=OrganizationResponse)
async def update_organization(
    organization_id: str,
    payload: UpdateOrganizationRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> OrganizationResponse:
    try:
        return security_service.update_organization(
            organization_id, authenticated.user, payload, request_id(request)
        )
    except SecurityError as error:
        raise organization_error(error) from error


@router.get(
    "/organizations/{organization_id}/members",
    response_model=OrganizationMemberListResponse,
)
async def list_organization_members(
    organization_id: str, authenticated: CurrentAuth
) -> OrganizationMemberListResponse:
    try:
        return security_service.list_organization_members(organization_id, authenticated.user)
    except SecurityError as error:
        raise organization_error(error) from error


@router.post(
    "/organizations/{organization_id}/members",
    response_model=OrganizationMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_organization_member(
    organization_id: str,
    payload: AddOrganizationMemberRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> OrganizationMemberResponse:
    try:
        return security_service.add_organization_member(
            organization_id, authenticated.user, payload, request_id(request)
        )
    except SecurityError as error:
        raise organization_error(error) from error


@router.patch(
    "/organizations/{organization_id}/members/{member_user_id}",
    response_model=OrganizationMemberResponse,
)
async def update_organization_member(
    organization_id: str,
    member_user_id: str,
    payload: UpdateOrganizationMemberRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> OrganizationMemberResponse:
    try:
        return security_service.update_organization_member(
            organization_id,
            member_user_id,
            authenticated.user,
            payload,
            request_id(request),
        )
    except SecurityError as error:
        raise organization_error(error) from error


@router.delete(
    "/organizations/{organization_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_organization_member(
    organization_id: str,
    member_user_id: str,
    authenticated: CurrentAuth,
    request: Request,
) -> Response:
    try:
        security_service.remove_organization_member(
            organization_id, member_user_id, authenticated.user, request_id(request)
        )
    except SecurityError as error:
        raise organization_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
