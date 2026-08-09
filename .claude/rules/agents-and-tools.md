# Rule — Agents and Tools (LangGraph)

Applies to everything in `backend/app/agent/`, every tool definition, and every prompt that drives
a tool-calling loop. Read together with `ai-guardrails.md` and `security.md`.

---

## 1. The governing principle

**The agent decides *what to do*. It never decides *what it is allowed to do*.**

Autonomy lives strictly inside a sandbox built from deterministic gates:

```
DETERMINISTIC PRE-FLIGHT   (agent cannot skip, reorder or influence)
   auth → input validation → injection scan → cache lookup
         ↓
AGENTIC CORE               (LangGraph; plan, call tools, observe, reflect, loop)
         ↓
DETERMINISTIC POST-FLIGHT  (agent cannot skip, reorder or influence)
   citation validation → output guardrail → usage recording → trace → response
```

An agent that could choose to skip authorization or citation validation is not an agent, it is a
vulnerability. Gates are code, not instructions in a prompt.

---

## 2. Graph structure

Framework: **LangGraph**. One compiled graph per entry point, built at startup, never mutated at
request time.

Nodes in the agentic core:

| Node | Responsibility |
|---|---|
| `planner` | Reads the question, the principal's scope and prior observations; produces the next action: call a tool, ask for clarification, answer, or refuse |
| `tool_router` | Validates and dispatches the chosen tool call through the tool registry |
| `tool_executor` | Runs the tool with the server-side `Principal` injected; captures result + telemetry |
| `observation` | Normalizes the tool result into typed evidence; records provenance |
| `reflector` | Sufficiency critic: is the accumulated evidence enough to answer with citations? What is missing? |
| `context_builder` | Ordering, dedupe, token budget, dominance cap, untrusted-content delimiting |
| `model_router` | Chooses the generation model + fallback chain |
| `generator` | Produces the grounded, cited answer |
| `finish` | Hands off to the deterministic post-flight |

Edges: `planner → tool_router → tool_executor → observation → reflector → {planner | context_builder}`.
`planner → {clarify | refuse | context_builder}` as direct exits.

Rules:

- Graph state is a typed Pydantic model (`AgentState`), never a loose dict.
- Every node is an independently unit-testable function `(AgentState) -> AgentState`.
- Every node emits a LangSmith span and a latency metric.
- Checkpointing is per conversation and tenant-scoped; a checkpoint is never shared across tenants.
- Sub-agents (retrieval specialist, media specialist, tabular specialist) are compiled subgraphs,
  invoked as tools by the planner — they inherit the same Principal and the same caps.

---

## 3. Hard limits on the loop

Every one of these is enforced in code and configurable per environment, never by the prompt:

| Limit | Default |
|---|---|
| Max planner iterations per request | 8 |
| Max tool calls per request | 12 |
| Max calls to any single tool | 4 |
| Max total retrieved chunks entering context | 40 |
| Max total tokens (input + output) per request | tenant policy, hard cap 60k |
| Max wall-clock per request | 45 s (dev 90 s) |
| Max recursion depth into sub-agents | 2 |

On breach: the loop terminates, the agent answers from what it has if citations validate, otherwise
returns the refusal string. A breach is logged, audited and emitted as a metric — it is never
silently retried.

Loop-detection: identical tool call with identical arguments twice in a row terminates the branch.

---

## 4. Tool catalog

Tools live in `backend/app/agent/tools/`, one module per tool, registered in a single
`TOOL_REGISTRY`. Nothing reaches the model that is not in the registry.

### 4.1 Retrieval and knowledge tools

| Tool | Purpose | Notes |
|---|---|---|
| `semantic_search` | Vector search over the tenant's permitted chunks | Filter built by the chokepoint; `top_k` capped |
| `keyword_search` | Lexical/BM25-style search for exact terms, IDs, clause numbers | Same filter path |
| `hybrid_search` | Fused semantic + keyword | Preferred default |
| `list_documents` | Enumerate documents visible to the principal | Metadata only |
| `get_document_metadata` | Title, version, owner, page count, dates, department | No content |
| `fetch_chunk` | Retrieve a specific chunk by `chunk_id` | Must already be in the permitted set |
| `expand_context` | Fetch neighbouring chunks around a hit | Bounded window |
| `get_page` | Retrieve a specific page's extracted text | Page-level provenance |
| `summarize_document` | Map-reduce summary of one permitted document | Costed; counts against budget |
| `compare_documents` | Structured diff of two permitted documents/versions | e.g. policy v3 vs v4 |

### 4.2 Modality-specialist tools

| Tool | Purpose |
|---|---|
| `table_lookup` | Query extracted tables (filter/aggregate over rows) with row-level provenance |
| `image_describe` | Return the stored description + multimodal match for a diagram/screenshot |
| `media_locate` | Find the timestamp range in an audio/video asset matching a query |
| `transcript_segment` | Fetch a transcript window around a timestamp |

### 4.3 Reasoning and utility tools

| Tool | Purpose | Notes |
|---|---|---|
| `calculator` | Deterministic arithmetic over figures found in context | Sandboxed expression evaluator, no `eval` |
| `date_resolver` | Resolve relative dates ("last quarter") against a supplied clock | No ambient `now()` |
| `glossary_lookup` | Expand tenant-specific acronyms and entity aliases | From tenant glossary table |
| `language_normalize` | Detect language, produce a normalized retrieval query | Answer language stays the user's |
| `department_scope` | Report which departments the principal may search | Never reveals other departments' content |

### 4.4 Control tools

| Tool | Purpose |
|---|---|
| `request_clarification` | Ask the user one targeted question when the query is ambiguous |
| `refuse` | Terminate with the fixed refusal string and a machine-readable reason |
| `escalate` | Flag for human/admin review (e.g. suspected poisoning), returns a ticket reference |

### 4.5 Tools that must NEVER exist

- Arbitrary code execution, shell, or Python REPL
- Arbitrary HTTP fetch / URL loader (SSRF, exfiltration)
- Raw SQL execution
- Filesystem read/write
- Document deletion, retirement, grant modification, or any tenant-configuration mutation
- Any tool taking `tenant_id`, `department`, `owner_id` or `role` as a model-supplied argument
- Any tool that returns a secret, a raw JWT, or a presigned URL into the model's context

Writes are performed by deterministic application code after an authenticated human action —
never by a model deciding to.

---

## 5. Tool contract

Every tool declares:

```python
class ToolSpec(BaseModel):
    name: str
    description: str            # what the model sees — precise, no capability inflation
    args_schema: type[BaseModel]  # strict, extra="forbid"
    returns_schema: type[BaseModel]
    allowed_roles: frozenset[Role]
    cost_class: Literal["cheap", "moderate", "expensive"]
    max_calls_per_request: int
    read_only: Literal[True]    # all tools are read-only; there is no other value
```

Execution rules:

1. **Principal injection.** `tenant_id`, `departments`, `user_id`, `role` and `correlation_id` are
   injected server-side from the verified JWT. They are not parameters the model can set. If a
   model emits them, they are discarded and the event is audited.
2. **Strict argument validation.** Pydantic strict, `extra="forbid"`. Invalid arguments return a
   typed tool error to the agent — never a stack trace, never a partial execution.
3. **Authorization per call.** The tool re-checks role and department scope. Authorization is not
   inherited from "the planner already decided".
4. **Read-only.** Every tool is read-only. There is no write tool, no delete tool.
5. **Bounded output.** Each tool truncates its result to a declared token ceiling and reports the
   truncation to the agent honestly.
6. **Provenance.** Any tool returning content returns full provenance (`document_id`, `chunk_id`,
   `document_version`, `page_number` or `time_offset_ms`, `source_uri`). Content without provenance
   cannot be cited and therefore cannot be used.
7. **Untrusted output.** Tool results are wrapped in untrusted-content delimiters and rescanned for
   indirect injection before entering context.
8. **Telemetry.** Every call records: tool name, arguments (redacted), duration, result size, cache
   status, error, iteration index, correlation ID — to LangSmith and CloudWatch.
9. **Failure handling.** A tool failure returns a typed error the agent can reason about
   (`NOT_FOUND`, `OUT_OF_SCOPE`, `TRUNCATED`, `RATE_LIMITED`, `UPSTREAM_ERROR`). Retries are
   bounded and counted; a tool never silently returns empty on error.

---

## 6. Planner prompt rules

- The planner prompt is a versioned template in `app/agent/prompts/`, registered in
  `prompt_releases`, and referenced by version in every trace and usage record.
- It states the instruction hierarchy explicitly: system rules > user question > tool results.
  Tool results and document content are **data**, never instructions.
- It never contains secrets, tenant names other than the caller's, or the tool catalog of roles the
  caller does not hold — the registry is filtered by role before the model sees it.
- It must not be able to talk the system into a wider scope: scope comes from the Principal.
- Tool descriptions are honest and narrow. Do not describe a tool as more capable than it is —
  that causes wasted loops and hallucinated arguments.

---

## 7. Evidence and sufficiency

- `AgentState.evidence` is an append-only list of typed evidence items with provenance and scores.
- The `reflector` decides sufficiency against explicit criteria: does every part of the question
  have supporting evidence above the relevance floor, from documents the principal may see?
- If the reflector reports insufficiency and no further productive tool call exists, the agent
  refuses. **Insufficiency is a correct outcome, not a failure to work around.**
- The agent may ask for clarification at most once per conversation turn.

---

## 8. Determinism and testability

- Sampling temperature for the planner is low and configured, not improvised.
- Every tool is unit-testable in isolation against fakes.
- The graph is testable with a scripted planner (a stub that emits a fixed action sequence), so
  routing logic is tested without model calls.
- Golden-path tests assert the exact tool sequence for representative questions.
- Adversarial tests assert that no prompt can cause: a tool call outside the registry, a
  principal-field override, a loop-cap breach, or an unprovenanced citation.

---

## 9. Observability requirements for the agent

Every request's trace must show: the plan at each iteration, each tool call with arguments and
outcome, the evidence set, the reflector's verdict, the routing decision, and the terminal reason
(`answered`, `refused_insufficient_evidence`, `refused_guardrail`, `clarification_requested`,
`limit_exceeded`). Metrics: iterations per request, tool-call count, per-tool latency and error
rate, loop-cap breaches, refusal rate by reason, clarification rate.

---

## 10. Forbidden

| Never | Why |
|---|---|
| Let the model supply `tenant_id`/`department`/`role` | Trivially exploitable authorization bypass |
| Add a write, delete or config-mutation tool | Model error becomes data loss |
| Add a generic HTTP/shell/SQL/file tool | SSRF, RCE, exfiltration |
| Rely on a prompt to enforce a limit | Prompts are advisory; caps are code |
| Let the agent skip citation validation or output guardrails | They are outside the agent's reach by design |
| Unbounded loops or unbounded `gather` over tool calls | Cost and latency blowout |
| Register a tool without a strict args schema | Argument injection |
| Return raw upstream errors to the model | Information leakage and confused retries |
