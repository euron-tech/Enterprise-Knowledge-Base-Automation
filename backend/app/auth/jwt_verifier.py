"""Cognito JWT verification. Every check is mandatory; a missing one is a vulnerability."""

from __future__ import annotations

import time
from typing import Any

import jwt
from jwt import PyJWKClient

from app.core.config import Settings
from app.core.errors import AuthenticationError

ALLOWED_ALGORITHMS = ["RS256"]  # never "none", never HS*
LEEWAY_SECONDS = 60


class CognitoVerifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jwk_client: PyJWKClient | None = None
        self._jwks_last_fetch = 0.0

    @property
    def issuer(self) -> str:
        return (
            f"https://cognito-idp.{self.settings.cognito_region}.amazonaws.com/"
            f"{self.settings.cognito_user_pool_id}"
        )

    def _client(self) -> PyJWKClient:
        if self._jwk_client is None:
            self._jwk_client = PyJWKClient(
                f"{self.issuer}/.well-known/jwks.json", cache_keys=True
            )
        return self._jwk_client

    def verify(self, token: str) -> dict[str, Any]:
        if not token or token.count(".") != 2:
            raise AuthenticationError("malformed token")

        # Reject alg confusion before touching any key material.
        try:
            header = jwt.get_unverified_header(token)
        except Exception as exc:  # noqa: BLE001
            raise AuthenticationError(f"unreadable header: {exc}") from exc
        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise AuthenticationError(f"disallowed alg: {alg}")

        if self.settings.auth_dev_mode:
            return self._verify_dev(token)

        try:
            key = self._client().get_signing_key_from_jwt(token).key
        except Exception as exc:  # noqa: BLE001
            raise AuthenticationError(f"jwks lookup failed: {exc}") from exc

        return self._decode(token, key)

    def _verify_dev(self, token: str) -> dict[str, Any]:
        """Dev only: tokens signed by the local test key. Never reachable in prod."""
        from app.auth.dev_keys import dev_public_key

        return self._decode(token, dev_public_key())

    def _decode(self, token: str, key: Any) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self.settings.cognito_client_id or None,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": bool(self.settings.cognito_client_id),
                    "verify_iss": True,
                    "require": ["exp", "iss", "sub"],
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("token expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthenticationError("bad audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthenticationError("bad issuer") from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthenticationError("bad signature") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"invalid token: {exc}") from exc

        token_use = claims.get("token_use")
        if token_use not in (None, "id", "access"):
            raise AuthenticationError(f"bad token_use: {token_use}")
        if claims.get("exp", 0) < time.time() - LEEWAY_SECONDS:
            raise AuthenticationError("token expired")
        return claims
