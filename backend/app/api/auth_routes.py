"""Login and identity.

Credentials are exchanged for Cognito tokens server-side so the browser never
implements SRP. The password is never logged, never stored and never returned.
"""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentPrincipal
from app.core.audit import Actions, record
from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)
router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    id_token: str
    expires_in: int
    token_type: str = "Bearer"  # noqa: S105 - a scheme name, not a credential


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    settings: Settings = request.app.state.settings
    client = boto3.client("cognito-idp", region_name=settings.cognito_region)

    try:
        result = client.initiate_auth(
            ClientId=settings.cognito_client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": body.email, "PASSWORD": body.password},
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        await record(
            Actions.AUTH_FAILURE,
            actor_id=body.email,
            outcome="denied",
            reason=code,
            ip=request.client.host if request.client else "",
        )
        # Never disclose whether the account exists or the password was wrong.
        raise AuthenticationError(f"cognito {code}") from exc

    challenge = result.get("ChallengeName")
    if challenge:
        # e.g. NEW_PASSWORD_REQUIRED. Handled by the admin during seeding.
        raise AuthenticationError(
            f"challenge {challenge}",
            public_message="This account requires setup. Contact an administrator.",
        )

    auth = result["AuthenticationResult"]
    log_event(logger, logging.INFO, "auth.login_success", user=body.email)
    return LoginResponse(
        access_token=auth["AccessToken"],
        id_token=auth["IdToken"],
        expires_in=int(auth.get("ExpiresIn", 3600)),
    )


@router.get("/me")
async def me(principal: CurrentPrincipal) -> dict[str, Any]:
    """Who the server believes you are, and what you may reach.

    This is the RBAC surface made visible: role and departments come from the
    database, not from the token, so what you see here is exactly what the
    retrieval filter will enforce.
    """
    return {
        "user_id": principal.user_id,
        "email": principal.email,
        "tenant_id": principal.tenant_id,
        "role": principal.role,
        "departments": list(principal.departments),
        "is_admin": principal.is_admin,
        "permission_scope_hash": principal.scope_hash(),
    }
