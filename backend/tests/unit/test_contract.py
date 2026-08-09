"""Response contract, citation validation, cache keys, ingestion, rate limits."""

from __future__ import annotations

import pytest

from app.clients.vectorstore import Chunk
from app.core.constants import INSUFFICIENT_EVIDENCE
from app.rag import citations as cg
from app.rag.cache import cache_key, normalize_question
from tests.conftest import TENANT_A, auth_headers, seed_chunk

CONTRACT_FIELDS = {
    "answer",
    "citations",
    "retrieved_chunks",
    "model_used",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
    "latency_ms",
    "cache_hit",
    "trace_id",
    "confidence",
}


def _chunk(cid: str, score: float = 0.9) -> Chunk:
    return Chunk(
        chunk_id=cid,
        document_id="d1",
        document_name="Handbook.pdf",
        text="text",
        score=score,
        page_number=3,
        source_uri="s3://x",
        document_version=2,
        tenant_id=TENANT_A,
        department="hr",
    )


# ------------------------------------------------------------------ refusal string
def test_refusal_string_is_byte_exact():
    assert INSUFFICIENT_EVIDENCE == (
        "I could not find enough evidence in the approved documents to answer this question."
    )


# ------------------------------------------------------------------ citations
def test_fabricated_citation_is_stripped():
    result = cg.validate("Leave is 18 weeks [made-up-id].", [_chunk("real-1")])
    assert result.stripped == ["made-up-id"]
    assert result.citations == []
    assert "made-up-id" not in result.text


def test_valid_citation_kept_with_provenance():
    result = cg.validate("Leave is 18 weeks [real-1].", [_chunk("real-1")])
    assert len(result.citations) == 1
    c = result.citations[0]
    assert c["chunk_id"] == "real-1"
    assert c["page_number"] == 3
    assert c["document_version"] == 2


def test_mixed_citations_keep_only_real_ones():
    result = cg.validate("A [real-1] and B [fake-9].", [_chunk("real-1")])
    assert [c["chunk_id"] for c in result.citations] == ["real-1"]
    assert result.stripped == ["fake-9"]


def test_confidence_zero_without_chunks():
    assert cg.score_confidence([], 0.0, stripped=0, guardrail_hits=0) == 0.0


def test_confidence_penalised_by_stripped_citations():
    high = cg.score_confidence([_chunk("a")], 1.0, stripped=0, guardrail_hits=0)
    low = cg.score_confidence([_chunk("a")], 1.0, stripped=3, guardrail_hits=0)
    assert low < high


async def test_fabricated_only_citation_becomes_refusal(app, client, fake_euri):
    await seed_chunk(
        app, tenant_id=TENANT_A, department="hr", text="Leave is 18 weeks."
    )
    fake_euri.queue_tool_call("hybrid_search", {"query": "leave"})
    fake_euri.queue_stop()
    fake_euri.queue_answer("Leave is unlimited [totally-invented-chunk].")
    r = await client.post(
        "/chat", json={"question": "How much leave?"}, headers=auth_headers("user-a")
    )
    body = r.json()
    assert body["answer"] == INSUFFICIENT_EVIDENCE
    assert body["citations"] == []


# ------------------------------------------------------------------ contract
async def test_all_contract_fields_on_answer(app, client, fake_euri):
    cid = await seed_chunk(
        app, tenant_id=TENANT_A, department="hr", text="Leave is 18 weeks."
    )
    fake_euri.queue_tool_call("hybrid_search", {"query": "leave"})
    fake_euri.queue_stop()
    fake_euri.queue_answer(f"Leave is 18 weeks. [{cid}]")
    body = (
        await client.post(
            "/chat", json={"question": "leave?"}, headers=auth_headers("user-a")
        )
    ).json()
    assert CONTRACT_FIELDS <= set(body)


async def test_all_contract_fields_on_refusal(app, client):
    body = (
        await client.post(
            "/chat",
            json={"question": "unknowable question"},
            headers=auth_headers("user-a"),
        )
    ).json()
    assert CONTRACT_FIELDS <= set(body)
    assert body["answer"] == INSUFFICIENT_EVIDENCE
    assert body["confidence"] == 0.0


async def test_all_contract_fields_on_cache_hit(app, client, fake_euri):
    cid = await seed_chunk(
        app, tenant_id=TENANT_A, department="hr", text="Leave is 18 weeks."
    )
    for _ in range(2):
        fake_euri.queue_tool_call("hybrid_search", {"query": "leave"})
        fake_euri.queue_stop()
        fake_euri.queue_answer(f"Leave is 18 weeks. [{cid}]")

    q = {"question": "How much leave is granted?"}
    first = (await client.post("/chat", json=q, headers=auth_headers("user-a"))).json()
    second = (await client.post("/chat", json=q, headers=auth_headers("user-a"))).json()
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert CONTRACT_FIELDS <= set(second)


# ------------------------------------------------------------------ cache keys
def test_cache_key_differs_across_permission_scope(settings, principal_a, admin_a):
    a = cache_key("q", principal_a, settings, kb_version=1, tool_registry_hash="h")
    b = cache_key("q", admin_a, settings, kb_version=1, tool_registry_hash="h")
    assert a != b, "different permission scopes must never share a cache entry"


def test_cache_key_differs_across_tenant(settings, principal_a, principal_b):
    a = cache_key("q", principal_a, settings, kb_version=1, tool_registry_hash="h")
    b = cache_key("q", principal_b, settings, kb_version=1, tool_registry_hash="h")
    assert a != b


def test_cache_key_changes_with_kb_version(settings, principal_a):
    a = cache_key("q", principal_a, settings, kb_version=1, tool_registry_hash="h")
    b = cache_key("q", principal_a, settings, kb_version=2, tool_registry_hash="h")
    assert a != b


def test_cache_key_changes_with_registry(settings, principal_a):
    a = cache_key("q", principal_a, settings, kb_version=1, tool_registry_hash="h1")
    b = cache_key("q", principal_a, settings, kb_version=1, tool_registry_hash="h2")
    assert a != b


def test_question_normalization():
    assert normalize_question("  How   MANY days?  ") == "how many days?"


# ------------------------------------------------------------------ ingestion
async def test_ingestion_writes_all_mandatory_payload_fields(app):
    from app.core.constants import MANDATORY_PAYLOAD_FIELDS

    result = await app.state.ingestion.ingest(
        data=b"# Policy\n\nLeave is 18 weeks at full pay.\n",
        filename="policy.md",
        mime="text/markdown",
        tenant_id=TENANT_A,
        department="hr",
        owner_id="user-a",
    )
    assert result.chunks_written >= 1
    points, _ = await app.state.vectors.client.scroll(
        collection_name=app.state.vectors.collection, limit=10, with_payload=True
    )
    for p in points:
        for field in MANDATORY_PAYLOAD_FIELDS:
            assert p.payload.get(field) not in (None, ""), f"{field} missing"


async def test_upsert_rejects_missing_mandatory_field(app):
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        await app.state.vectors.upsert(
            [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "vector": [0.0] * 64,
                    "payload": {"tenant_id": TENANT_A},
                }
            ]
        )


async def test_duplicate_upload_is_a_noop(app, client):
    files = {"file": ("p.md", b"# Policy\n\nLeave is 18 weeks.\n", "text/markdown")}
    first = await client.post(
        "/documents/upload",
        files=files,
        data={"department": "hr"},
        headers=auth_headers("user-a"),
    )
    assert first.json()["status"] == "completed"
    files2 = {"file": ("p.md", b"# Policy\n\nLeave is 18 weeks.\n", "text/markdown")}
    second = await client.post(
        "/documents/upload",
        files=files2,
        data={"department": "hr"},
        headers=auth_headers("user-a"),
    )
    assert second.json()["status"] == "duplicate"
    assert second.json()["chunks_written"] == 0


async def test_chunker_enforces_local_ceiling(app, settings):
    """We must not depend on the gateway to reject oversized input — it returns 200."""
    huge = ("policy " * 20000).encode()
    result = await app.state.ingestion.ingest(
        data=huge,
        filename="big.txt",
        mime="text/plain",
        tenant_id=TENANT_A,
        department="hr",
        owner_id="user-a",
    )
    limit = settings.max_chunk_tokens * 4
    for call in app.state.euri.embed_calls:
        for text in call:
            assert len(text) <= limit, "a chunk exceeded the local ceiling"
    assert result.chunks_written > 1


async def test_data_uri_is_never_embedded_as_text(app):
    """The verified silent-failure mode must be refused, not silently indexed."""
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        await app.state.euri.embed(["data:image/png;base64,iVBORw0KGgo="])


async def test_image_is_bridged_to_text(app):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000020000000208020000"
        "00fdd49a730000000f49444154789c63f8cf00000301010018dd8db0"
        "0000000049454e44ae426082"
    )
    result = await app.state.ingestion.ingest(
        data=png,
        filename="diagram.png",
        mime="image/png",
        tenant_id=TENANT_A,
        department="hr",
        owner_id="user-a",
    )
    assert result.chunks_written == 1
    assert any(
        "workflow" in t.lower() for call in app.state.euri.embed_calls for t in call
    )


# ------------------------------------------------------------------ api surface
async def test_security_headers_present(client):
    r = await client.get("/healthz")
    for h in ("X-Content-Type-Options", "X-Frame-Options", "Content-Security-Policy"):
        assert h in r.headers


async def test_correlation_id_echoed(client):
    r = await client.get("/healthz", headers={"X-Correlation-ID": "my-trace-123"})
    assert r.headers["X-Correlation-ID"] == "my-trace-123"


async def test_correlation_id_minted_when_absent(client):
    r = await client.get("/healthz")
    assert r.headers.get("X-Correlation-ID")


async def test_trace_id_matches_correlation_id(client):
    r = await client.post(
        "/chat",
        json={"question": "anything"},
        headers={**auth_headers("user-a"), "X-Correlation-ID": "trace-abc"},
    )
    assert r.json()["trace_id"] == "trace-abc"
    assert r.headers["X-Correlation-ID"] == "trace-abc"


async def test_rate_limit_enforced_on_chat(client, settings):
    limit, _ = settings.rate_limits["/chat"]
    statuses = []
    for _ in range(limit + 3):
        r = await client.post(
            "/chat", json={"question": "hello there"}, headers=auth_headers("user-a")
        )
        statuses.append(r.status_code)
    assert 429 in statuses
    assert statuses.count(429) >= 2


async def test_unknown_field_rejected(client):
    r = await client.post(
        "/chat",
        json={"question": "hi", "role": "admin"},
        headers=auth_headers("user-a"),
    )
    assert r.status_code == 422


async def test_health_needs_no_auth(client):
    assert (await client.get("/healthz")).status_code == 200
