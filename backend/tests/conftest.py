"""Test fixtures. No network, no AWS, no real gateway — fakes at every seam."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("AUTH_DEV_MODE", "true")
os.environ.setdefault("COGNITO_USER_POOL_ID", "test-pool")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client")
os.environ.setdefault("QDRANT_URL", ":memory:")
os.environ.setdefault("EURI_API_KEY", "test-key-not-real")

from app.agent.registry import REGISTRY  # noqa: E402
from app.agent.tools import register_all  # noqa: E402
from app.auth.dev_keys import dev_private_pem  # noqa: E402
from app.auth.principal import Principal  # noqa: E402
from app.clients.euri import ChatResult  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.ratelimit import reset_memory_buckets  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.db.models import Tenant, User  # noqa: E402

TENANT_A = "tenant-aaa"
TENANT_B = "tenant-bbb"


# ------------------------------------------------------------------ fake gateway
class FakeEuri:
    """Deterministic stand-in. Vectors are stable per text so retrieval is testable."""

    def __init__(self, dims: int = 64) -> None:
        self.dims = dims
        self.chat_queue: list[ChatResult] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.embed_calls: list[list[str]] = []
        self.default_answer = "The policy grants 18 weeks. [{chunk}]"

    def _vec(self, text: str) -> list[float]:
        """Feature-hashed bag of words, L2-normalized — like the real gateway.

        Shared vocabulary genuinely raises the dot product, so retrieval tests
        exercise real ranking behaviour rather than hash noise.
        """
        import hashlib
        import math
        import re

        raw = [0.0] * self.dims
        words = re.findall(r"[a-z0-9]+", text.lower())
        for w in words:
            idx = int.from_bytes(hashlib.sha256(w.encode()).digest()[:4], "big") % self.dims
            sign = 1.0 if idx % 2 == 0 else -1.0
            raw[idx] += sign
        if not any(raw):
            raw[0] = 1.0
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]

    async def embed(self, texts: list[str], *, dimensions: int | None = None) -> list[list[float]]:
        from app.core.errors import ValidationError

        for t in texts:
            if isinstance(t, str) and t.startswith("data:") and ";base64," in t[:64]:
                raise ValidationError("refusing to embed a data URI as text")
        self.embed_calls.append(texts)
        return [self._vec(t) for t in texts]

    async def embed_one(self, text: str, *, dimensions: int | None = None) -> list[float]:
        return (await self.embed([text]))[0]

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        self.chat_calls.append({"messages": messages, **kwargs})
        if self.chat_queue:
            return self.chat_queue.pop(0)
        # No script left: behave like a planner that is done, then a generator.
        if kwargs.get("tools"):
            return ChatResult(
                content=None, tool_calls=[], model="fake-planner", finish_reason="stop"
            )
        return ChatResult(
            content=self.default_answer,
            tool_calls=[],
            model="fake-gen",
            input_tokens=10,
            output_tokens=5,
            finish_reason="stop",
        )

    def queue_tool_call(self, name: str, args: dict[str, Any]) -> None:
        import json

        self.chat_queue.append(
            ChatResult(
                content=None,
                tool_calls=[
                    {
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
                model="fake-planner",
                input_tokens=5,
                output_tokens=5,
                finish_reason="stop",
                raw_message={"role": "assistant", "content": None, "tool_calls": []},
            )
        )

    def queue_stop(self) -> None:
        self.chat_queue.append(
            ChatResult(content=None, tool_calls=[], model="fake-planner", finish_reason="stop")
        )

    def queue_answer(self, text: str) -> None:
        self.chat_queue.append(
            ChatResult(
                content=text,
                tool_calls=[],
                model="fake-gen",
                input_tokens=10,
                output_tokens=5,
                finish_reason="stop",
            )
        )

    async def estimate_cost(self, model: str, i: int, o: int) -> float:
        return (i * 0.4 + o * 1.6) / 1_000_000

    async def describe_image(self, image_b64: str, mime: str = "image/png") -> str:
        return "A diagram showing the approval workflow with three stages."

    async def transcribe(self, audio: bytes, filename: str = "a.wav") -> str:
        return "Recorded onboarding session covering the leave policy."

    async def prices(self) -> dict[str, dict[str, float]]:
        return {}

    async def aclose(self) -> None:
        return None


class _CachedStub:
    """Callable stand-in for get_settings that still exposes cache_clear()."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def __call__(self) -> Settings:
        return self._settings

    def cache_clear(self) -> None:
        return None


# ------------------------------------------------------------------ fixtures
@pytest.fixture(autouse=True)
def _reset_rate_limits() -> None:
    reset_memory_buckets()


@pytest.fixture
def settings(tmp_path) -> Settings:
    get_settings.cache_clear()
    db_file = tmp_path / f"t{uuid.uuid4().hex[:8]}.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file}"
    s = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{db_file}",
        qdrant_url=":memory:",
        auth_dev_mode=True,
        cognito_user_pool_id="test-pool",
        cognito_client_id="test-client",
        euri_embedding_dimensions=64,
        relevance_threshold=0.0,
        trusted_hosts="localhost,127.0.0.1,testserver,test",
    )
    get_settings.cache_clear()
    return s


@pytest.fixture
def fake_euri() -> FakeEuri:
    return FakeEuri(dims=64)


@pytest_asyncio.fixture
async def app(settings, fake_euri, monkeypatch):
    from app.clients.vectorstore import VectorStore
    from app.ingestion.pipeline import IngestionPipeline
    from app.main import create_app
    from app.rag.cache import AnswerCache
    from app.rag.service import RagContext, RagService

    await db_session.reset_state()
    import app.core.config as cfg

    # monkeypatch restores the real lru_cache-wrapped function after each test;
    # assigning directly would destroy .cache_clear() for every later test.
    cached = _CachedStub(settings)
    monkeypatch.setattr(cfg, "get_settings", cached)
    monkeypatch.setattr("app.api.deps.get_settings", cached, raising=False)

    application = create_app(settings)
    vectors = VectorStore(settings)
    cache = AnswerCache(settings)
    application.state.euri = fake_euri
    application.state.vectors = vectors
    application.state.cache = cache
    application.state.rag = RagService(settings, fake_euri, vectors, cache)
    application.state.ingestion = IngestionPipeline(settings, fake_euri, vectors)
    application.state.rag_context_factory = lambda: RagContext(settings, fake_euri, vectors)

    await db_session.init_db()
    await vectors.ensure_collection()
    await _seed(settings)
    yield application
    await db_session.reset_state()


async def _seed(settings: Settings) -> None:
    async with db_session.get_sessionmaker()() as s:
        s.add_all(
            [
                Tenant(id=TENANT_A, name="Acme", kb_version=1),
                Tenant(id=TENANT_B, name="Globex", kb_version=1),
                User(
                    id="user-a",
                    tenant_id=TENANT_A,
                    email="u@a.com",
                    role="user",
                    departments=["hr"],
                ),
                User(
                    id="admin-a",
                    tenant_id=TENANT_A,
                    email="a@a.com",
                    role="admin",
                    departments=["hr", "finance"],
                ),
                User(
                    id="user-b",
                    tenant_id=TENANT_B,
                    email="u@b.com",
                    role="user",
                    departments=["hr"],
                ),
                User(
                    id="user-nodept",
                    tenant_id=TENANT_A,
                    email="n@a.com",
                    role="user",
                    departments=[],
                ),
                User(
                    id="user-disabled",
                    tenant_id=TENANT_A,
                    email="d@a.com",
                    role="user",
                    departments=["hr"],
                    status="disabled",
                ),
            ]
        )
        await s.commit()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        # lifespan is not run by ASGITransport; state is already initialised above
        yield c


def make_token(
    sub: str = "user-a",
    *,
    issuer: str | None = None,
    audience: str | None = "test-client",
    expires_in: int = 3600,
    algorithm: str = "RS256",
    key: Any = None,
    token_use: str = "id",  # noqa: S107 - a JWT claim value, not a credential
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": sub,
        "iss": issuer or "https://cognito-idp.ap-south-1.amazonaws.com/test-pool",
        "exp": now + timedelta(seconds=expires_in),
        "iat": now,
        "nbf": now - timedelta(seconds=5),
        "token_use": token_use,
    }
    if audience:
        claims["aud"] = audience
    if extra:
        claims.update(extra)
    signing_key = key if key is not None else dev_private_pem()
    return jwt.encode(claims, signing_key, algorithm=algorithm)


def auth_headers(sub: str = "user-a", **kw: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(sub, **kw)}"}


@pytest.fixture
def principal_a() -> Principal:
    return Principal(
        user_id="user-a",
        tenant_id=TENANT_A,
        role="user",
        departments=("hr",),
        correlation_id="test-cid",
    )


@pytest.fixture
def principal_b() -> Principal:
    return Principal(
        user_id="user-b",
        tenant_id=TENANT_B,
        role="user",
        departments=("hr",),
        correlation_id="test-cid",
    )


@pytest.fixture
def admin_a() -> Principal:
    return Principal(
        user_id="admin-a",
        tenant_id=TENANT_A,
        role="admin",
        departments=("hr", "finance"),
        correlation_id="test-cid",
    )


@pytest.fixture(scope="session", autouse=True)
def _register_tools():
    register_all()
    return REGISTRY


async def seed_chunk(
    app_,
    *,
    tenant_id: str,
    department: str,
    text: str,
    document_id: str = "doc-1",
    document_name: str = "Handbook.pdf",
    owner_id: str = "user-a",
    chunk_id: str | None = None,
) -> str:
    """Insert one vector directly, bypassing ingestion, for retrieval tests."""
    cid = chunk_id or f"chunk-{uuid.uuid4().hex[:8]}"
    vec = await app_.state.euri.embed_one(text)
    await app_.state.vectors.upsert(
        [
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, cid)),
                "vector": vec,
                "payload": {
                    "document_id": document_id,
                    "chunk_id": cid,
                    "document_name": document_name,
                    "page_number": 1,
                    "source_uri": f"s3://bucket/{document_id}",
                    "owner_id": owner_id,
                    "tenant_id": tenant_id,
                    "document_version": 1,
                    "checksum": "abc123",
                    "created_at": datetime.now(UTC).isoformat(),
                    "department": department,
                    "modality": "text",
                    "element_type": "paragraph",
                    "status": "active",
                    "text": text,
                },
            }
        ]
    )
    return cid
