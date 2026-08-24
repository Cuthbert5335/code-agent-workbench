"""Durable local authentication, project permissions, and audit service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from app.config import Settings, settings
from app.schemas.security import (
    AddOrganizationMemberRequest,
    AddProjectMemberRequest,
    AuditEventResponse,
    AuditListResponse,
    AuthResponse,
    CreateOrganizationRequest,
    CreateProjectRequest,
    OrganizationListResponse,
    OrganizationMemberListResponse,
    OrganizationMemberResponse,
    OrganizationResponse,
    OrganizationRole,
    ProjectListResponse,
    ProjectMemberListResponse,
    ProjectMemberResponse,
    ProjectPermission,
    ProjectResponse,
    ProjectRole,
    RegisterRequest,
    UpdateOrganizationMemberRequest,
    UpdateOrganizationRequest,
    UpdateProjectRequest,
    UserResponse,
)
from app.services.retention import discard_workflow_caches
from app.storage import database

PASSWORD_HASH_ITERATIONS = 600_000
EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$",
)
PROJECT_PERMISSIONS: dict[ProjectRole, tuple[ProjectPermission, ...]] = {
    "viewer": ("read",),
    "editor": ("read", "write", "apply_patch"),
    "admin": ("read", "write", "apply_patch", "manage", "delete"),
    "owner": ("read", "write", "apply_patch", "manage", "delete"),
}
PermissionRequirement = Literal["read", "write", "apply_patch", "manage", "delete"]
ACTIVE_TASK_STATUSES = (
    "created",
    "planning",
    "waiting_for_confirmation",
    "queued",
    "executing",
    "reviewing",
    "validating",
)


class SecurityError(Exception):
    """Expected security boundary error with an HTTP-friendly status."""

    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AuthenticationError(SecurityError):
    status_code = 401


class PermissionDeniedError(SecurityError):
    status_code = 403


class SecurityConflictError(SecurityError):
    status_code = 409


class SecurityNotFoundError(SecurityError):
    status_code = 404


class LoginRateLimitError(SecurityError):
    status_code = 429


@dataclass(frozen=True)
class AuthenticatedUser:
    user: UserResponse
    raw_token: str


def utc_now() -> datetime:
    return datetime.now(UTC)


def datetime_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def required_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def constant_time_text_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(PASSWORD_HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


class SecurityService:
    """Own authentication and project state behind one SQLite boundary."""

    def __init__(self, app_settings: Settings) -> None:
        self.settings = app_settings
        self.database = database
        self._dummy_password_hash = hash_password("not-a-real-user-password")

    def use_database_for_test(self, path: str) -> None:
        """Point the singleton at an isolated test database."""

        self.database.reconfigure(path)

    def require_project_permission(
        self,
        project_id: str,
        user: UserResponse,
        permission: PermissionRequirement,
    ) -> None:
        """Expose one centralized authorization check to workflow routes."""

        with self.database.connect() as connection:
            self._require_project(connection, project_id, user.user_id, permission)

    def list_accessible_project_ids(self, user: UserResponse) -> set[str]:
        """Return project identifiers visible to one authenticated account."""

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT p.project_id
                FROM projects AS p
                LEFT JOIN project_members AS pm
                    ON pm.project_id = p.project_id AND pm.user_id = ?
                LEFT JOIN organization_members AS om
                    ON om.organization_id = p.organization_id AND om.user_id = ?
                WHERE (
                    p.organization_id IS NULL
                    AND (p.owner_user_id = ? OR pm.user_id = ?)
                ) OR om.user_id = ?
                """,
                (
                    user.user_id,
                    user.user_id,
                    user.user_id,
                    user.user_id,
                    user.user_id,
                ),
            ).fetchall()
        return {row["project_id"] for row in rows}

    def record_audit_event(
        self,
        *,
        actor_user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        project_id: str,
        request_id: str | None,
        detail: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        """Append a workflow audit event without exposing the internal SQL helper."""

        with self.database.connect() as connection:
            self._audit(
                connection,
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                project_id=project_id,
                request_id=request_id,
                detail=detail,
            )

    def _user(self, row: sqlite3.Row) -> UserResponse:
        return UserResponse(
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            is_system_admin=bool(row["is_system_admin"]),
            created_at=required_datetime(row["created_at"]),
        )

    def _validate_email(self, email: str) -> None:
        if not EMAIL_PATTERN.fullmatch(email) or ".." in email:
            raise SecurityError("请输入有效的邮箱地址。")

    def _validate_password(self, password: str, email: str | None = None) -> None:
        if len(password) < 8 or len(password) > 128 or password.isspace():
            raise SecurityError("密码长度必须为 8 到 128 个字符。")
        if email and email.split("@", maxsplit=1)[0] in password.casefold():
            raise SecurityError("密码不能包含邮箱账号部分。")

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        actor_user_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        project_id: str | None,
        request_id: str | None,
        detail: dict[str, str | int | bool | None] | None = None,
        organization_id: str | None = None,
    ) -> None:
        if organization_id is None and project_id is not None:
            organization = connection.execute(
                "SELECT organization_id FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            organization_id = organization["organization_id"] if organization else None
        connection.execute(
            """
            INSERT INTO audit_logs (
                audit_id, actor_user_id, action, resource_type, resource_id,
                project_id, organization_id, request_id, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                actor_user_id,
                action,
                resource_type,
                resource_id,
                project_id,
                organization_id,
                request_id,
                json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":")),
                datetime_text(utc_now()),
            ),
        )

    def register(self, request: RegisterRequest, request_id: str | None) -> UserResponse:
        email = request.email.strip().casefold()
        self._validate_email(email)
        self._validate_password(request.password, email)
        now = datetime_text(utc_now())
        user_id = uuid4().hex
        with self.database.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, email, display_name, password_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        email,
                        request.display_name.strip(),
                        hash_password(request.password),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise SecurityConflictError("该邮箱已经注册。") from error
            self._audit(
                connection,
                actor_user_id=user_id,
                action="user.registered",
                resource_type="user",
                resource_id=user_id,
                project_id=None,
                request_id=request_id,
            )
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            organization_id = f"personal-{user_id}"
            connection.execute(
                """
                INSERT INTO organizations (
                    organization_id, owner_user_id, name, description, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    user_id,
                    f"{request.display_name.strip()} 的个人组织",
                    "注册时自动创建的默认组织",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO organization_members (
                    organization_id, user_id, role, created_at, updated_at
                ) VALUES (?, ?, 'owner', ?, ?)
                """,
                (organization_id, user_id, now, now),
            )
        if row is None:
            raise RuntimeError("注册后的用户记录不可用。")
        return self._user(row)

    def _organization_row(
        self,
        connection: sqlite3.Connection,
        organization_id: str,
        user_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT o.*, om.role AS access_role
            FROM organizations AS o
            LEFT JOIN organization_members AS om
                ON om.organization_id = o.organization_id AND om.user_id = ?
            WHERE o.organization_id = ?
            """,
            (user_id, organization_id),
        ).fetchone()
        if row is None:
            raise SecurityNotFoundError("组织不存在。")
        if row["access_role"] is None:
            raise PermissionDeniedError("你没有访问该组织的权限。")
        return row

    def _require_organization(
        self,
        connection: sqlite3.Connection,
        organization_id: str,
        user_id: str,
        role: OrganizationRole,
    ) -> sqlite3.Row:
        row = self._organization_row(connection, organization_id, user_id)
        ranks = {"member": 1, "admin": 2, "owner": 3}
        if ranks[row["access_role"]] < ranks[role]:
            raise PermissionDeniedError("当前组织角色没有执行该操作的权限。")
        return row

    def _organization(self, row: sqlite3.Row) -> OrganizationResponse:
        return OrganizationResponse(
            organization_id=row["organization_id"],
            name=row["name"],
            description=row["description"],
            owner_user_id=row["owner_user_id"],
            role=row["access_role"],
            created_at=required_datetime(row["created_at"]),
            updated_at=required_datetime(row["updated_at"]),
        )

    def list_organizations(self, user: UserResponse) -> OrganizationListResponse:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT o.*, om.role AS access_role
                FROM organizations AS o
                JOIN organization_members AS om
                    ON om.organization_id = o.organization_id AND om.user_id = ?
                ORDER BY o.updated_at DESC LIMIT 100
                """,
                (user.user_id,),
            ).fetchall()
        organizations = [self._organization(row) for row in rows]
        return OrganizationListResponse(organizations=organizations, total=len(organizations))

    def create_organization(
        self,
        user: UserResponse,
        request: CreateOrganizationRequest,
        request_id: str | None,
    ) -> OrganizationResponse:
        organization_id = uuid4().hex
        now = datetime_text(utc_now())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO organizations (
                    organization_id, owner_user_id, name, description, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (organization_id, user.user_id, request.name, request.description, now, now),
            )
            connection.execute(
                """
                INSERT INTO organization_members (
                    organization_id, user_id, role, created_at, updated_at
                ) VALUES (?, ?, 'owner', ?, ?)
                """,
                (organization_id, user.user_id, now, now),
            )
            self._audit(
                connection,
                actor_user_id=user.user_id,
                action="organization.created",
                resource_type="organization",
                resource_id=organization_id,
                project_id=None,
                organization_id=organization_id,
                request_id=request_id,
                detail={"name": request.name},
            )
            row = self._organization_row(connection, organization_id, user.user_id)
        return self._organization(row)

    def get_organization(self, organization_id: str, user: UserResponse) -> OrganizationResponse:
        with self.database.connect() as connection:
            row = self._organization_row(connection, organization_id, user.user_id)
        return self._organization(row)

    def update_organization(
        self,
        organization_id: str,
        user: UserResponse,
        request: UpdateOrganizationRequest,
        request_id: str | None,
    ) -> OrganizationResponse:
        values = request.model_dump(exclude_unset=True)
        with self.database.connect() as connection:
            self._require_organization(connection, organization_id, user.user_id, "admin")
            assignments = [f"{field} = ?" for field in values]
            parameters = [values[field] for field in values]
            assignments.append("updated_at = ?")
            parameters.append(datetime_text(utc_now()))
            parameters.append(organization_id)
            connection.execute(
                f"UPDATE organizations SET {', '.join(assignments)} WHERE organization_id = ?",
                parameters,
            )
            self._audit(
                connection,
                actor_user_id=user.user_id,
                action="organization.updated",
                resource_type="organization",
                resource_id=organization_id,
                project_id=None,
                organization_id=organization_id,
                request_id=request_id,
                detail={"fields": ",".join(sorted(values))},
            )
            row = self._organization_row(connection, organization_id, user.user_id)
        return self._organization(row)

    def list_organization_members(
        self,
        organization_id: str,
        user: UserResponse,
    ) -> OrganizationMemberListResponse:
        with self.database.connect() as connection:
            self._require_organization(connection, organization_id, user.user_id, "member")
            rows = connection.execute(
                """
                SELECT u.user_id, u.email, u.display_name, om.role, om.created_at
                FROM organization_members AS om
                JOIN users AS u ON u.user_id = om.user_id
                WHERE om.organization_id = ? ORDER BY om.role, u.email
                """,
                (organization_id,),
            ).fetchall()
        members = [
            OrganizationMemberResponse(
                user_id=row["user_id"], email=row["email"], display_name=row["display_name"],
                role=row["role"], created_at=required_datetime(row["created_at"]),
            ) for row in rows
        ]
        return OrganizationMemberListResponse(members=members, total=len(members))

    def add_organization_member(
        self,
        organization_id: str,
        user: UserResponse,
        request: AddOrganizationMemberRequest,
        request_id: str | None,
    ) -> OrganizationMemberResponse:
        self._validate_email(request.email)
        with self.database.connect() as connection:
            self._require_organization(connection, organization_id, user.user_id, "admin")
            member = connection.execute(
                "SELECT * FROM users WHERE email = ? AND disabled_at IS NULL", (request.email,)
            ).fetchone()
            if member is None:
                raise SecurityNotFoundError("待添加的用户不存在。")
            now = datetime_text(utc_now())
            try:
                connection.execute(
                    """
                    INSERT INTO organization_members (
                        organization_id, user_id, role, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (organization_id, member["user_id"], request.role, now, now),
                )
            except sqlite3.IntegrityError as error:
                raise SecurityConflictError("该用户已经是组织成员。") from error
            self._audit(
                connection,
                actor_user_id=user.user_id,
                action="organization.member_added",
                resource_type="organization_member",
                resource_id=member["user_id"],
                project_id=None,
                organization_id=organization_id,
                request_id=request_id,
                detail={"role": request.role},
            )
        return OrganizationMemberResponse(
            user_id=member["user_id"], email=member["email"], display_name=member["display_name"],
            role=request.role, created_at=required_datetime(now),
        )

    def update_organization_member(
        self,
        organization_id: str,
        member_user_id: str,
        user: UserResponse,
        request: UpdateOrganizationMemberRequest,
        request_id: str | None,
    ) -> OrganizationMemberResponse:
        with self.database.connect() as connection:
            self._require_organization(connection, organization_id, user.user_id, "admin")
            now = datetime_text(utc_now())
            cursor = connection.execute(
                """
                UPDATE organization_members SET role = ?, updated_at = ?
                WHERE organization_id = ? AND user_id = ? AND role <> 'owner'
                """,
                (request.role, now, organization_id, member_user_id),
            )
            if cursor.rowcount == 0:
                raise SecurityNotFoundError("组织成员不存在或不能修改组织所有者角色。")
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (member_user_id,)).fetchone()
            self._audit(
                connection, actor_user_id=user.user_id, action="organization.member_role_updated",
                resource_type="organization_member", resource_id=member_user_id, project_id=None,
                organization_id=organization_id, request_id=request_id, detail={"role": request.role},
            )
        return OrganizationMemberResponse(
            user_id=row["user_id"], email=row["email"], display_name=row["display_name"],
            role=request.role, created_at=required_datetime(now),
        )

    def remove_organization_member(
        self,
        organization_id: str,
        member_user_id: str,
        user: UserResponse,
        request_id: str | None,
    ) -> None:
        with self.database.connect() as connection:
            self._require_organization(connection, organization_id, user.user_id, "admin")
            owned_projects = connection.execute(
                """
                SELECT COUNT(*) FROM projects
                WHERE organization_id = ? AND owner_user_id = ?
                """,
                (organization_id, member_user_id),
            ).fetchone()[0]
            if owned_projects:
                raise SecurityConflictError("该成员仍是组织内项目所有者，不能移出组织。")
            cursor = connection.execute(
                "DELETE FROM organization_members WHERE organization_id = ? AND user_id = ? AND role <> 'owner'",
                (organization_id, member_user_id),
            )
            if cursor.rowcount == 0:
                raise SecurityNotFoundError("组织成员不存在或不能删除组织所有者。")
            connection.execute(
                """
                DELETE FROM project_members
                WHERE user_id = ? AND project_id IN (
                    SELECT project_id FROM projects WHERE organization_id = ?
                )
                """,
                (member_user_id, organization_id),
            )
            self._audit(
                connection, actor_user_id=user.user_id, action="organization.member_removed",
                resource_type="organization_member", resource_id=member_user_id, project_id=None,
                organization_id=organization_id, request_id=request_id,
            )

    def login(
        self,
        email: str,
        password: str,
        client_address: str,
        request_id: str | None,
    ) -> AuthResponse:
        normalized_email = email.strip().casefold()
        self._validate_email(normalized_email)
        now = utc_now()
        cutoff = now - timedelta(seconds=self.settings.login_failure_window_seconds)
        retention_cutoff = now - timedelta(
            days=self.settings.login_attempt_retention_days,
        )
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM login_attempts WHERE attempted_at < ?",
                (datetime_text(retention_cutoff),),
            )
            failures = connection.execute(
                """
                SELECT COUNT(*) FROM login_attempts AS failed
                WHERE failed.email = ? AND failed.client_address = ?
                    AND failed.succeeded = 0 AND failed.attempted_at >= ?
                    AND NOT EXISTS (
                        SELECT 1 FROM login_attempts AS succeeded
                        WHERE succeeded.email = failed.email
                            AND succeeded.client_address = failed.client_address
                            AND succeeded.succeeded = 1
                            AND succeeded.attempted_at > failed.attempted_at
                    )
                """,
                (normalized_email, client_address, datetime_text(cutoff)),
            ).fetchone()[0]
            if failures >= self.settings.login_max_failures:
                raise LoginRateLimitError("登录尝试过于频繁，请稍后再试。")

            row = connection.execute(
                "SELECT * FROM users WHERE email = ? AND disabled_at IS NULL",
                (normalized_email,),
            ).fetchone()
            password_hash = row["password_hash"] if row is not None else self._dummy_password_hash
            if not verify_password(password, password_hash) or row is None:
                connection.execute(
                    """
                    INSERT INTO login_attempts (email, client_address, succeeded, attempted_at)
                    VALUES (?, ?, 0, ?)
                    """,
                    (normalized_email, client_address, datetime_text(now)),
                )
                self._audit(
                    connection,
                    actor_user_id=None,
                    action="auth.login_failed",
                    resource_type="session",
                    resource_id=None,
                    project_id=None,
                    request_id=request_id,
                    detail={"reason": "invalid_credentials"},
                )
                connection.commit()
                raise AuthenticationError("邮箱或密码不正确。")

            connection.execute(
                """
                INSERT INTO login_attempts (email, client_address, succeeded, attempted_at)
                VALUES (?, ?, 1, ?)
                """,
                (normalized_email, client_address, datetime_text(now)),
            )
            raw_token = secrets.token_urlsafe(32)
            expires_at = now + timedelta(seconds=self.settings.session_ttl_seconds)
            session_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, user_id, token_hash, created_at, expires_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    row["user_id"],
                    token_digest(raw_token),
                    datetime_text(now),
                    datetime_text(expires_at),
                    datetime_text(now),
                ),
            )
            self._audit(
                connection,
                actor_user_id=row["user_id"],
                action="auth.logged_in",
                resource_type="session",
                resource_id=session_id,
                project_id=None,
                request_id=request_id,
            )
        return AuthResponse(
            access_token=raw_token,
            expires_at=expires_at,
            user=self._user(row),
        )

    def authenticate(self, raw_token: str) -> AuthenticatedUser:
        now = utc_now()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT u.*, s.expires_at, s.revoked_at
                FROM sessions AS s
                JOIN users AS u ON u.user_id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_digest(raw_token),),
            ).fetchone()
            if (
                row is None
                or row["revoked_at"] is not None
                or row["disabled_at"] is not None
                or required_datetime(row["expires_at"]) <= now
            ):
                raise AuthenticationError("登录已失效，请重新登录。")
            connection.execute(
                "UPDATE sessions SET last_used_at = ? WHERE token_hash = ?",
                (datetime_text(now), token_digest(raw_token)),
            )
        return AuthenticatedUser(user=self._user(row), raw_token=raw_token)

    def logout(self, authenticated: AuthenticatedUser, request_id: str | None) -> None:
        now = datetime_text(utc_now())
        with self.database.connect() as connection:
            session = connection.execute(
                "SELECT session_id FROM sessions WHERE token_hash = ?",
                (token_digest(authenticated.raw_token),),
            ).fetchone()
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (now, token_digest(authenticated.raw_token)),
            )
            self._audit(
                connection,
                actor_user_id=authenticated.user.user_id,
                action="auth.logged_out",
                resource_type="session",
                resource_id=session["session_id"] if session else None,
                project_id=None,
                request_id=request_id,
            )

    def change_password(
        self,
        authenticated: AuthenticatedUser,
        current_password: str,
        new_password: str,
        request_id: str | None,
    ) -> None:
        self._validate_password(new_password, authenticated.user.email)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE user_id = ?",
                (authenticated.user.user_id,),
            ).fetchone()
            if row is None or not verify_password(current_password, row["password_hash"]):
                raise AuthenticationError("当前密码不正确。")
            if verify_password(new_password, row["password_hash"]):
                raise SecurityError("新密码不能与当前密码相同。")
            now = datetime_text(utc_now())
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                (hash_password(new_password), now, authenticated.user.user_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, authenticated.user.user_id),
            )
            self._audit(
                connection,
                actor_user_id=authenticated.user.user_id,
                action="auth.password_changed",
                resource_type="user",
                resource_id=authenticated.user.user_id,
                project_id=None,
                request_id=request_id,
            )

    def delete_account(
        self,
        authenticated: AuthenticatedUser,
        *,
        email: str,
        current_password: str,
        request_id: str | None,
    ) -> str:
        """Irreversibly delete one account and every record it owns."""

        user_id = authenticated.user.user_id
        task_ids: set[str] = set()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ? AND disabled_at IS NULL",
                (user_id,),
            ).fetchone()
            if row is None:
                raise AuthenticationError("登录已失效，请重新登录。")
            if not constant_time_text_equal(str(row["email"]), email.strip().casefold()):
                raise SecurityError("账号邮箱确认不匹配，未执行删除。")
            if not verify_password(current_password, str(row["password_hash"])):
                raise AuthenticationError("当前密码不正确。")

            placeholders = ", ".join("?" for _status in ACTIVE_TASK_STATUSES)
            active_tasks = connection.execute(
                f"""
                SELECT COUNT(*) FROM agent_tasks AS task
                LEFT JOIN projects AS project ON project.project_id = task.project_id
                WHERE (task.owner_user_id = ? OR project.owner_user_id = ?)
                    AND task.status IN ({placeholders})
                """,
                (user_id, user_id, *ACTIVE_TASK_STATUSES),
            ).fetchone()[0]
            if active_tasks:
                raise SecurityConflictError("请先取消或完成账号下的活动任务。")

            task_rows = connection.execute(
                """
                SELECT task.task_id FROM agent_tasks AS task
                LEFT JOIN projects AS project ON project.project_id = task.project_id
                WHERE task.owner_user_id = ? OR project.owner_user_id = ?
                """,
                (user_id, user_id),
            ).fetchall()
            task_ids = {str(task["task_id"]) for task in task_rows}
            owned_projects = connection.execute(
                "SELECT COUNT(*) FROM projects WHERE owner_user_id = ?",
                (user_id,),
            ).fetchone()[0]
            self._audit(
                connection,
                actor_user_id=user_id,
                action="account.deleted",
                resource_type="user",
                resource_id=user_id,
                project_id=None,
                request_id=request_id,
                detail={"owned_projects": owned_projects, "irreversible": True},
            )
            connection.execute("DELETE FROM login_attempts WHERE email = ?", (row["email"],))
            connection.execute("DELETE FROM projects WHERE owner_user_id = ?", (user_id,))
            connection.execute("DELETE FROM users WHERE user_id = ?", (user_id,))

        discard_workflow_caches(task_ids)
        return user_id

    def _project_access_row(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        user_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT p.*,
                CASE
                    WHEN p.organization_id IS NULL AND p.owner_user_id = ? THEN 'owner'
                    WHEN p.organization_id IS NULL AND pm.role IS NOT NULL THEN pm.role
                    WHEN om.user_id IS NULL THEN NULL
                    WHEN p.owner_user_id = ? THEN 'owner'
                    WHEN pm.role IS NOT NULL THEN pm.role
                    WHEN om.role IN ('owner', 'admin') THEN 'admin'
                    WHEN om.role = 'member' THEN 'viewer'
                END AS access_role
            FROM projects AS p
            LEFT JOIN project_members AS pm
                ON pm.project_id = p.project_id AND pm.user_id = ?
            LEFT JOIN organization_members AS om
                ON om.organization_id = p.organization_id AND om.user_id = ?
            WHERE p.project_id = ?
            """,
            (user_id, user_id, user_id, user_id, project_id),
        ).fetchone()
        if row is None:
            raise SecurityNotFoundError("项目不存在。")
        if row["access_role"] is None:
            raise PermissionDeniedError("你没有访问该项目的权限。")
        return row

    def _require_project(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        user_id: str,
        permission: PermissionRequirement,
    ) -> sqlite3.Row:
        row = self._project_access_row(connection, project_id, user_id)
        if permission not in PROJECT_PERMISSIONS[row["access_role"]]:
            raise PermissionDeniedError("当前项目角色没有执行该操作的权限。")
        return row

    def _project(self, row: sqlite3.Row) -> ProjectResponse:
        role: ProjectRole = row["access_role"]
        return ProjectResponse(
            project_id=row["project_id"],
            organization_id=row["organization_id"],
            name=row["name"],
            description=row["description"],
            owner_user_id=row["owner_user_id"],
            role=role,
            permissions=list(PROJECT_PERMISSIONS[role]),
            run_mode=row["run_mode"],
            default_model=row["default_model"],
            file_count=row["file_count"],
            index_status=row["index_status"],
            archived_at=parse_datetime(row["archived_at"]),
            created_at=required_datetime(row["created_at"]),
            updated_at=required_datetime(row["updated_at"]),
        )

    def create_project(
        self,
        user: UserResponse,
        request: CreateProjectRequest,
        request_id: str | None,
    ) -> ProjectResponse:
        now = datetime_text(utc_now())
        project_id = uuid4().hex
        with self.database.connect() as connection:
            organization_id = request.organization_id
            if organization_id is None:
                organization = connection.execute(
                    """
                    SELECT organization_id FROM organization_members
                    WHERE user_id = ? ORDER BY role = 'owner' DESC, created_at LIMIT 1
                    """,
                    (user.user_id,),
                ).fetchone()
                if organization is None:
                    raise SecurityConflictError("请先创建或加入一个组织。")
                organization_id = organization["organization_id"]
            self._require_organization(connection, organization_id, user.user_id, "member")
            connection.execute(
                """
                INSERT INTO projects (
                    project_id, organization_id, owner_user_id, name, description,
                    run_mode, default_model, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    organization_id,
                    user.user_id,
                    request.name.strip(),
                    request.description,
                    request.run_mode,
                    request.default_model,
                    now,
                    now,
                ),
            )
            self._audit(
                connection,
                actor_user_id=user.user_id,
                action="project.created",
                resource_type="project",
                resource_id=project_id,
                project_id=project_id,
                request_id=request_id,
                detail={"name": request.name.strip()},
            )
            row = self._project_access_row(connection, project_id, user.user_id)
        return self._project(row)

    def list_projects(self, user: UserResponse) -> ProjectListResponse:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*,
                    CASE
                        WHEN p.organization_id IS NULL AND p.owner_user_id = ? THEN 'owner'
                        WHEN p.organization_id IS NULL AND pm.role IS NOT NULL THEN pm.role
                        WHEN om.user_id IS NULL THEN NULL
                        WHEN p.owner_user_id = ? THEN 'owner'
                        WHEN pm.role IS NOT NULL THEN pm.role
                        WHEN om.role IN ('owner', 'admin') THEN 'admin'
                        WHEN om.role = 'member' THEN 'viewer'
                    END AS access_role
                FROM projects AS p
                LEFT JOIN project_members AS pm
                    ON pm.project_id = p.project_id AND pm.user_id = ?
                LEFT JOIN organization_members AS om
                    ON om.organization_id = p.organization_id AND om.user_id = ?
                WHERE (
                    p.organization_id IS NULL
                    AND (p.owner_user_id = ? OR pm.user_id = ?)
                ) OR om.user_id = ?
                ORDER BY p.updated_at DESC
                LIMIT 100
                """,
                (
                    user.user_id,
                    user.user_id,
                    user.user_id,
                    user.user_id,
                    user.user_id,
                    user.user_id,
                    user.user_id,
                ),
            ).fetchall()
        projects = [self._project(row) for row in rows]
        return ProjectListResponse(projects=projects, total=len(projects))

    def get_project(self, project_id: str, user: UserResponse) -> ProjectResponse:
        with self.database.connect() as connection:
            row = self._require_project(connection, project_id, user.user_id, "read")
        return self._project(row)

    def update_project(
        self,
        project_id: str,
        user: UserResponse,
        request: UpdateProjectRequest,
        request_id: str | None,
    ) -> ProjectResponse:
        values = request.model_dump(exclude_unset=True)
        with self.database.connect() as connection:
            self._require_project(connection, project_id, user.user_id, "manage")
            assignments: list[str] = []
            parameters: list[object] = []
            for field in ("name", "description", "run_mode", "default_model"):
                if field in values:
                    assignments.append(f"{field} = ?")
                    value = values[field]
                    parameters.append(value.strip() if field == "name" else value)
            if "archived" in values:
                assignments.append("archived_at = ?")
                parameters.append(datetime_text(utc_now()) if values["archived"] else None)
            assignments.append("updated_at = ?")
            parameters.append(datetime_text(utc_now()))
            parameters.append(project_id)
            connection.execute(
                f"UPDATE projects SET {', '.join(assignments)} WHERE project_id = ?",
                parameters,
            )
            self._audit(
                connection,
                actor_user_id=user.user_id,
                action="project.updated",
                resource_type="project",
                resource_id=project_id,
                project_id=project_id,
                request_id=request_id,
                detail={"fields": ",".join(sorted(values))},
            )
            row = self._project_access_row(connection, project_id, user.user_id)
        return self._project(row)

    def delete_project(
        self,
        project_id: str,
        user: UserResponse,
        project_name: str,
        request_id: str | None,
    ) -> None:
        task_ids: set[str] = set()
        with self.database.connect() as connection:
            row = self._require_project(connection, project_id, user.user_id, "delete")
            if not constant_time_text_equal(str(row["name"]), project_name):
                raise SecurityError("项目名称确认不匹配，未执行删除。")
            placeholders = ", ".join("?" for _status in ACTIVE_TASK_STATUSES)
            active_tasks = connection.execute(
                f"""
                SELECT COUNT(*) FROM agent_tasks
                WHERE project_id = ? AND status IN ({placeholders})
                """,
                (project_id, *ACTIVE_TASK_STATUSES),
            ).fetchone()[0]
            if active_tasks:
                raise SecurityConflictError("请先取消或完成项目中的活动任务。")
            task_rows = connection.execute(
                "SELECT task_id FROM agent_tasks WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            task_ids = {str(task["task_id"]) for task in task_rows}
            self._audit(
                connection,
                actor_user_id=user.user_id,
                action="project.deleted",
                resource_type="project",
                resource_id=project_id,
                project_id=project_id,
                request_id=request_id,
                detail={"name": row["name"], "irreversible": True},
            )
            connection.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        discard_workflow_caches(task_ids)

    def list_members(
        self,
        project_id: str,
        user: UserResponse,
    ) -> ProjectMemberListResponse:
        with self.database.connect() as connection:
            project = self._require_project(connection, project_id, user.user_id, "read")
            rows = connection.execute(
                """
                SELECT u.user_id, u.email, u.display_name, 'owner' AS role, p.created_at
                FROM projects AS p JOIN users AS u ON u.user_id = p.owner_user_id
                WHERE p.project_id = ?
                UNION ALL
                SELECT u.user_id, u.email, u.display_name, pm.role, pm.created_at
                FROM project_members AS pm JOIN users AS u ON u.user_id = pm.user_id
                WHERE pm.project_id = ?
                ORDER BY role, email
                """,
                (project["project_id"], project["project_id"]),
            ).fetchall()
        members = [
            ProjectMemberResponse(
                user_id=row["user_id"],
                email=row["email"],
                display_name=row["display_name"],
                role=row["role"],
                created_at=required_datetime(row["created_at"]),
            )
            for row in rows
        ]
        return ProjectMemberListResponse(members=members, total=len(members))

    def add_member(
        self,
        project_id: str,
        user: UserResponse,
        request: AddProjectMemberRequest,
        request_id: str | None,
    ) -> ProjectMemberResponse:
        self._validate_email(request.email)
        with self.database.connect() as connection:
            project = self._require_project(connection, project_id, user.user_id, "manage")
            member = connection.execute(
                "SELECT * FROM users WHERE email = ? AND disabled_at IS NULL",
                (request.email,),
            ).fetchone()
            if member is None:
                raise SecurityNotFoundError("待添加的用户不存在。")
            if member["user_id"] == project["owner_user_id"]:
                raise SecurityConflictError("项目所有者无需重复添加为成员。")
            now = datetime_text(utc_now())
            if project["organization_id"] is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO organization_members (
                        organization_id, user_id, role, created_at, updated_at
                    ) VALUES (?, ?, 'member', ?, ?)
                    """,
                    (project["organization_id"], member["user_id"], now, now),
                )
            try:
                connection.execute(
                    """
                    INSERT INTO project_members (
                        project_id, user_id, role, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, member["user_id"], request.role, now, now),
                )
            except sqlite3.IntegrityError as error:
                raise SecurityConflictError("该用户已经是项目成员。") from error
            self._audit(
                connection,
                actor_user_id=user.user_id,
                action="project.member_added",
                resource_type="project_member",
                resource_id=member["user_id"],
                project_id=project_id,
                request_id=request_id,
                detail={"role": request.role},
            )
        return ProjectMemberResponse(
            user_id=member["user_id"],
            email=member["email"],
            display_name=member["display_name"],
            role=request.role,
            created_at=required_datetime(now),
        )

    def update_member(
        self,
        project_id: str,
        member_user_id: str,
        user: UserResponse,
        role: Literal["admin", "editor", "viewer"],
        request_id: str | None,
    ) -> ProjectMemberResponse:
        with self.database.connect() as connection:
            self._require_project(connection, project_id, user.user_id, "manage")
            now = datetime_text(utc_now())
            cursor = connection.execute(
                """
                UPDATE project_members SET role = ?, updated_at = ?
                WHERE project_id = ? AND user_id = ?
                """,
                (role, now, project_id, member_user_id),
            )
            if cursor.rowcount == 0:
                raise SecurityNotFoundError("项目成员不存在或不能修改所有者角色。")
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (member_user_id,),
            ).fetchone()
            self._audit(
                connection,
                actor_user_id=user.user_id,
                action="project.member_role_updated",
                resource_type="project_member",
                resource_id=member_user_id,
                project_id=project_id,
                request_id=request_id,
                detail={"role": role},
            )
        return ProjectMemberResponse(
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            role=role,
            created_at=required_datetime(now),
        )

    def remove_member(
        self,
        project_id: str,
        member_user_id: str,
        user: UserResponse,
        request_id: str | None,
    ) -> None:
        with self.database.connect() as connection:
            self._require_project(connection, project_id, user.user_id, "manage")
            cursor = connection.execute(
                "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
                (project_id, member_user_id),
            )
            if cursor.rowcount == 0:
                raise SecurityNotFoundError("项目成员不存在或不能删除项目所有者。")
            self._audit(
                connection,
                actor_user_id=user.user_id,
                action="project.member_removed",
                resource_type="project_member",
                resource_id=member_user_id,
                project_id=project_id,
                request_id=request_id,
            )

    def _audit_event(self, row: sqlite3.Row) -> AuditEventResponse:
        return AuditEventResponse(
            audit_id=row["audit_id"],
            actor_user_id=row["actor_user_id"],
            action=row["action"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            project_id=row["project_id"],
            organization_id=row["organization_id"],
            request_id=row["request_id"],
            detail=json.loads(row["detail_json"]),
            created_at=required_datetime(row["created_at"]),
        )

    def list_user_audit(self, user: UserResponse) -> AuditListResponse:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_logs WHERE actor_user_id = ?
                ORDER BY created_at DESC LIMIT 100
                """,
                (user.user_id,),
            ).fetchall()
        events = [self._audit_event(row) for row in rows]
        return AuditListResponse(events=events, total=len(events))

    def list_project_audit(
        self,
        project_id: str,
        user: UserResponse,
    ) -> AuditListResponse:
        with self.database.connect() as connection:
            self._require_project(connection, project_id, user.user_id, "manage")
            rows = connection.execute(
                """
                SELECT * FROM audit_logs WHERE project_id = ? OR (
                    resource_type = 'project' AND resource_id = ?
                ) ORDER BY created_at DESC LIMIT 100
                """,
                (project_id, project_id),
            ).fetchall()
        events = [self._audit_event(row) for row in rows]
        return AuditListResponse(events=events, total=len(events))


security_service = SecurityService(settings)
