"""Dependencies: principal resolution, RBAC, rate limiting. No handler reads raw claims."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select

from app.auth.jwt_verifier import CognitoVerifier
from app.auth.principal import Principal, Role
from app.core.audit import Actions, record
from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError, AuthorizationError
from app.core.logging import correlation_id_var, tenant_id_var, user_id_var
from app.core.ratelimit import RateLimiter
from app.db.models import User
from app.db.session import get_sessionmaker


def get_config() -> Settings:
    return get_settings()


async def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_config),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("missing bearer token")
    token = authorization.split(" ", 1)[1].strip()

    try:
        claims = CognitoVerifier(settings).verify(token)
    except AuthenticationError:
        await record(
            Actions.AUTH_FAILURE,
            outcome="denied",
            ip=request.client.host if request.client else "",
            path=request.url.path,
        )
        raise

    sub = claims.get("sub", "")
    if not sub:
        raise AuthenticationError("token has no subject")

    # Grants come from the database, never from the token.
    async with get_sessionmaker()() as session:
        user = (
            await session.execute(select(User).where(User.id == sub))
        ).scalar_one_or_none()

    if user is None or user.status != "active":
        await record(
            Actions.AUTH_FAILURE, actor_id=sub, outcome="denied", reason="unknown_user"
        )
        raise AuthenticationError("unknown or inactive user")

    principal = Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,  # type: ignore[arg-type]
        departments=tuple(user.departments or ()),
        email=user.email,
        correlation_id=correlation_id_var.get(),
    )
    tenant_id_var.set(principal.tenant_id)
    user_id_var.set(principal.user_id)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_role(*roles: Role) -> Callable:
    async def _dep(principal: CurrentPrincipal) -> Principal:
        if principal.role not in roles:
            await record(
                Actions.AUTHZ_DENIED,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                outcome="denied",
                required=list(roles),
                actual=principal.role,
            )
            raise AuthorizationError(f"requires role in {roles}")
        return principal

    return _dep


def rate_limit(route: str) -> Callable:
    async def _dep(request: Request, principal: CurrentPrincipal) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        await limiter.check(route, principal.user_id)

    return _dep


def public_rate_limit(route: str) -> Callable:
    async def _dep(request: Request) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        ident = request.client.host if request.client else "anon"
        await limiter.check(route, ident)

    return _dep
