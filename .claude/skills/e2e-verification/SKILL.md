---
name: e2e-verification
description: Run the complete end-to-end test and verification sweep for the EKBA platform — static checks, unit/integration/security/agent suites, ingestion and agentic RAG flows, tenancy isolation, guardrail attack corpus, observability correlation, infrastructure validation and evaluation metrics. Use before any release, after any change to auth/tenancy/guardrails/agent tools, and whenever the user asks to verify, validate, or confirm the system works end to end.
---

# End-to-End Verification

Produces one verdict: **PASS** or **FAIL**, backed by pasted evidence. Nothing is "probably fine".

## Rules for this skill

1. **Report honestly.** If a stage fails, the run fails. Never summarize a red suite as green.
2. **Never fix by weakening.** No skipping a test, loosening a guardrail, widening CORS, or
   removing a tenant filter to get a pass.
3. **Never delete anything.** No non-project AWS resource, no secret, no `terraform destroy`, no
   Qdrant collection drop, no `FLUSHALL`. Verification is read-mostly by design.
4. **Evidence or it didn't happen.** Every stage's verdict is backed by pasted command output.
5. Test data is created in a dedicated verification tenant and is cleaned up only within that
   tenant's own namespace.

## Inputs

Ask for these if not supplied:

- Target: `local` | `dev` | `prod` (prod runs the read-only subset — see §Prod)
- Scope: `full` (default) | `fast` (stages 1–4, 6) | `security-only` (4, 6) | `agent-only` (5)

---

## Stage 0 — Preconditions

```bash
git status --short          # note uncommitted changes
git rev-parse --short HEAD  # record the SHA under test
```

Confirm the environment is reachable and that you are pointed at the intended AWS account and
cluster. Record: SHA, target, account id, cluster, namespace, image digest. If the target is not
what the user expects, stop.

---

## Stage 1 — Static verification

```bash
ruff check backend && black --check backend && mypy backend
cd frontend && npx tsc --noEmit && npm run lint && cd ..
terraform -chdir=infra/terraform/envs/<env> fmt -check
terraform -chdir=infra/terraform/envs/<env> validate
tflint --chdir infra/terraform && checkov -d infra/terraform --quiet
```

Also assert the repo-level invariants:

- No `terraform destroy` in `.github/`, `Makefile`, or `scripts/`
- No `delete-secret` / `delete_secret` anywhere outside a test asserting its absence
- `prevent_destroy` present on S3 buckets, secrets, KMS keys, ECR repos, the Cognito pool
- No secret patterns in the tree (`gitleaks detect`)

**Fail the run** on any hit. These are the safety invariants, not style preferences.

---

## Stage 2 — Unit and contract suites

```bash
pytest backend/tests/unit backend/tests/security backend/tests/agent -q \
  --cov=backend/app --cov-report=term-missing
```

Verify the coverage floors from `.claude/rules/testing.md` §3:

| Area | Floor |
|---|---|
| Overall | 85% |
| auth, tenancy chokepoint, guardrails, citation validation, tool registry/authorization, budgets | 100% |

Confirm zero skipped tests. A skip without a linked issue fails the stage.

---

## Stage 3 — Integration suites

```bash
make dev-up                              # local/dev only
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
pytest backend/tests/integration -q
```

Checks: database migrations reversible; Qdrant reachable with payload indexes present; Redis
reachable; S3/MinIO round-trip; Euri gateway client handles timeout, 429 and 5xx with the expected
retry/fallback behaviour.

---

## Stage 4 — Security verification (the stage that must never be shortened)

### 4.1 Authentication matrix

Every case returns 401 with a generic body and writes an audit event: invalid signature, wrong
issuer, wrong audience, expired, not-yet-valid, `alg: none`, HS256-signed-with-public-key, malformed,
missing, wrong `token_use`, unknown `kid`, disabled user.

### 4.2 Tenancy isolation — run against every endpoint

For each of `/chat`, `/search`, `/documents`, `/documents/{id}`, `/documents/{id}/download`,
`DELETE /documents/{id}`, `/conversations`, `/messages`, `/feedback`, `/admin/metrics`:

- Tenant A's token against tenant B's resource → denied, zero leakage, audit event
- A question whose answer exists only in tenant B → the exact refusal string, zero chunks
- A department the user is not granted → refusal, zero chunks

### 4.3 Authorization matrix

`user` vs `admin` × every endpoint. Non-owner delete denied. Admin delete allowed and audited.
Admin metrics scoped to the caller's tenant only.

### 4.4 Guardrail attack corpus

Run the full corpus and assert blocked-or-safely-refused, audit event written, metric emitted, no
partial leakage:

direct injection · indirect injection in an uploaded document · system-prompt extraction ·
instruction override · delimiter escape · cross-tenant probing · retrieval poisoning · oversized
input · unsupported file type · malicious filename (traversal, control chars, homoglyphs, overlong)
· unsafe HTML/script · token exhaustion · sensitive-data leakage

### 4.5 API surface

Security headers present; CORS rejects a non-allow-listed origin; trusted-host rejects a bad Host;
body-size and upload-size limits enforced; rate limits fire at exactly the configured thresholds
(`/chat` 20/min, `/search` 40/min, `/documents/upload` 5/min) and return `Retry-After`; production
errors are generic and carry a correlation ID; `/docs` and `/openapi.json` are not public in prod.

---

## Stage 5 — Agentic behaviour verification

### 5.1 Tool contract invariants

- Every registered tool is read-only (assert programmatically over `TOOL_REGISTRY`)
- No tool accepts `tenant_id`, `department`, `owner_id` or `role` as a model-supplied argument
- No shell, code-execution, HTTP-fetch, SQL or filesystem tool exists in the registry
- Every tool has a strict args schema, a returns schema, `allowed_roles` and a call cap
- Every content-returning tool returns full provenance

### 5.2 Tool-authorization matrix

Every tool × every role × in-scope/out-of-scope. All enforced inside the tool, not just at the
planner.

### 5.3 Agent attack corpus

- Unregistered tool name → typed error, nothing executed
- Model-supplied `tenant_id` / `department` / `role` → discarded, audited, scope unchanged
- Loop-cap abuse / denial-of-wallet → loop terminates, terminal reason `limit_exceeded`, audit + metric
- Tool-output injection (instructions inside a tool result) → treated as data, not followed
- Sub-agent scope confusion → same Principal, depth cap 2 honoured
- Reasoning-trace / tool-registry extraction → refused; response contains no plan or tool arguments

### 5.4 Budget enforcement

Each cap individually: 8 iterations, 12 tool calls, 4 per tool, 40 chunks, 60k tokens, 45 s wall
clock, depth 2. Each terminates the loop with the correct terminal reason.

### 5.5 Golden paths (agent autonomy actually working)

| Archetype | Expected agent behaviour |
|---|---|
| Single-hop factual | `hybrid_search` → answer with citation |
| Multi-hop comparison | `hybrid_search` → `compare_documents` → `calculator`, every figure cited |
| Tabular | `table_lookup` with row-level provenance |
| Media | `media_locate` → `transcript_segment`, citation carries the timestamp |
| Ambiguous | one `request_clarification`, then answer |
| Out of scope | `refuse` with the exact refusal string |

Assert the terminal reason recorded for each.

---

## Stage 6 — Functional end-to-end

1. **Ingestion**: upload the fixture corpus (PDF with tables and images, XLSX, MD, PNG, MP3, MP4).
   Assert every Qdrant point has all ten mandatory payload fields non-null; job rows reach a
   terminal state; the planted injection payload is quarantined and not indexed.
2. **Idempotency**: re-upload an identical file → no new points. Upload a modified file → v2
   created, v1 retired and excluded from retrieval.
3. **Answering**: ask in English and in a non-English language. Assert grounded answers, resolvable
   citations, answer language matching the question.
4. **Response contract**: all eleven fields present on every terminal path — cache hit, answered,
   refusal, clarification, limit exceeded.
5. **Refusal**: byte-compare against `INSUFFICIENT_EVIDENCE`.
6. **Fabricated citation**: force one; assert it is stripped and, if it was the only one, the
   refusal is returned.
7. **Cache**: identical question twice → second is a hit; the same question from a user with a
   different permission scope → **miss**; after an ingest → **miss** (kb_version bumped).
8. **Deletion**: delete a document → vectors gone, S3 marked, audit event written, subsequent
   answers no longer cite it.

---

## Stage 7 — Observability verification

Take one correlation ID from a real request and prove it appears in all five places:

```bash
curl -si .../chat -H "X-Correlation-ID: $CID" ...        # 1. response header
aws logs start-query --query-string "fields @message | filter correlation_id='$CID'"   # 2. app logs
# 3. ALB access logs                                     # 4. LangSmith run tag
psql -c "select trace_id from request_usage where trace_id='$CID'"                     # 5. usage row
```

Then confirm metrics exist and are moving: request count, 4xx, 5xx, p50/p95, cache hit ratio, auth
failures, model failures, Qdrant failures, failed ingestion jobs, tokens, cost, and the agent
metrics (iterations, tool calls, per-tool latency and error rate, loop-cap breaches, refusal rate by
terminal reason).

Confirm every alarm exists and is in `OK` (not `INSUFFICIENT_DATA`): unhealthy workloads, 5xx rate,
latency, deployment failure, database connection pressure, loop-cap breach rate, cost spike.

---

## Stage 8 — Infrastructure verification (read-only)

```bash
terraform -chdir=infra/terraform/envs/<env> plan -detailed-exitcode   # expect: no changes
kubectl -n ekba-<env> get pods -o wide
kubectl -n ekba-<env> get pod -o jsonpath='{.items[*].spec.securityContext.runAsNonRoot}'
```

Assert: plan is clean (no drift); pods non-root with read-only root filesystems; no secret material
in image layers; every project resource tagged `Project=ekba`; ElastiCache and the database have no
public endpoint; S3 buckets encrypted, versioned, public access blocked; ECR scan findings within
policy.

**Never apply anything from this skill.** A drifted plan is a finding to report, not to fix here.

---

## Stage 9 — Evaluation

```bash
make eval
```

Report all nine core metrics (retrieval precision, retrieval recall/hit rate, answer relevance,
faithfulness, citation correctness, refusal correctness, latency, token consumption, estimated cost)
plus the agent metrics (tool-selection accuracy, tool calls per question, loop efficiency,
clarification appropriateness, terminal-reason correctness), each against its threshold and against
the previous run.

---

## Output format

```
EKBA END-TO-END VERIFICATION
Target: <env>   Commit: <sha>   Image: <digest>   Started: <ts>

STAGE                          RESULT   EVIDENCE
0  Preconditions               PASS     <summary>
1  Static + safety invariants   PASS     <output>
2  Unit / contract              PASS     N passed, coverage X%
3  Integration                  PASS     N passed
4  Security                     FAIL     <exact failing case + output>
5  Agent behaviour              PASS     N cases
6  Functional E2E               PASS     <summary>
7  Observability                PASS     correlation id proven in 5/5 systems
8  Infrastructure               PASS     plan clean, 0 drift
9  Evaluation                   PASS     <metric table>

VERDICT: FAIL

BLOCKING ISSUES
1. <what failed, the evidence, and the smallest correct fix — never a control weakening>

NON-BLOCKING OBSERVATIONS
- <...>

NOT VERIFIED
- <anything skipped, and why>
```

Never omit the "NOT VERIFIED" section. If a stage could not run, say so — an unrun stage is not a
passing stage.

---

## Prod

Against prod, run the read-only subset only: stages 0, 1, 7, 8, plus health and smoke checks.
No attack corpus, no test tenant creation, no ingestion, no rate-limit saturation against
production. Say explicitly in the report which stages were omitted and why.
