"""Typed agent state and the code-enforced budget manager."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.auth.principal import Principal
from app.clients.vectorstore import Chunk
from app.core.config import Settings
from app.core.constants import TerminalReason


@dataclass
class Evidence:
    chunk: Chunk
    tool: str
    iteration: int

    @property
    def has_provenance(self) -> bool:
        """Content without provenance cannot be cited, so it cannot be used."""
        c = self.chunk
        return bool(c.chunk_id and c.document_id and c.document_name)


class BudgetExceeded(Exception):
    def __init__(self, limit: str) -> None:
        self.limit = limit
        super().__init__(f"budget exceeded: {limit}")


@dataclass
class Budget:
    """Hard limits. Enforced here in code — never requested in a prompt."""

    max_iterations: int
    max_tool_calls: int
    max_calls_per_tool: int
    max_context_chunks: int
    max_tokens: int
    wall_clock_seconds: int

    iterations: int = 0
    tool_calls: int = 0
    tokens: int = 0
    per_tool: Counter[str] = field(default_factory=Counter)
    # perf_counter, not monotonic: on Windows monotonic() has ~15.6 ms resolution,
    # which is too coarse to measure sub-tick elapsed time reliably.
    started_at: float = field(default_factory=time.perf_counter)

    @classmethod
    def from_settings(cls, s: Settings) -> Budget:
        return cls(
            max_iterations=s.agent_max_iterations,
            max_tool_calls=s.agent_max_tool_calls,
            max_calls_per_tool=s.agent_max_calls_per_tool,
            max_context_chunks=s.agent_max_context_chunks,
            max_tokens=s.agent_max_tokens,
            wall_clock_seconds=s.agent_wall_clock_seconds,
        )

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started_at

    def check_clock(self) -> None:
        if self.elapsed > self.wall_clock_seconds:
            raise BudgetExceeded("wall_clock")

    def start_iteration(self) -> None:
        self.check_clock()
        if self.iterations >= self.max_iterations:
            raise BudgetExceeded("iterations")
        self.iterations += 1

    def charge_tool(self, name: str) -> None:
        self.check_clock()
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded("tool_calls")
        if self.per_tool[name] >= self.max_calls_per_tool:
            raise BudgetExceeded(f"calls_per_tool:{name}")
        self.tool_calls += 1
        self.per_tool[name] += 1

    def charge_tokens(self, n: int) -> None:
        self.tokens += n
        if self.tokens > self.max_tokens:
            raise BudgetExceeded("tokens")


@dataclass
class AgentState:
    question: str
    principal: Principal
    budget: Budget

    evidence: list[Evidence] = field(default_factory=list)
    plan_history: list[dict[str, Any]] = field(default_factory=list)
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    last_call_signature: str = ""

    answer: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    clarification: str = ""
    terminal_reason: str = ""
    confidence: float = 0.0

    input_tokens: int = 0
    output_tokens: int = 0
    model_used: str = ""
    fallback_used: bool = False

    def add_evidence(self, chunks: list[Chunk], tool: str) -> int:
        """Append-only, deduped by chunk_id, capped by the context budget."""
        seen = {e.chunk.chunk_id for e in self.evidence}
        added = 0
        for c in chunks:
            if c.chunk_id in seen:
                continue
            if len(self.evidence) >= self.budget.max_context_chunks:
                break
            ev = Evidence(chunk=c, tool=tool, iteration=self.budget.iterations)
            if not ev.has_provenance:
                continue
            self.evidence.append(ev)
            seen.add(c.chunk_id)
            added += 1
        return added

    @property
    def chunks(self) -> list[Chunk]:
        return [e.chunk for e in self.evidence]

    def is_repeat_call(self, signature: str) -> bool:
        """Loop detection: the same call twice in a row terminates the branch."""
        return signature == self.last_call_signature

    def terminate(self, reason: str) -> None:
        if not self.terminal_reason:
            self.terminal_reason = reason

    @property
    def refused(self) -> bool:
        return self.terminal_reason.startswith("refused") or (
            self.terminal_reason == TerminalReason.LIMIT_EXCEEDED and not self.answer
        )
