"""Qdrant access. Every search goes through the tenancy chokepoint — no exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from app.auth.principal import Principal
from app.core.config import Settings
from app.core.constants import MANDATORY_PAYLOAD_FIELDS
from app.core.errors import ValidationError
from app.rag.filters import assert_tenant_scoped, build_filter


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    document_name: str
    text: str
    score: float
    page_number: int
    source_uri: str
    document_version: int
    tenant_id: str
    department: str
    modality: str = "text"
    time_offset_ms: int | None = None

    def citation(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "page_number": self.page_number,
            "document_version": self.document_version,
            "source_uri": self.source_uri,
            "time_offset_ms": self.time_offset_ms,
        }


class VectorStore:
    def __init__(self, settings: Settings, client: AsyncQdrantClient | None = None) -> None:
        self.settings = settings
        self.collection = settings.qdrant_collection
        if client is not None:
            self.client = client
        elif settings.qdrant_url in (":memory:", "", None):
            self.client = AsyncQdrantClient(location=":memory:")
        else:
            self.client = AsyncQdrantClient(url=settings.qdrant_url)

    async def ensure_collection(self) -> None:
        existing = await self.client.get_collections()
        if any(c.name == self.collection for c in existing.collections):
            return
        await self.client.create_collection(
            collection_name=self.collection,
            # Vectors are L2-normalized (verified), so DOT is equivalent to COSINE and faster.
            vectors_config=qm.VectorParams(
                size=self.settings.euri_embedding_dimensions, distance=qm.Distance.DOT
            ),
        )
        for field_name in ("tenant_id", "department", "status", "document_id"):
            try:
                await self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field_name,
                    field_schema=qm.PayloadSchemaType.KEYWORD,
                )
            except Exception:  # noqa: BLE001, S110 - index may already exist
                pass

    async def upsert(self, points: list[dict[str, Any]]) -> int:
        """Reject any point missing a mandatory payload field — in code, not in review."""
        qpoints = []
        for p in points:
            payload = p["payload"]
            missing = [f for f in MANDATORY_PAYLOAD_FIELDS if payload.get(f) in (None, "")]
            if missing:
                raise ValidationError(f"vector payload missing mandatory fields: {missing}")
            qpoints.append(qm.PointStruct(id=p["id"], vector=p["vector"], payload=payload))
        if qpoints:
            await self.client.upsert(collection_name=self.collection, points=qpoints)
        return len(qpoints)

    async def search(
        self,
        principal: Principal,
        vector: list[float],
        *,
        department: str | None = None,
        document_ids: list[str] | None = None,
        top_k: int = 8,
    ) -> list[Chunk]:
        flt = build_filter(principal, department=department, document_ids=document_ids)
        assert_tenant_scoped(flt, principal.tenant_id)

        hits = await self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=flt,
            limit=min(top_k, 50),
            with_payload=True,
        )
        return [self._to_chunk(h.payload or {}, h.score) for h in hits.points]

    async def fetch_by_ids(self, principal: Principal, chunk_ids: list[str]) -> list[Chunk]:
        flt = build_filter(principal)
        assert_tenant_scoped(flt, principal.tenant_id)
        combined = qm.Filter(
            must=[
                *(flt.must or []),
                qm.FieldCondition(key="chunk_id", match=qm.MatchAny(any=chunk_ids)),
            ]
        )
        points, _ = await self.client.scroll(
            collection_name=self.collection,
            scroll_filter=combined,
            limit=len(chunk_ids) or 1,
            with_payload=True,
        )
        return [self._to_chunk(p.payload or {}, 1.0) for p in points]

    async def delete_document(self, principal: Principal, document_id: str) -> None:
        flt = build_filter(principal, include_retired=True, document_ids=[document_id])
        assert_tenant_scoped(flt, principal.tenant_id)
        await self.client.delete(
            collection_name=self.collection, points_selector=qm.FilterSelector(filter=flt)
        )

    @staticmethod
    def _to_chunk(payload: dict[str, Any], score: float) -> Chunk:
        return Chunk(
            chunk_id=payload.get("chunk_id", ""),
            document_id=payload.get("document_id", ""),
            document_name=payload.get("document_name", ""),
            text=payload.get("text", ""),
            score=float(score),
            page_number=int(payload.get("page_number") or 0),
            source_uri=payload.get("source_uri", ""),
            document_version=int(payload.get("document_version") or 1),
            tenant_id=payload.get("tenant_id", ""),
            department=payload.get("department", ""),
            modality=payload.get("modality", "text"),
            time_offset_ms=payload.get("time_offset_ms"),
        )
