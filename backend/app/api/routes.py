"""All routers. Handlers parse, delegate and return — no business logic here."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.deps import CurrentPrincipal, rate_limit, require_role
from app.core.audit import Actions, record
from app.core.constants import INSUFFICIENT_EVIDENCE
from app.core.errors import AuthorizationError, NotFoundError
from app.db.models import Document, IngestionJob, RequestUsage, Tenant, UserFeedback
from app.db.session import get_sessionmaker
from app.security.files import validate_upload

router = APIRouter()


# --------------------------------------------------------------------- schemas
class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=1000)
    department: str | None = None
    top_k: int = Field(default=8, ge=1, le=20)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str
    rating: int = Field(ge=-1, le=1)
    reason: str = Field(default="", max_length=64)
    comment: str = Field(default="", max_length=2000)


# --------------------------------------------------------------------- health
@router.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["health"])
async def readyz(request: Request) -> dict[str, Any]:
    checks: dict[str, str] = {}
    try:
        async with get_sessionmaker()() as s:
            await s.execute(select(func.count()).select_from(Tenant))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        await request.app.state.vectors.ensure_collection()
        checks["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["qdrant"] = f"error: {type(exc).__name__}"
    ready = all(v == "ok" for v in checks.values())
    return {"status": "ready" if ready else "degraded", "checks": checks}


# --------------------------------------------------------------------- chat
@router.post("/chat", tags=["rag"], dependencies=[Depends(rate_limit("/chat"))])
async def chat(body: ChatRequest, principal: CurrentPrincipal, request: Request) -> dict[str, Any]:
    return await request.app.state.rag.answer(body.question, principal, route="/chat")


@router.post("/search", tags=["rag"], dependencies=[Depends(rate_limit("/search"))])
async def search(
    body: SearchRequest, principal: CurrentPrincipal, request: Request
) -> dict[str, Any]:
    """Retrieval only, no generation — same authorization rules as /chat."""
    if body.department and not principal.may_access(body.department):
        await record(
            Actions.AUTHZ_DENIED,
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            resource_type="department",
            resource_id=body.department,
            outcome="denied",
        )
        raise AuthorizationError("department not granted")

    ctx = request.app.state.rag_context_factory()
    chunks = await ctx.retrieve(principal, body.query, department=body.department, top_k=body.top_k)
    return {
        "results": [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "document_name": c.document_name,
                "page_number": c.page_number,
                "score": round(c.score, 4),
                "text": c.text[:800],
            }
            for c in chunks
        ],
        "count": len(chunks),
    }


# --------------------------------------------------------------------- documents
@router.post(
    "/documents/upload",
    tags=["documents"],
    dependencies=[Depends(rate_limit("/documents/upload"))],
)
async def upload_document(
    principal: CurrentPrincipal,
    request: Request,
    file: Annotated[UploadFile, File()],
    department: Annotated[str, Form()],
) -> dict[str, Any]:
    if not principal.may_access(department):
        await record(
            Actions.AUTHZ_DENIED,
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            resource_type="department",
            resource_id=department,
            outcome="denied",
        )
        raise AuthorizationError("department not granted")

    data = await file.read()
    settings = request.app.state.settings
    safe_name, mime = validate_upload(data, file.filename or "upload", settings.max_upload_bytes)

    pipeline = request.app.state.ingestion
    checksum = __import__("hashlib").sha256(data).hexdigest()

    async with get_sessionmaker()() as s:
        existing = (
            await s.execute(
                select(Document).where(
                    Document.tenant_id == principal.tenant_id,
                    Document.department == department,
                    Document.checksum == checksum,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return {
                "document_id": existing.id,
                "status": "duplicate",
                "chunks_written": 0,
                "message": "identical content already ingested",
            }

        doc = Document(
            id=str(uuid.uuid4()),
            tenant_id=principal.tenant_id,
            department=department,
            name=safe_name,
            s3_uri=f"s3://{settings.s3_bucket_documents}/{principal.tenant_id}/{department}/{safe_name}",
            mime_type=mime,
            size_bytes=len(data),
            checksum=checksum,
            version=1,
            owner_id=principal.user_id,
        )
        job = IngestionJob(
            document_id=doc.id,
            tenant_id=principal.tenant_id,
            state="running",
            stage="parse",
        )
        s.add_all([doc, job])
        await s.commit()
        doc_id, job_id = doc.id, job.id

    result = await pipeline.ingest(
        data=data,
        filename=safe_name,
        mime=mime,
        tenant_id=principal.tenant_id,
        department=department,
        owner_id=principal.user_id,
        document_id=doc_id,
        source_uri=doc.s3_uri,
    )

    async with get_sessionmaker()() as s:
        d = await s.get(Document, doc_id)
        j = await s.get(IngestionJob, job_id)
        if d:
            d.page_count = result.pages
        if j:
            j.state = "completed" if result.chunks_written else "failed"
            j.stage = "done"
            j.chunks_written = result.chunks_written
            if not result.chunks_written:
                j.error_code = "no_chunks"
        t = (
            await s.execute(select(Tenant).where(Tenant.id == principal.tenant_id))
        ).scalar_one_or_none()
        if t:
            t.kb_version += 1  # invalidates this tenant's cached answers
        await s.commit()

    await record(
        Actions.DOCUMENT_UPLOAD,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        resource_type="document",
        resource_id=doc_id,
        outcome="allowed",
        chunks=result.chunks_written,
        quarantined=result.quarantined,
    )
    return {
        "document_id": doc_id,
        "job_id": job_id,
        "status": "completed" if result.chunks_written else "failed",
        "chunks_written": result.chunks_written,
        "quarantined_elements": result.quarantined,
        "pages": result.pages,
        "warnings": result.warnings,
    }


@router.get("/documents", tags=["documents"])
async def list_documents(principal: CurrentPrincipal, request: Request) -> dict[str, Any]:
    ctx = request.app.state.rag_context_factory()
    docs = await ctx.list_documents(principal, limit=100)
    return {"documents": docs, "count": len(docs)}


@router.delete("/documents/{document_id}", tags=["documents"])
async def delete_document(
    document_id: str, principal: CurrentPrincipal, request: Request
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        doc = (
            await s.execute(
                select(Document).where(
                    Document.id == document_id,
                    Document.tenant_id == principal.tenant_id,
                )
            )
        ).scalar_one_or_none()

        if doc is None:
            raise NotFoundError("no such document")
        # Ownership verified server-side against the database; admin may override.
        if doc.owner_id != principal.user_id and not principal.is_admin:
            await record(
                Actions.AUTHZ_DENIED,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                resource_type="document",
                resource_id=document_id,
                outcome="denied",
                reason="not_owner",
            )
            raise AuthorizationError("only the owner or an admin may delete this document")

        # Audit first, then remove vectors, then mark the row.
        await record(
            Actions.DOCUMENT_DELETE,
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            resource_type="document",
            resource_id=document_id,
            outcome="allowed",
        )
        await request.app.state.vectors.delete_document(principal, document_id)
        doc.status = "retired"
        t = (
            await s.execute(select(Tenant).where(Tenant.id == principal.tenant_id))
        ).scalar_one_or_none()
        if t:
            t.kb_version += 1
        await s.commit()
    return {"document_id": document_id, "status": "deleted"}


# --------------------------------------------------------------------- feedback
@router.post("/feedback", tags=["feedback"])
async def feedback(body: FeedbackRequest, principal: CurrentPrincipal) -> dict[str, str]:
    async with get_sessionmaker()() as s:
        s.add(
            UserFeedback(
                message_id=body.message_id,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                rating=body.rating,
                reason=body.reason,
                comment=body.comment,
            )
        )
        await s.commit()
    return {"status": "recorded"}


# --------------------------------------------------------------------- admin
@router.get("/admin/metrics", tags=["admin"], dependencies=[Depends(require_role("admin"))])
async def admin_metrics(principal: CurrentPrincipal) -> dict[str, Any]:
    await record(
        Actions.ADMIN_METRICS,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        outcome="allowed",
    )
    async with get_sessionmaker()() as s:
        # Tenant-scoped. There is no cross-tenant read in v1.
        base = select(RequestUsage).where(RequestUsage.tenant_id == principal.tenant_id)
        rows = (await s.execute(base)).scalars().all()
        docs = (
            await s.execute(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.tenant_id == principal.tenant_id,
                    Document.status == "active",
                )
            )
        ).scalar_one()

    total = len(rows)
    latencies = sorted(r.latency_ms for r in rows) or [0]

    def pct(p: float) -> int:
        return latencies[min(int(len(latencies) * p), len(latencies) - 1)]

    return {
        "tenant_id": principal.tenant_id,
        "requests": total,
        "documents_active": docs,
        "tokens_in": sum(r.input_tokens for r in rows),
        "tokens_out": sum(r.output_tokens for r in rows),
        "estimated_cost": float(sum(float(r.estimated_cost) for r in rows)),
        "latency_p50_ms": pct(0.5),
        "latency_p95_ms": pct(0.95),
        "refusals": sum(1 for r in rows if r.terminal_reason.startswith("refused")),
        "limit_exceeded": sum(1 for r in rows if r.terminal_reason == "limit_exceeded"),
        "avg_iterations": (round(sum(r.iterations for r in rows) / total, 2) if total else 0),
        "avg_tool_calls": (round(sum(r.tool_calls for r in rows) / total, 2) if total else 0),
        "refusal_message": INSUFFICIENT_EVIDENCE,
    }
