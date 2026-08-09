# Rule — Testing

Applies to every test and to every piece of code that needs one (which is all of it).

---

## 1. Non-negotiables

1. **Never delete, skip, `xfail` or weaken a test to make a build pass.** Fix the code or fix the
   test's correctness — never its strictness.
2. **Never weaken a security control** (tenant filter, auth check, guardrail, budget cap) to make a
   test pass. If the control blocks the test, the test is wrong or the design is wrong.
3. **Never report green when it is not.** Paste real output. A skipped suite is not a passing suite.
4. **Tests never touch non-project AWS infrastructure**, never delete a secret, and never run
   `terraform destroy`.
5. Tests use fake credentials that are obviously fake (`test-key-not-real`), never redacted real
   ones.

---

## 2. Layout and markers

```
backend/tests/
  unit/          fast, no I/O, no network, no containers
  integration/   docker-compose stack: Qdrant, Redis, Postgres, LocalStack/MinIO
  security/      authn, authz, tenancy, guardrails, agent attack corpus
  agent/         graph routing, tool contracts, budgets, tool-authorization matrix
  e2e/           full API against a running stack
  evals/         RAG quality (separate from correctness tests)
  fixtures/      documents corpus, JWTs, tenants, attack payloads
```

Markers: `@pytest.mark.unit|integration|security|agent|e2e|slow`. Default `pytest` run executes
unit + security + agent. CI runs everything.

---

## 3. Coverage requirements

| Area | Minimum |
|---|---|
| Overall | 85% |
| `auth/` (JWT, principal, RBAC) | 100% |
| Tenancy chokepoint / filter builder | 100% |
| `security/` guardrails and sanitizers | 100% |
| Citation validation | 100% |
| Tool registry, tool authorization, budget manager | 100% |
| Ingestion parsers | 80% |
| Frontend components | 70% |

Coverage is a floor, not a goal. A 100% covered function with no assertion about tenancy is worth
nothing.

---

## 4. How to write a test here

- **Arrange–Act–Assert**, one behaviour per test, a name that states the behaviour:
  `test_chat_returns_refusal_when_all_chunks_below_threshold`.
- Assert on **behaviour and contract**, not implementation details.
- Parametrize over cases instead of copy-pasting.
- No sleeps — use fake clocks and deterministic waits.
- No network in unit tests. No live AWS, ever, in unit or integration tests (LocalStack/MinIO).
- Fakes over mocks where practical: an in-memory Qdrant fake beats six `patch` decorators.
- Seed randomness; inject `now()`.
- Every bug fix ships with a regression test that fails before the fix.

---

## 5. Mandatory test suites

### 5.1 Authentication

Every failure mode, individually: invalid signature, wrong issuer, wrong audience, expired, not yet
valid, `alg: none`, algorithm confusion (HS256 signed with the public key), malformed token, missing
token, wrong `token_use`, unknown `kid`, revoked/disabled user, user from a different tenant than
the token claims.

### 5.2 Tenancy and authorization (the most important suite in the repo)

- Cross-tenant probe on **every** endpoint: chat, search, list, download, delete, metrics, feedback,
  conversation history. Expected: zero leakage, correct status, audit event.
- A test that constructing a Qdrant filter without a `tenant_id` **raises**.
- A test that every call site of the vector client goes through the chokepoint (static check or
  registry assertion).
- Department-grant matrix: granted / not granted × each operation.
- Role matrix: `user` vs `admin` × each endpoint.
- Ownership: non-owner delete denied; admin delete allowed and audited.

### 5.3 Agent suites

- **Tool-authorization matrix**: every tool × every role × in-scope/out-of-scope.
- **Principal override**: a model-emitted `tenant_id`/`department`/`role` argument is discarded,
  audited, and does not widen scope.
- **Unregistered tool**: dispatch returns a typed error and executes nothing.
- **Read-only assertion**: a programmatic check that no registered tool exposes a write path.
- **Budget enforcement**: each cap (iterations, tool calls, per-tool calls, chunks, tokens, wall
  clock, recursion depth) terminates the loop with the correct terminal reason.
- **Loop detection**: identical consecutive calls terminate the branch.
- **Scripted planner**: routing tested deterministically with a stub planner, no model calls.
- **Golden paths**: expected tool sequence per question archetype — single-hop, multi-hop, tabular,
  media, comparison, ambiguous, out-of-scope.
- **Argument validation**: malformed tool arguments produce a typed error, never a partial run or a
  stack trace.

### 5.4 Guardrails / red team

The full attack corpus: direct injection, indirect injection in uploaded documents, system-prompt
extraction, instruction override, delimiter escape, cross-tenant probing, retrieval poisoning,
oversized input, unsupported file types, malicious filenames, unsafe HTML/script, token exhaustion,
sensitive-data leakage — plus the agent attacks in §5.3.

Every corpus entry asserts: blocked or safely refused, an audit event written, a metric emitted, and
no partial leakage in the response.

### 5.5 Contract tests

- All eleven `/chat` fields present on every terminal path: cache hit, answered, refusal,
  clarification, limit exceeded, upstream failure.
- The refusal string is byte-identical to the constant (compare to the imported constant, and also
  to a hard-coded literal in the test so a change to the constant is caught).
- Every Qdrant point written by ingestion has all ten payload fields non-null.
- A fabricated citation from the model is stripped; if it was the only one, the refusal is returned.

### 5.6 Ingestion

Fixture corpus covering every supported type, plus: corrupt file, zip bomb, polyglot file,
mismatched extension, 500-character filename, path-traversal filename, unicode-homoglyph filename,
empty file, oversized file, password-protected PDF, scanned image PDF, embedded injection payload.

### 5.7 Infrastructure

`terraform validate`, `tflint`, `checkov`/`tfsec`, a tag-policy test (`Project=ekba` on every
resource), a `prevent_destroy` test on protected resources, and a grep test proving no
`terraform destroy` exists in `.github/` or `Makefile`.

---

## 6. Evaluations are not tests

`backend/evals/` measures RAG quality (precision, recall/hit rate, relevance, faithfulness, citation
correctness, refusal correctness, latency, tokens, cost, plus agent metrics: tool-selection
accuracy, tool calls per question, loop efficiency, terminal-reason correctness).

They run separately from `pytest`, gate on thresholds, and publish to LangSmith. A correctness test
must never depend on an eval metric, and an eval must never be relaxed to hide a regression.

---

## 7. CI

| Stage | Runs |
|---|---|
| PR | lint, format, mypy, unit, security, agent, terraform validate/plan |
| PR (labelled or on relevant paths) | integration, evals |
| main | everything + build + scan + deploy to dev |
| release | everything + prod blue/green |

CI never runs `terraform destroy`, never deletes a secret, and never touches non-project resources.

---

## 8. Forbidden

| Never | Instead |
|---|---|
| `pytest.mark.skip` without a linked issue and an expiry | Fix it |
| Deleting a failing security test | Fix the code |
| Mocking the tenancy filter away | Use a real filter with a test principal |
| Asserting only "no exception raised" | Assert the actual contract |
| Tests depending on execution order | Independent, isolated tests |
| Real AWS calls in unit/integration | LocalStack/MinIO/fakes |
| Real secrets in fixtures | Obvious fakes |
| Broad `patch` of an entire module | Inject a fake at the seam |
