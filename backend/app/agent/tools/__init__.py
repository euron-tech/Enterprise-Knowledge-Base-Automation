"""Tool implementations. All read-only, all provenance-carrying, all role-gated."""

from __future__ import annotations

import ast
import operator
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.registry import REGISTRY, ToolError, ToolSpec
from app.auth.principal import Principal
from app.core.constants import INSUFFICIENT_EVIDENCE

USER_AND_ADMIN = frozenset({"user", "admin"})
MAX_TOOL_RESULT_CHARS = 6000


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------- retrieval
class SearchArgs(_Args):
    query: str = Field(min_length=1, max_length=1000, description="What to search for")
    top_k: int = Field(default=8, ge=1, le=20)
    # Deliberately NO department field. Scope comes from the Principal, never from the
    # model — the registry rejects any tool that exposes a principal field as an argument.
    # Searches automatically span exactly the departments the caller is granted.


async def _search(args: Any, principal: Principal, ctx: Any, mode: str) -> dict[str, Any]:
    chunks = await ctx.retrieve(principal, args.query, department=None, top_k=args.top_k)
    ctx.state.add_evidence(chunks, mode)
    return {
        "mode": mode,
        "hits": [
            {
                "chunk_id": c.chunk_id,
                "document_name": c.document_name,
                "page_number": c.page_number,
                "score": round(c.score, 4),
                "text": c.text[:1200],
            }
            for c in chunks
        ],
        "count": len(chunks),
    }


async def semantic_search(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    return await _search(args, principal, ctx, "semantic_search")


async def hybrid_search(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    return await _search(args, principal, ctx, "hybrid_search")


async def keyword_search(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    return await _search(args, principal, ctx, "keyword_search")


class FetchChunkArgs(_Args):
    chunk_ids: list[str] = Field(min_length=1, max_length=20)


async def fetch_chunk(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    chunks = await ctx.vectors.fetch_by_ids(principal, args.chunk_ids)
    ctx.state.add_evidence(chunks, "fetch_chunk")
    return {"chunks": [{"chunk_id": c.chunk_id, "text": c.text[:1500]} for c in chunks]}


# ----------------------------------------------------------------- documents
class ListDocsArgs(_Args):
    limit: int = Field(default=25, ge=1, le=100)


async def list_documents(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    docs = await ctx.list_documents(principal, limit=args.limit)
    return {"documents": docs, "count": len(docs)}


class DocMetaArgs(_Args):
    document_id: str = Field(min_length=1, max_length=64)


async def get_document_metadata(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    meta = await ctx.document_metadata(principal, args.document_id)
    if meta is None:
        raise ToolError("NOT_FOUND", "no such document in your scope")
    return meta


# ----------------------------------------------------------------- utility
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    """Deterministic arithmetic. No eval(), no names, no calls, no attributes."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ToolError("INVALID_ARGUMENTS", "only numeric literals are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ToolError("INVALID_ARGUMENTS", "unsupported expression")


class CalcArgs(_Args):
    expression: str = Field(min_length=1, max_length=200)


async def calculator(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    try:
        tree = ast.parse(args.expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError("INVALID_ARGUMENTS", "not a valid arithmetic expression") from exc
    value = _safe_eval(tree)
    return {"expression": args.expression, "result": value}


class DateArgs(_Args):
    expression: str = Field(description="e.g. 'today', 'last quarter', '30 days ago'")


async def date_resolver(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    now = ctx.now()
    expr = args.expression.lower().strip()
    if "today" in expr:
        return {"resolved": now.date().isoformat()}
    if "yesterday" in expr:
        return {"resolved": (now - timedelta(days=1)).date().isoformat()}
    for token, days in (("week", 7), ("month", 30), ("quarter", 91), ("year", 365)):
        if token in expr:
            return {
                "start": (now - timedelta(days=days)).date().isoformat(),
                "end": now.date().isoformat(),
            }
    return {"resolved": now.date().isoformat(), "note": "unrecognised expression; returned today"}


async def department_scope(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    """Reports the caller's own scope only — never what other departments exist."""
    return {"departments": list(principal.departments), "role": principal.role}


class EmptyArgs(_Args):
    pass


# ----------------------------------------------------------------- control
class ClarifyArgs(_Args):
    question: str = Field(min_length=1, max_length=300)


async def request_clarification(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    ctx.state.clarification = args.question
    return {"clarification_requested": args.question}


class RefuseArgs(_Args):
    reason: str = Field(default="insufficient_evidence", max_length=64)


async def refuse(*, args: Any, principal: Principal, ctx: Any) -> dict[str, Any]:
    return {"refused": True, "reason": args.reason, "message": INSUFFICIENT_EVIDENCE}


def register_all(registry: Any = REGISTRY) -> None:
    specs = [
        ToolSpec(
            name="hybrid_search",
            description=(
                "Search the approved knowledge base combining semantic and keyword matching. "
                "Use this first for most questions. Returns chunks with provenance."
            ),
            args_schema=SearchArgs,
            handler=hybrid_search,
            allowed_roles=USER_AND_ADMIN,
            cost_class="moderate",
        ),
        ToolSpec(
            name="semantic_search",
            description="Vector search for conceptually similar content in approved documents.",
            args_schema=SearchArgs,
            handler=semantic_search,
            allowed_roles=USER_AND_ADMIN,
            cost_class="moderate",
        ),
        ToolSpec(
            name="keyword_search",
            description="Exact-term search for IDs, clause numbers and specific phrases.",
            args_schema=SearchArgs,
            handler=keyword_search,
            allowed_roles=USER_AND_ADMIN,
        ),
        ToolSpec(
            name="fetch_chunk",
            description="Retrieve the full text of specific chunks by their chunk_id.",
            args_schema=FetchChunkArgs,
            handler=fetch_chunk,
            allowed_roles=USER_AND_ADMIN,
        ),
        ToolSpec(
            name="list_documents",
            description="List documents visible to you. Metadata only, no content.",
            args_schema=ListDocsArgs,
            handler=list_documents,
            allowed_roles=USER_AND_ADMIN,
        ),
        ToolSpec(
            name="get_document_metadata",
            description="Title, version, owner, page count and dates for one document.",
            args_schema=DocMetaArgs,
            handler=get_document_metadata,
            allowed_roles=USER_AND_ADMIN,
        ),
        ToolSpec(
            name="calculator",
            description="Evaluate arithmetic over figures found in retrieved context.",
            args_schema=CalcArgs,
            handler=calculator,
            allowed_roles=USER_AND_ADMIN,
        ),
        ToolSpec(
            name="date_resolver",
            description="Resolve a relative date expression to absolute dates.",
            args_schema=DateArgs,
            handler=date_resolver,
            allowed_roles=USER_AND_ADMIN,
        ),
        ToolSpec(
            name="department_scope",
            description="Report which departments you are permitted to search.",
            args_schema=EmptyArgs,
            handler=department_scope,
            allowed_roles=USER_AND_ADMIN,
        ),
        ToolSpec(
            name="request_clarification",
            description="Ask the user one targeted question when the request is ambiguous.",
            args_schema=ClarifyArgs,
            handler=request_clarification,
            allowed_roles=USER_AND_ADMIN,
        ),
        ToolSpec(
            name="refuse",
            description=(
                "Terminate with the standard refusal when the approved documents cannot "
                "answer the question. Refusing is correct when evidence is missing."
            ),
            args_schema=RefuseArgs,
            handler=refuse,
            allowed_roles=USER_AND_ADMIN,
        ),
    ]
    for spec in specs:
        # Idempotent: re-registering the same tool is a no-op, but a genuine
        # registration failure (read-only or principal-field violation) still raises.
        if spec.name in registry.names():
            continue
        registry.register(spec)


def now_utc() -> datetime:
    return datetime.now(UTC)
