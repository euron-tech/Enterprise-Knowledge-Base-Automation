"""Tenancy isolation — the most important suite in the repo."""

from __future__ import annotations

import pytest

from app.auth.principal import Principal
from app.core.constants import INSUFFICIENT_EVIDENCE
from app.core.errors import TenancyError
from app.rag.filters import assert_tenant_scoped, build_filter
from tests.conftest import TENANT_A, TENANT_B, auth_headers, seed_chunk

pytestmark = pytest.mark.security


# ---------------------------------------------------------------- chokepoint
def test_filter_without_tenant_raises():
    bad = Principal(user_id="u", tenant_id="", role="user", departments=("hr",))
    with pytest.raises(TenancyError):
        build_filter(bad)


def test_filter_with_whitespace_tenant_raises():
    bad = Principal(user_id="u", tenant_id="   ", role="user", departments=("hr",))
    with pytest.raises(TenancyError):
        build_filter(bad)


def test_filter_always_carries_tenant(principal_a):
    flt = build_filter(principal_a)
    assert_tenant_scoped(flt, TENANT_A)  # raises if absent
    with pytest.raises(TenancyError):
        assert_tenant_scoped(flt, TENANT_B)


def test_ungranted_department_raises(principal_a):
    with pytest.raises(TenancyError):
        build_filter(principal_a, department="finance")


def test_user_with_no_grants_raises(principal_a):
    nodept = Principal(user_id="n", tenant_id=TENANT_A, role="user", departments=())
    with pytest.raises(TenancyError):
        build_filter(nodept)


def test_granted_department_allowed(principal_a):
    flt = build_filter(principal_a, department="hr")
    assert_tenant_scoped(flt, TENANT_A)


# ---------------------------------------------------------------- end to end
async def test_cross_tenant_chunk_never_retrieved(app, client):
    await seed_chunk(
        app,
        tenant_id=TENANT_B,
        department="hr",
        text="Globex parental leave is 26 weeks at full pay",
        document_id="doc-b",
    )
    r = await client.post(
        "/search",
        json={"query": "parental leave weeks"},
        headers=auth_headers("user-a"),
    )
    assert r.status_code == 200
    assert r.json()["count"] == 0, "tenant A must never see tenant B content"


async def test_cross_tenant_chat_returns_refusal(app, client):
    await seed_chunk(
        app,
        tenant_id=TENANT_B,
        department="hr",
        text="Globex parental leave is 26 weeks",
        document_id="doc-b",
    )
    r = await client.post(
        "/chat",
        json={"question": "What is the parental leave policy?"},
        headers=auth_headers("user-a"),
    )
    body = r.json()
    assert body["answer"] == INSUFFICIENT_EVIDENCE
    assert body["retrieved_chunks"] == []
    assert body["citations"] == []


async def test_own_tenant_chunk_is_retrieved(app, client):
    await seed_chunk(
        app,
        tenant_id=TENANT_A,
        department="hr",
        text="Acme parental leave is 18 weeks at full pay",
    )
    r = await client.post(
        "/search",
        json={"query": "Acme parental leave weeks full pay"},
        headers=auth_headers("user-a"),
    )
    assert r.json()["count"] >= 1


async def test_ungranted_department_search_denied(client):
    r = await client.post(
        "/search",
        json={"query": "budget", "department": "finance"},
        headers=auth_headers("user-a"),
    )
    assert r.status_code == 403


async def test_department_isolation_within_tenant(app, client):
    await seed_chunk(
        app,
        tenant_id=TENANT_A,
        department="finance",
        text="Finance travel expense cap is 500 dollars",
        document_id="doc-fin",
    )
    r = await client.post(
        "/search",
        json={"query": "travel expense cap dollars"},
        headers=auth_headers("user-a"),
    )
    ids = [x["document_id"] for x in r.json()["results"]]
    assert "doc-fin" not in ids, "hr-only user must not see finance content"


async def test_admin_sees_own_tenant_only(app, client):
    await seed_chunk(
        app,
        tenant_id=TENANT_B,
        department="hr",
        text="Globex secret",
        document_id="doc-b",
    )
    await seed_chunk(
        app,
        tenant_id=TENANT_A,
        department="finance",
        text="Acme finance travel cap",
        document_id="doc-fin",
    )
    r = await client.post(
        "/search", json={"query": "travel cap secret"}, headers=auth_headers("admin-a")
    )
    ids = [x["document_id"] for x in r.json()["results"]]
    assert "doc-b" not in ids
    assert "doc-fin" in ids  # admin spans departments, never tenants


# ---------------------------------------------------------------- authorization
async def test_user_cannot_read_admin_metrics(client):
    r = await client.get("/admin/metrics", headers=auth_headers("user-a"))
    assert r.status_code == 403


async def test_admin_can_read_metrics(client):
    r = await client.get("/admin/metrics", headers=auth_headers("admin-a"))
    assert r.status_code == 200
    assert r.json()["tenant_id"] == TENANT_A


async def test_non_owner_cannot_delete(app, client):
    from app.db.models import Document
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        s.add(
            Document(
                id="doc-owned",
                tenant_id=TENANT_A,
                department="hr",
                name="x.pdf",
                s3_uri="s3://x",
                mime_type="application/pdf",
                checksum="c1",
                owner_id="admin-a",
            )
        )
        await s.commit()

    r = await client.delete("/documents/doc-owned", headers=auth_headers("user-a"))
    assert r.status_code == 403


async def test_cross_tenant_delete_is_not_found(app, client):
    from app.db.models import Document
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        s.add(
            Document(
                id="doc-of-b",
                tenant_id=TENANT_B,
                department="hr",
                name="b.pdf",
                s3_uri="s3://x",
                mime_type="application/pdf",
                checksum="c2",
                owner_id="user-b",
            )
        )
        await s.commit()

    r = await client.delete("/documents/doc-of-b", headers=auth_headers("user-a"))
    assert r.status_code == 404  # existence is not disclosed


async def test_client_cannot_spoof_tenant_in_body(client):
    r = await client.post(
        "/chat",
        json={"question": "hi", "tenant_id": TENANT_B},
        headers=auth_headers("user-a"),
    )
    assert r.status_code == 422  # extra="forbid" rejects it outright
