"""The RAG service.

Shape:
  PRE-FLIGHT (fixed)  -> AGENT (decides) -> POST-FLIGHT (fixed)
The agent cannot skip, reorder or influence the gates. That is enforced structurally:
the gates are here, not registered as tools.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.agent.graph import AgentGraph
from app.agent.registry import REGISTRY
from app.agent.state import AgentState, Budget
from app.auth.principal import Principal
from app.clients.euri import EuriClient
from app.clients.vectorstore import Chunk, VectorStore
from app.core.audit import Actions, record
from app.core.config import Settings
from app.core.constants import INSUFFICIENT_EVIDENCE, TerminalReason
from app.core.errors import GuardrailError
from app.core.logging import correlation_id_var, get_logger, hash_text, log_event
from app.db.models import Document, RequestUsage, Tenant
from app.db.session import get_sessionmaker
from app.rag import attribution
from app.rag import citations as citation_gate
from app.rag.cache import AnswerCache, cache_key
from app.security import output_guard
from app.security.injection import scan

logger = get_logger(__name__)


class RagContext:
    """What tools are given. Note there is no write path anywhere in here."""

    def __init__(
        self,
        settings: Settings,
        euri: EuriClient,
        vectors: VectorStore,
        state: AgentState | None = None,
    ) -> None:
        self.settings = settings
        self.euri = euri
        self.vectors = vectors
        self.state = state

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def retrieve(
        self, principal: Principal, query: str, *, department: str | None, top_k: int
    ) -> list[Chunk]:
        vector = await self.euri.embed_one(query)
        chunks = await self.vectors.search(
            principal, vector, department=department, top_k=top_k
        )
        # Relevance floor + per-document dominance cap, applied on every retrieval.
        threshold = self.settings.relevance_threshold
        kept: list[Chunk] = []
        per_doc: dict[str, int] = {}
        cap = max(2, self.settings.agent_max_context_chunks // 4)
        for c in sorted(chunks, key=lambda x: x.score, reverse=True):
            if c.score < threshold:
                continue
            if per_doc.get(c.document_id, 0) >= cap:
                continue
            per_doc[c.document_id] = per_doc.get(c.document_id, 0) + 1
            kept.append(c)
        return kept

    async def list_documents(
        self, principal: Principal, *, limit: int
    ) -> list[dict[str, Any]]:
        async with get_sessionmaker()() as s:
            stmt = select(Document).where(
                Document.tenant_id == principal.tenant_id, Document.status == "active"
            )
            if not principal.is_admin:
                stmt = stmt.where(
                    Document.department.in_(list(principal.departments) or [""])
                )
            rows = (await s.execute(stmt.limit(limit))).scalars().all()
        return [
            {
                "document_id": d.id,
                "name": d.name,
                "department": d.department,
                "version": d.version,
                "pages": d.page_count,
            }
            for d in rows
        ]

    async def document_metadata(
        self, principal: Principal, document_id: str
    ) -> dict[str, Any] | None:
        async with get_sessionmaker()() as s:
            stmt = select(Document).where(
                Document.id == document_id, Document.tenant_id == principal.tenant_id
            )
            doc = (await s.execute(stmt)).scalar_one_or_none()
        if doc is None or not principal.may_access(doc.department):
            return None
        return {
            "document_id": doc.id,
            "name": doc.name,
            "department": doc.department,
            "version": doc.version,
            "pages": doc.page_count,
            "created_at": doc.created_at.isoformat(),
            "mime_type": doc.mime_type,
        }


def registry_hash() -> str:
    return hashlib.sha256("|".join(REGISTRY.names()).encode()).hexdigest()[:12]


class RagService:
    def __init__(
        self,
        settings: Settings,
        euri: EuriClient,
        vectors: VectorStore,
        cache: AnswerCache,
    ) -> None:
        self.settings = settings
        self.euri = euri
        self.vectors = vectors
        self.cache = cache

    async def answer(
        self, question: str, principal: Principal, *, route: str = "/chat"
    ) -> dict[str, Any]:
        started = time.perf_counter()
        trace_id = correlation_id_var.get()

        # ---------------- PRE-FLIGHT (the agent cannot skip any of this) --------------
        if not question or not question.strip():
            raise GuardrailError(
                "empty question", public_message="A question is required."
            )
        if len(question) > 4000:
            raise GuardrailError(
                "question too long", public_message="The question is too long."
            )

        injection = scan(question, source="user")
        if injection.blocked:
            await record(
                Actions.INJECTION_DETECTED,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                resource_type="chat",
                outcome="denied",
                categories=list(injection.categories),
                question_hash=hash_text(question),
            )
            raise GuardrailError(f"injection: {injection.categories}")

        kb_version = await self._kb_version(principal.tenant_id)
        key = cache_key(
            question,
            principal,
            self.settings,
            kb_version=kb_version,
            tool_registry_hash=registry_hash(),
        )
        cached = await self.cache.get(key)
        if cached:
            cached = {
                **cached,
                "cache_hit": True,
                "trace_id": trace_id,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
            return cached

        # ---------------- AGENTIC CORE (the agent decides what to do) -----------------
        state = AgentState(
            question=question,
            principal=principal,
            budget=Budget.from_settings(self.settings),
        )
        ctx = RagContext(self.settings, self.euri, self.vectors, state)
        await AgentGraph(ctx).run(state)

        # ---------------- POST-FLIGHT (outside the agent's reach) ---------------------
        response = await self._finalize(state, route, started, trace_id, key)
        return response

    async def _finalize(
        self, state: AgentState, route: str, started: float, trace_id: str, key: str
    ) -> dict[str, Any]:
        principal = state.principal
        guardrail_hits = 0
        citations: list[dict[str, Any]] = []
        confidence = 0.0

        if state.clarification:
            answer = state.clarification
            state.terminate(TerminalReason.CLARIFICATION_REQUESTED)
        elif state.refused or not state.answer:
            answer = INSUFFICIENT_EVIDENCE
            state.terminate(TerminalReason.REFUSED_INSUFFICIENT_EVIDENCE)
        else:
            # Gate 1 — citation validation. Fabricated citations are stripped.
            result = citation_gate.validate(state.answer, state.chunks)
            if result.stripped:
                log_event(
                    logger,
                    logging.WARNING,
                    "rag.fabricated_citations_stripped",
                    count=len(result.stripped),
                    ids=result.stripped[:5],
                )
            answer = result.text
            citations = result.citations

            refusal_emitted = INSUFFICIENT_EVIDENCE in answer
            if not result.has_valid_citation and not refusal_emitted:
                answer = INSUFFICIENT_EVIDENCE
                citations = []
                state.terminate(TerminalReason.REFUSED_INSUFFICIENT_EVIDENCE)
            elif refusal_emitted:
                answer = INSUFFICIENT_EVIDENCE
                citations = []
                state.terminate(TerminalReason.REFUSED_INSUFFICIENT_EVIDENCE)
            else:
                # Gate 1b — attribution. Do not let evidence from one source be
                # presented as another's just because the question framed it that way.
                attrib = attribution.check(state.question, state.chunks)
                if attrib.blocked:
                    log_event(
                        logger,
                        logging.INFO,
                        "rag.attribution_mismatch",
                        unknown_entities=list(attrib.unknown_entities),
                    )
                    answer = INSUFFICIENT_EVIDENCE
                    citations = []
                    state.terminate(TerminalReason.REFUSED_INSUFFICIENT_EVIDENCE)

                # Gate 2 — output guardrails.
                guarded = output_guard.apply(answer)
                if attrib.blocked:
                    pass  # already refused above; skip scoring
                elif guarded.blocked:
                    guardrail_hits += 1
                    await record(
                        Actions.GUARDRAIL_BLOCK,
                        tenant_id=principal.tenant_id,
                        actor_id=principal.user_id,
                        outcome="denied",
                        reason=guarded.reason,
                    )
                    answer = INSUFFICIENT_EVIDENCE
                    citations = []
                    state.terminate(TerminalReason.REFUSED_GUARDRAIL)
                else:
                    if guarded.redactions:
                        guardrail_hits += 1
                    answer = guarded.text
                    state.terminate(TerminalReason.ANSWERED)
                    confidence = citation_gate.score_confidence(
                        state.chunks,
                        result.coverage,
                        stripped=len(result.stripped),
                        guardrail_hits=guardrail_hits,
                    )

        latency_ms = int((time.perf_counter() - started) * 1000)
        model = state.model_used or self.settings.euri_generation_model
        cost = await self.euri.estimate_cost(
            model, state.input_tokens, state.output_tokens
        )

        payload = {
            "answer": answer,
            "citations": citations,
            "retrieved_chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "document_name": c.document_name,
                    "page_number": c.page_number,
                    "score": round(c.score, 4),
                }
                for c in state.chunks
            ],
            "model_used": model,
            "input_tokens": state.input_tokens,
            "output_tokens": state.output_tokens,
            "estimated_cost": round(cost, 8),
            "latency_ms": latency_ms,
            "cache_hit": False,
            "trace_id": trace_id,
            "confidence": confidence,
            "terminal_reason": state.terminal_reason,
            "iterations": state.budget.iterations,
            "tool_calls": state.budget.tool_calls,
        }

        # Gate 3 — usage recording. Always, on every path.
        await self._record_usage(state, route, payload)

        if state.terminal_reason == TerminalReason.ANSWERED:
            await self.cache.set(key, {**payload, "cache_hit": False})
        return payload

    async def _record_usage(
        self, state: AgentState, route: str, payload: dict[str, Any]
    ) -> None:
        try:
            async with get_sessionmaker()() as s:
                s.add(
                    RequestUsage(
                        tenant_id=state.principal.tenant_id,
                        user_id=state.principal.user_id,
                        route=route,
                        model=payload["model_used"],
                        input_tokens=state.input_tokens,
                        output_tokens=state.output_tokens,
                        estimated_cost=payload["estimated_cost"],
                        latency_ms=payload["latency_ms"],
                        cache_status="miss",
                        route_selected=payload["model_used"],
                        fallback_used=state.fallback_used,
                        prompt_version=self.settings.prompt_version,
                        agent_version=self.settings.agent_version,
                        iterations=state.budget.iterations,
                        tool_calls=state.budget.tool_calls,
                        terminal_reason=state.terminal_reason,
                        trace_id=payload["trace_id"],
                    )
                )
                await s.commit()
        except Exception as exc:  # noqa: BLE001 - accounting must not break the answer
            log_event(logger, logging.ERROR, "usage.record_failed", error=str(exc))

    async def _kb_version(self, tenant_id: str) -> int:
        try:
            async with get_sessionmaker()() as s:
                t = (
                    await s.execute(select(Tenant).where(Tenant.id == tenant_id))
                ).scalar_one_or_none()
                return t.kb_version if t else 1
        except Exception:  # noqa: BLE001
            return 1
