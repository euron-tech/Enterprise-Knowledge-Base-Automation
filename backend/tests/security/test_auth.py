"""Authentication matrix. Every failure mode, individually."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.jwt_verifier import CognitoVerifier
from app.core.errors import AuthenticationError
from tests.conftest import auth_headers, make_token

pytestmark = pytest.mark.security


async def test_valid_token_is_accepted(client):
    r = await client.post("/chat", json={"question": "hello"}, headers=auth_headers("user-a"))
    assert r.status_code != 401


async def test_missing_token_rejected(client):
    r = await client.post("/chat", json={"question": "hello"})
    assert r.status_code == 401
    assert r.json()["error"] == "authentication_failed"


async def test_malformed_token_rejected(client):
    r = await client.post(
        "/chat", json={"question": "hi"}, headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert r.status_code == 401


async def test_non_bearer_scheme_rejected(client):
    r = await client.post("/chat", json={"question": "hi"}, headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


async def test_expired_token_rejected(client):
    r = await client.post(
        "/chat", json={"question": "hi"}, headers=auth_headers("user-a", expires_in=-7200)
    )
    assert r.status_code == 401


async def test_wrong_issuer_rejected(client):
    r = await client.post(
        "/chat",
        json={"question": "hi"},
        headers=auth_headers("user-a", issuer="https://evil.example.com/pool"),
    )
    assert r.status_code == 401


async def test_wrong_audience_rejected(client):
    r = await client.post(
        "/chat", json={"question": "hi"}, headers=auth_headers("user-a", audience="other-client")
    )
    assert r.status_code == 401


async def test_alg_none_rejected(settings):
    """The classic. An unsigned token must never be accepted."""
    import jwt as pyjwt

    token = pyjwt.encode({"sub": "user-a"}, key="", algorithm="none")
    with pytest.raises(AuthenticationError):
        CognitoVerifier(settings).verify(token)


async def test_hs256_signed_with_public_key_rejected(settings):
    """Algorithm confusion: HS256 signed with the RSA public key must not verify.

    Hand-rolled because PyJWT refuses to *encode* this attack — we still have to
    prove our verifier refuses to *decode* it.
    """
    import base64
    import hashlib
    import hmac
    import json

    from cryptography.hazmat.primitives import serialization

    from app.auth.dev_keys import dev_public_key

    pub_pem = dev_public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"sub": "user-a", "exp": 9999999999}).encode())
    signing_input = header + b"." + payload
    sig = b64(hmac.new(pub_pem, signing_input, hashlib.sha256).digest())
    token = (signing_input + b"." + sig).decode()

    with pytest.raises(AuthenticationError, match="disallowed alg"):
        CognitoVerifier(settings).verify(token)


async def test_signature_from_wrong_key_rejected(settings):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = make_token("user-a", key=other)
    with pytest.raises(AuthenticationError):
        CognitoVerifier(settings).verify(token)


async def test_unknown_user_rejected(client):
    r = await client.post("/chat", json={"question": "hi"}, headers=auth_headers("no-such-user"))
    assert r.status_code == 401


async def test_disabled_user_rejected(client):
    r = await client.post("/chat", json={"question": "hi"}, headers=auth_headers("user-disabled"))
    assert r.status_code == 401


async def test_error_body_is_generic_and_has_correlation_id(client):
    r = await client.post("/chat", json={"question": "hi"})
    body = r.json()
    assert body["message"] == "Authentication failed."
    assert body["correlation_id"]
    # never leak which check failed
    assert "issuer" not in str(body).lower()
    assert "signature" not in str(body).lower()
