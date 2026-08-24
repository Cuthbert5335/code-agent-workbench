"""Account authentication endpoints backed by durable, revocable sessions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.schemas.security import (
    AuthResponse,
    AuthStatusResponse,
    ChangePasswordRequest,
    DeleteAccountRequest,
    DeleteAccountStatusResponse,
    LoginRequest,
    RegisterRequest,
    UserResponse,
)
from app.services.security import (
    AuthenticatedUser,
    AuthenticationError,
    LoginRateLimitError,
    SecurityError,
    security_service,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])
bearer_scheme = HTTPBearer(auto_error=False)


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def security_http_error(error: SecurityError) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
    if isinstance(error, LoginRateLimitError):
        headers = {
            "Retry-After": str(security_service.settings.login_failure_window_seconds),
        }
    return HTTPException(status_code=error.status_code, detail=error.detail, headers=headers)


def get_authenticated_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise security_http_error(AuthenticationError("请先登录。"))
    try:
        return security_service.authenticate(credentials.credentials)
    except SecurityError as error:
        raise security_http_error(error) from error


CurrentAuth = Annotated[AuthenticatedUser, Depends(get_authenticated_user)]


def get_optional_authenticated_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> AuthenticatedUser | None:
    """Accept anonymous local compatibility calls but validate any supplied token."""

    if credentials is None:
        return None
    if credentials.scheme.casefold() != "bearer":
        raise security_http_error(AuthenticationError("请先登录。"))
    try:
        return security_service.authenticate(credentials.credentials)
    except SecurityError as error:
        raise security_http_error(error) from error


OptionalAuth = Annotated[AuthenticatedUser | None, Depends(get_optional_authenticated_user)]


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(payload: RegisterRequest, request: Request) -> UserResponse:
    """Create an account while persisting only a salted password hash."""

    try:
        return security_service.register(payload, request_id(request))
    except SecurityError as error:
        raise security_http_error(error) from error


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
) -> AuthResponse:
    """Issue a bounded session token after rate-limited credential checks."""

    client_address = request.client.host if request.client else "unknown"
    try:
        result = security_service.login(
            payload.email,
            payload.password,
            client_address,
            request_id(request),
        )
    except SecurityError as error:
        raise security_http_error(error) from error
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/me", response_model=UserResponse)
async def get_me(authenticated: CurrentAuth) -> UserResponse:
    """Return the current account without exposing session storage details."""

    return authenticated.user


@router.post("/logout", response_model=AuthStatusResponse)
async def logout(
    authenticated: CurrentAuth,
    request: Request,
) -> AuthStatusResponse:
    """Revoke the presented bearer token immediately."""

    security_service.logout(authenticated, request_id(request))
    return AuthStatusResponse(status="logged_out")


@router.post("/change-password", response_model=AuthStatusResponse)
async def change_password(
    payload: ChangePasswordRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> AuthStatusResponse:
    """Change the password and revoke every active session for the account."""

    try:
        security_service.change_password(
            authenticated,
            payload.current_password,
            payload.new_password,
            request_id(request),
        )
    except SecurityError as error:
        raise security_http_error(error) from error
    return AuthStatusResponse(status="password_changed")


@router.delete("/account", response_model=DeleteAccountStatusResponse)
async def delete_account(
    payload: DeleteAccountRequest,
    authenticated: CurrentAuth,
    request: Request,
) -> DeleteAccountStatusResponse:
    """Delete the account, owned projects, sessions, tasks, and project data."""

    try:
        user_id = security_service.delete_account(
            authenticated,
            email=payload.email,
            current_password=payload.current_password,
            request_id=request_id(request),
        )
    except SecurityError as error:
        raise security_http_error(error) from error
    return DeleteAccountStatusResponse(deleted_user_id=user_id)
