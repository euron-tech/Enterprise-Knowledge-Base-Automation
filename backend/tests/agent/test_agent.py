"""Agent invariants: read-only tools, principal injection, budgets, loop detection."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.agent.registry import REGISTRY, ToolError, ToolRegistry, ToolSpec
from app.agent.state import AgentState, Budget, BudgetExceeded
from app.core.constants import INSUFFICIENT_EVIDENCE, PRINCIPAL_FIELDS
from tests.conftest import TENANT_A, auth_headers, seed_chunk

pytestmark = pytest.mark.agent


# ------------------------------------------------------- registry invariants
def test_every_registered_tool_is_read_only(_register_tools):
    for name in REGISTRY.names():
        assert REGISTRY.get(name).read_only is True, f"{name} is not read-only"


def test_no_tool_exposes_a_principal_field(_register_tools):
    for name in REGISTRY.names():
        fields = set(REGISTRY.get(name).args_schema.model_fields)
        leaked = fields & PRINCIPAL_FIELDS
        assert not leaked, f"{name} exposes {leaked} as model-supplied arguments"


def test_no_dangerous_tool_exists(_register_tools):
    forbidden = {
        "shell",
        "exec",
        "python",
        "http_fetch",
        "fetch_url",
        "sql",
        "read_file",
        "write_file",
        "delete_document",
        "update_grants",
    }
    assert not (set(REGISTRY.names()) & forbidden)


def test_registering_a_principal_field_tool_is_rejected():
    class BadArgs(BaseModel):
        tenant_id: str

    reg = ToolRegistry()
    with pytest.raises(ValueError, match="principal field"):
        reg.register(
            ToolSpec(
                name="bad",
                description="",
                args_schema=BadArgs,
                handler=lambda **kw: None,
                allowed_roles=frozenset({"user"}),
            )
        )


def test_unknown_tool_raises_typed_error(_register_tools):
    with pytest.raises(ToolError) as exc:
        REGISTRY.get("definitely_not_a_tool")
    assert exc.value.code == "UNKNOWN_TOOL"


async def test_model_supplied_tenant_id_is_rejected(_register_tools, principal_a):
    spec = REGISTRY.get("hybrid_search")
    with pytest.raises(ToolError) as exc:
        REGISTRY.validate_args(spec, {"query": "x", "tenant_id": "tenant-bbb"})
    assert exc.value.code == "PRINCIPAL_OVERRIDE"


async def test_role_filtering_hides_tools_from_wrong_role(_register_tools):
    user_tools = {s.name for s in REGISTRY.for_role("user")}
    assert "hybrid_search" in user_tools
    schemas = REGISTRY.schemas_for("user")
    assert all(
        s["function"]["parameters"]["additionalProperties"] is False for s in schemas
    )


async def test_invalid_arguments_produce_typed_error(_register_tools):
    spec = REGISTRY.get("calculator")
    with pytest.raises(ToolError) as exc:
        REGISTRY.validate_args(spec, {"expression": 42})
    assert exc.value.code == "INVALID_ARGUMENTS"


# ------------------------------------------------------- budgets
def _budget(**kw):
    base = dict(
        max_iterations=3,
        max_tool_calls=4,
        max_calls_per_tool=2,
        max_context_chunks=10,
        max_tokens=100,
        wall_clock_seconds=30,
    )
    base.update(kw)
    return Budget(**base)


def test_iteration_cap_enforced():
    b = _budget()
    for _ in range(3):
        b.start_iteration()
    with pytest.raises(BudgetExceeded) as exc:
        b.start_iteration()
    assert exc.value.limit == "iterations"


def test_tool_call_cap_enforced():
    b = _budget(max_calls_per_tool=99)
    for i in range(4):
        b.charge_tool(f"t{i}")
    with pytest.raises(BudgetExceeded) as exc:
        b.charge_tool("t5")
    assert exc.value.limit == "tool_calls"


def test_per_tool_cap_enforced():
    b = _budget()
    b.charge_tool("search")
    b.charge_tool("search")
    with pytest.raises(BudgetExceeded) as exc:
        b.charge_tool("search")
    assert "calls_per_tool" in exc.value.limit


def test_token_cap_enforced():
    b = _budget()
    b.charge_tokens(90)
    with pytest.raises(BudgetExceeded) as exc:
        b.charge_tokens(20)
    assert exc.value.limit == "tokens"


def test_wall_clock_cap_enforced():
    import time

    b = _budget(wall_clock_seconds=0)
    time.sleep(0.05)
    with pytest.raises(BudgetExceeded) as exc:
        b.check_clock()
    assert exc.value.limit == "wall_clock"


def test_context_chunk_cap_enforced(principal_a):
    from app.clients.vectorstore import Chunk

    state = AgentState(
        question="q", principal=principal_a, budget=_budget(max_context_chunks=3)
    )
    chunks = [
        Chunk(
            chunk_id=f"c{i}",
            document_id="d",
            document_name="n",
            text="t",
            score=0.9,
            page_number=1,
            source_uri="s",
            document_version=1,
            tenant_id=TENANT_A,
            department="hr",
        )
        for i in range(10)
    ]
    added = state.add_evidence(chunks, "test")
    assert added == 3
    assert len(state.evidence) == 3


def test_evidence_without_provenance_is_dropped(principal_a):
    from app.clients.vectorstore import Chunk

    state = AgentState(question="q", principal=principal_a, budget=_budget())
    bad = Chunk(
        chunk_id="",
        document_id="",
        document_name="",
        text="orphan",
        score=0.9,
        page_number=1,
        source_uri="",
        document_version=1,
        tenant_id=TENANT_A,
        department="hr",
    )
    assert state.add_evidence([bad], "test") == 0


def test_loop_detection(principal_a):
    state = AgentState(question="q", principal=principal_a, budget=_budget())
    state.last_call_signature = 'search:{"query":"x"}'
    assert state.is_repeat_call('search:{"query":"x"}')
    assert not state.is_repeat_call('search:{"query":"y"}')


# ------------------------------------------------------- calculator sandbox
async def test_calculator_is_sandboxed(_register_tools, principal_a):
    for evil in [
        "__import__('os').system('ls')",
        "open('/etc/passwd')",
        "1+1; import os",
    ]:
        with pytest.raises(ToolError):
            await REGISTRY.dispatch(
                "calculator", {"expression": evil}, principal_a, None
            )


async def test_calculator_computes(_register_tools, principal_a):
    out = await REGISTRY.dispatch(
        "calculator", {"expression": "18 * 5 + 2"}, principal_a, None
    )
    assert out["result"] == 92.0


async def test_department_scope_reports_only_own_scope(_register_tools, principal_a):
    out = await REGISTRY.dispatch("department_scope", {}, principal_a, None)
    assert out["departments"] == ["hr"]
    assert "finance" not in str(out)


# ------------------------------------------------------- end to end agent loop
async def test_agent_uses_tool_then_answers(app, client, fake_euri):
    cid = await seed_chunk(
        app,
        tenant_id=TENANT_A,
        department="hr",
        text="Parental leave at Acme is 18 weeks at full pay.",
    )
    fake_euri.queue_tool_call("hybrid_search", {"query": "parental leave"})
    fake_euri.queue_stop()
    fake_euri.queue_answer(f"Parental leave is 18 weeks at full pay. [{cid}]")

    r = await client.post(
        "/chat",
        json={"question": "How much parental leave?"},
        headers=auth_headers("user-a"),
    )
    body = r.json()
    assert body["terminal_reason"] == "answered"
    assert body["citations"] and body["citations"][0]["chunk_id"] == cid
    assert body["tool_calls"] == 1


async def test_refuse_tool_produces_exact_refusal(app, client, fake_euri):
    fake_euri.queue_tool_call("refuse", {"reason": "out_of_scope"})
    r = await client.post(
        "/chat",
        json={"question": "What is the weather?"},
        headers=auth_headers("user-a"),
    )
    body = r.json()
    assert body["answer"] == INSUFFICIENT_EVIDENCE
    assert body["citations"] == []


async def test_clarification_path(app, client, fake_euri):
    fake_euri.queue_tool_call(
        "request_clarification", {"question": "Which department?"}
    )
    r = await client.post(
        "/chat",
        json={"question": "What is the policy?"},
        headers=auth_headers("user-a"),
    )
    body = r.json()
    assert body["terminal_reason"] == "clarification_requested"
    assert "Which department" in body["answer"]


async def test_unregistered_tool_call_is_handled_not_executed(app, client, fake_euri):
    fake_euri.queue_tool_call("run_shell_command", {"cmd": "rm -rf /"})
    fake_euri.queue_stop()
    r = await client.post(
        "/chat", json={"question": "hello"}, headers=auth_headers("user-a")
    )
    assert r.status_code == 200
    assert r.json()["answer"] == INSUFFICIENT_EVIDENCE


async def test_model_supplied_department_cannot_widen_scope(app, client, fake_euri):
    await seed_chunk(
        app,
        tenant_id=TENANT_A,
        department="finance",
        text="Finance travel cap is 500 dollars",
        document_id="doc-fin",
    )
    fake_euri.queue_tool_call(
        "hybrid_search", {"query": "travel cap", "department": "finance"}
    )
    fake_euri.queue_stop()
    r = await client.post(
        "/chat",
        json={"question": "What is the travel cap?"},
        headers=auth_headers("user-a"),
    )
    body = r.json()
    assert body["retrieved_chunks"] == []
    assert body["answer"] == INSUFFICIENT_EVIDENCE


async def test_iteration_cap_terminates_the_loop(app, client, fake_euri, settings):
    for _ in range(30):
        fake_euri.queue_tool_call("hybrid_search", {"query": f"q{_}"})
    r = await client.post(
        "/chat", json={"question": "loop forever"}, headers=auth_headers("user-a")
    )
    body = r.json()
    assert body["iterations"] <= settings.agent_max_iterations
    assert body["tool_calls"] <= settings.agent_max_tool_calls
