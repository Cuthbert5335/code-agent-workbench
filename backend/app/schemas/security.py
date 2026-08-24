"""Strict authentication, project permission, and audit API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ProjectRole = Literal["owner", "admin", "editor", "viewer"]
ProjectRunMode = Literal["local", "service"]
ProjectPermission = Literal["read", "write", "apply_patch", "manage", "delete"]
OrganizationRole = Literal["owner", "admin", "member"]


class StrictRequest(BaseModel):
    """Reject unknown fields on all security-sensitive writes."""

    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictRequest):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("display_name")
    @classmethod
    def trim_display_name(cls, value: str) -> str:
        return value.strip()


class LoginRequest(StrictRequest):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class ChangePasswordRequest(StrictRequest):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class DeleteAccountRequest(StrictRequest):
    confirm: Literal[True]
    email: str = Field(min_length=3, max_length=254)
    current_password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class UserResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    is_system_admin: bool
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserResponse


class AuthStatusResponse(BaseModel):
    status: Literal["logged_out", "password_changed"]


class DeleteAccountStatusResponse(BaseModel):
    status: Literal["deleted"] = "deleted"
    deleted_user_id: str


class RetentionPolicyResponse(BaseModel):
    expired_sessions_days: int = Field(ge=1)
    login_attempts_days: int = Field(ge=1)
    terminal_tasks_days: int = Field(ge=1)
    patches_days: int = Field(ge=1)
    validations_days: int = Field(ge=1)
    audit_logs_days: int = Field(ge=1)
    usage_records_days: int = Field(ge=1)
    cleanup_interval_seconds: int = Field(ge=60)


class DeleteStatusResponse(BaseModel):
    status: Literal["deleted"] = "deleted"
    deleted_project_id: str


class CreateProjectRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    run_mode: ProjectRunMode = "local"
    default_model: str | None = Field(default=None, max_length=200)
    organization_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        return value.strip()


class UpdateProjectRequest(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    run_mode: ProjectRunMode | None = None
    default_model: str | None = Field(default=None, max_length=200)
    archived: bool | None = None

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> UpdateProjectRequest:
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的项目字段。")
        return self


class DeleteProjectRequest(StrictRequest):
    confirm: Literal[True]
    project_name: str = Field(min_length=1, max_length=120)


class AddProjectMemberRequest(StrictRequest):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["admin", "editor", "viewer"]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class UpdateProjectMemberRequest(StrictRequest):
    role: Literal["admin", "editor", "viewer"]


class ProjectResponse(BaseModel):
    project_id: str
    organization_id: str | None
    name: str
    description: str
    owner_user_id: str
    role: ProjectRole
    permissions: list[ProjectPermission]
    run_mode: ProjectRunMode
    default_model: str | None
    file_count: int = Field(ge=0)
    index_status: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
    total: int = Field(ge=0)


class ProjectMemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: ProjectRole
    created_at: datetime


class ProjectMemberListResponse(BaseModel):
    members: list[ProjectMemberResponse]
    total: int = Field(ge=0)


class AuditEventResponse(BaseModel):
    audit_id: str
    actor_user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    project_id: str | None
    organization_id: str | None = None
    request_id: str | None
    detail: dict[str, str | int | bool | None]
    created_at: datetime


class AuditListResponse(BaseModel):
    events: list[AuditEventResponse]
    total: int = Field(ge=0)


class CreateOrganizationRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        return value.strip()


class UpdateOrganizationRequest(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> UpdateOrganizationRequest:
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的组织字段。")
        return self


class OrganizationResponse(BaseModel):
    organization_id: str
    name: str
    description: str
    owner_user_id: str
    role: OrganizationRole
    created_at: datetime
    updated_at: datetime


class OrganizationListResponse(BaseModel):
    organizations: list[OrganizationResponse]
    total: int = Field(ge=0)


class AddOrganizationMemberRequest(StrictRequest):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["admin", "member"]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class UpdateOrganizationMemberRequest(StrictRequest):
    role: Literal["admin", "member"]


class OrganizationMemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: OrganizationRole
    created_at: datetime


class OrganizationMemberListResponse(BaseModel):
    members: list[OrganizationMemberResponse]
    total: int = Field(ge=0)
