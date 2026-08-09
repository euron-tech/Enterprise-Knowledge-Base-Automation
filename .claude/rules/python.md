# Rule — Python Code

Applies to every `.py` file in this repository. Read before writing Python.

---

## 1. Baseline

- **Python 3.12.** Target it in `pyproject.toml`; use modern syntax (`X | None`, `match`,
  `dataclass(slots=True)`, PEP 695 generics where it helps).
- **Tooling:** `ruff` (lint + import sort), `black` (format, line length 100), `mypy --strict`,
  `pytest`. All four must pass before a task is done.
- **Dependency management:** `uv` with a committed lockfile. Pin versions. No unpinned installs,
  no `pip install` inside application code.

---

## 2. Typing

- Every function, method and module-level constant is annotated. `mypy --strict` passes with no
  blanket ignores.
- No bare `Any`. If a boundary genuinely returns `Any`, narrow it immediately with a validator.
- `# type: ignore[code]` requires a specific error code and an inline reason comment.
- Prefer `Protocol` for seams you want to fake in tests; prefer `Literal` and `Enum` over strings
  for closed sets (roles, job states, cache status, modality).
- Use `NewType` for identifiers that must not be mixed up: `TenantId`, `DocumentId`, `ChunkId`,
  `UserId`, `CorrelationId`. This is how we make cross-tenant bugs a type error.

---

## 3. Project structure

```
backend/app/
  api/            routers only — HTTP in, DTO out, no business logic
  core/           config, logging, correlation, errors, constants
  auth/           JWT verification, principal, RBAC dependencies
  security/       guardrails, scanners, sanitizers
  rag/            LangGraph nodes, retrieval, cache, routing, prompts
  ingestion/      parsers, chunkers, embedders, workers
  db/             SQLAlchemy models, repositories, Alembic
  observability/  logging, metrics, tracing
  schemas/        Pydantic contracts
  services/       orchestration between the above
```

Dependency direction: `api → services → {rag, ingestion, auth, security} → db/clients`.
Never import upward. `core` and `schemas` may be imported by anyone; they import nothing local.

---

## 4. FastAPI

- One router per resource, an explicit `prefix` and `tags`, and a `response_model` on every route.
- Routers contain **no** business logic — parse, delegate, return.
- Auth, rate limiting and tenancy arrive via `Depends(...)`, never ad-hoc inside a handler.
- The authenticated `Principal` is a dependency-injected object carrying `user_id`, `tenant_id`,
  `role`, `departments`, `correlation_id`. Handlers never read raw JWT claims.
- Use `async def` for I/O-bound handlers. Never call blocking I/O from an async path — wrap it in
  `anyio.to_thread.run_sync`.
- Startup/shutdown via `lifespan`, not deprecated event handlers.
- Every route declares its rate-limit policy explicitly. A route with no policy fails a test.

---

## 5. Pydantic v2

- All request and response bodies are Pydantic models in `app/schemas/`.
- `model_config = ConfigDict(extra="forbid", strict=True, frozen=True)` for inbound DTOs.
- Validate at the boundary; inside the app, pass typed domain objects, not dicts.
- Settings via `pydantic-settings`, one `Settings` object, loaded once, never re-read from `os.environ`
  scattered through the code.
- Never put a secret's *value* in a model that could be logged or serialized into a response. Use
  `SecretStr` and never call `.get_secret_value()` outside the client that needs it.

---

## 6. Errors

- One exception hierarchy rooted at `AppError`, with `code`, `http_status`, `public_message` and
  `internal_detail`.
- Handlers return `public_message` + correlation ID in production. `internal_detail` goes to logs
  only.
- Never `except Exception: pass`. Never swallow an exception without logging it with context.
- Never catch a guardrail or authorization exception and continue — those propagate.
- Errors are typed by code so metrics and alarms can key off them.

---

## 7. Logging

- `structlog` (or stdlib logging with a JSON formatter). JSON only, one event per line.
- Every log line carries `correlation_id`, and where known `tenant_id`, `user_id`, `route`.
- **Never log:** secrets, tokens, JWTs, presigned URLs, full document text, embeddings, or (in
  prod) raw user questions. Log a question hash and length instead.
- No `print()` in application code.
- Log level via config; `DEBUG` never enabled in prod.

---

## 8. Async, concurrency and external calls

- Every outbound call (Euri gateway, Qdrant, S3, Supabase, Redis, Cognito JWKS) goes through a
  typed client in its own module with: explicit timeout, bounded retries with jittered backoff,
  a circuit breaker, and a metric on failure.
- No unbounded `asyncio.gather` over user-controlled input — bound concurrency with a semaphore.
- Idempotency keys on anything that writes twice safely (ingestion, upserts).
- Connection pools are created once at startup and shared; never per request.

---

## 9. Security-relevant coding rules

- **Never build a Qdrant filter by hand.** Use the single chokepoint builder in
  `app/rag/filters.py`. It requires a `Principal` and raises if `tenant_id` is absent.
- No raw SQL string interpolation — parameterized queries or the ORM, always.
- No `eval`, `exec`, `pickle` on untrusted data, `yaml.load` (use `safe_load`), or `subprocess`
  with `shell=True`.
- Path handling via `pathlib` with explicit normalization; reject traversal in any user-supplied
  name.
- `secrets` module for anything random that matters; never `random` for tokens or ids.
- Constant-time comparison for tokens/HMACs.
- HTTP clients always verify TLS. `verify=False` is forbidden.

---

## 10. LangGraph, agent and RAG code

See `.claude/rules/agents-and-tools.md` for the full contract. In Python terms:

- Each node is a pure-ish function `(AgentState) -> AgentState` in its own module, independently
  unit-testable without the graph and without a model call.
- `AgentState` is a typed Pydantic model, never a loose dict. Evidence is append-only and typed.
- The graph is compiled once at startup and never mutated per request.
- Tools live one-per-module under `app/agent/tools/`, each with a strict Pydantic args schema
  (`extra="forbid"`), a returns schema, an `allowed_roles` set and a per-request call cap, and are
  registered in the single `TOOL_REGISTRY`. Nothing dispatchable exists outside the registry.
- Principal fields are injected by the executor from the verified JWT. A tool signature must not
  accept `tenant_id`, `department`, `owner_id` or `role` as model-supplied arguments — enforce it
  with a registry-time assertion, not a convention.
- Every tool is read-only. A tool module that imports a write path is a review failure.
- Budgets (iterations, tool calls, tokens, wall clock) are enforced by the budget manager in code.
  Never rely on the prompt.
- Prompts live in `app/agent/prompts/` and `app/rag/prompts/` as versioned templates loaded through
  the prompt registry — never inline f-strings in logic code.
- The refusal string is imported from `app/rag/constants.py`, never retyped.
- Every node and every tool call emits a LangSmith span and a duration metric.
- Deterministic and post-flight gates (`auth`, injection scan, citation validation, output
  guardrail) live outside the graph's decision space — they are called by the service around the
  agent, never registered as tools.

---

## 11. Testing hooks in production code

- Design for injection: clients and clocks are passed in or provided via dependency overrides.
- No global mutable singletons other than the settings object and the connection pools.
- No network access at import time.
- Deterministic behaviour under test: seedable randomness, injectable `now()`.

---

## 12. Documentation and comments

- Docstrings on public functions: what, args, returns, raises. Skip the obvious.
- Comments explain *why*, not *what*. Delete commented-out code.
- Match the surrounding file's comment density and naming style.
- Type annotations replace most documentation — keep them accurate.

---

## 13. Forbidden

| Never | Instead |
|---|---|
| `print()` | structured logger |
| Mutable default arguments | `None` + in-body default |
| Bare `except:` / `except Exception: pass` | typed catch + log + re-raise |
| Business logic in a router | a service function |
| Reading `os.environ` outside `Settings` | inject `Settings` |
| Blocking I/O in `async def` | `anyio.to_thread.run_sync` |
| Hand-rolled Qdrant filters | the chokepoint builder |
| Inline prompt strings | the prompt registry |
| `# noqa` / `# nosec` without a reason | fix it, or justify inline |
| Wildcard imports | explicit imports |
