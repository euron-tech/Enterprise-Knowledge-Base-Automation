# Build Plan — Enterprise Knowledge-Base Automation

**Do not begin Phase 0 until the user has reviewed and approved the scaffolding documents.**

Legend: `[ ]` not started · `[~]` in progress · `[x]` done (DoD evidence pasted in the PR)

Rules for working this plan:

- Phases run in order. Within a phase, tasks may run in parallel unless a dependency is noted.
- A task is done only when its Definition of Done is demonstrated with real command output.
- Any change to the pipeline node order, the response contract, or the vector payload requires
  user sign-off before the task starts.
- Every phase ends with the `e2e-verification` skill run against the environment it touches.

---

## Phase 0 — Foundations (no AWS spend)

| ID | Task | Notes |
|---|---|---|
| 0.1 | `[ ]` Repo skeleton per `CLAUDE.md` §5 | backend/, frontend/, infra/, k8s/, .github/ |
| 0.2 | `[ ]` Python tooling: `pyproject.toml`, ruff, black, mypy strict, pytest config | Python 3.12 |
| 0.3 | `[ ]` Pre-commit: ruff, black, mypy, detect-secrets, gitleaks, terraform fmt/validate | |
| 0.4 | `[ ]` `.gitignore` + `.dockerignore` covering `.env`, `*.tfvars`, kubeconfig, `.pem` | |
| 0.5 | `[ ]` `.env.example` with every key, no values | Mirrors Secrets Manager names |
| 0.6 | `[ ]` `docker-compose.dev.yml`: Qdrant, Redis, Postgres, LocalStack, MinIO | Local-only |
| 0.7 | `[ ]` `Makefile`: dev-up, migrate, api, worker, test, lint, eval, deploy | |
| 0.8 | `[ ]` CI skeleton: lint + type + unit on PR | GitHub Actions |

**DoD:** `make lint` and `make test` run green on an empty test suite; pre-commit blocks a planted
fake secret; CI passes on a draft PR.

---

## Phase 1 — Core backend, auth, tenancy

| ID | Task | Depends |
|---|---|---|
| 1.1 | `[ ]` FastAPI app factory, settings via Pydantic Settings, env-aware config | 0.2 |
| 1.2 | `[ ]` Correlation-ID middleware + structured JSON logging | 1.1 |
| 1.3 | `[ ]` Security headers, trusted host, CORS allow-list, body-size limit middleware | 1.1 |
| 1.4 | `[ ]` Generic production error handler + typed error codes | 1.2 |
| 1.5 | `[ ]` Cognito JWT verifier: JWKS cache, sig/iss/aud/exp/nbf/token_use, alg allow-list | 1.1 |
| 1.6 | `[ ]` Principal model + RBAC dependency (`require_role`, `require_department`) | 1.5 |
| 1.7 | `[ ]` **Tenancy chokepoint**: the single function that builds every Qdrant filter | 1.6 |
| 1.8 | `[ ]` SQLAlchemy models for all ten tables + Alembic initial migration | 1.1 |
| 1.9 | `[ ]` Supabase connection, pooling, TLS, RLS policies where applicable | 1.8 |
| 1.10 | `[ ]` Redis client, token-bucket rate limiter, per-route policies | 1.1 |
| 1.11 | `[ ]` `audit_events` writer used by auth, authz, upload, delete, guardrails | 1.8 |
| 1.12 | `[ ]` `scripts/seed_secrets.py` — `.env` → Secrets Manager (create/update only, never delete) | 1.1 |
| 1.13 | `[ ]` `/healthz`, `/readyz` with dependency checks | 1.1 |
| 1.14 | `[ ]` `scripts/probe_euri.py` — re-verifies the whole `INTEGRATIONS-EURI.md` contract, prints no secret values, makes no writes | 1.1 |

**DoD:** unit tests cover every JWT failure mode (bad sig, wrong iss, wrong aud, expired, `alg:none`,
malformed); a test proves a Qdrant filter cannot be constructed without `tenant_id`; rate limiter
tests show 429 + `Retry-After` at the configured thresholds; `alembic upgrade head` and `downgrade`
both succeed; `seed_secrets.py` is idempotent and provably has no delete path (test asserts it).

---

## Phase 2 — Ingestion pipeline

| ID | Task | Depends |
|---|---|---|
| 2.1 | `[ ]` S3 client, canonical key builder, presigned upload/download | 1.1 |
| 2.2 | `[ ]` Upload endpoint: type sniffing, extension allow-list, size limit, filename sanitation | 2.1 |
| 2.3 | `[ ]` Parsers: PDF, DOCX, PPTX, XLSX/CSV, TXT/MD/HTML (sanitized) | 2.2 |
| 2.4 | `[ ]` Table extraction preserving row integrity → Markdown serialization | 2.3 |
| 2.5 | `[ ]` Image/diagram extraction → **chat-vision description** (the text bridge; embeddings are text-only) | 2.3, 2.10 |
| 2.6 | `[ ]` Audio transcription → timestamped segments (transcription provider TBD — see open question 2) | 2.3 |
| 2.7 | `[ ]` Video: keyframes → vision descriptions + transcript, linked by timestamp | 2.5, 2.6 |
| 2.8 | `[ ]` Document Element model + structure-aware chunker (no split rows) **with a hard token ceiling before embedding** | 2.3–2.7 |
| 2.9 | `[ ]` Indirect-injection scanner over every element; quarantine path | 2.8 |
| 2.10 | `[ ]` Euri gateway client per `INTEGRATIONS-EURI.md` §6: embeddings, chat, vision, tools, streaming, price cache from `/models`, permanent-vs-transient retry rule, circuit breaker, **client-side embedding token counting** | 1.1 |
| 2.11 | `[ ]` Qdrant client, collection bootstrap (dimensions fixed at 1536), payload indexes, upsert with all 10 fields | 2.10 |
| 2.12 | `[ ]` Versioning + checksum idempotency + retirement of old versions | 2.11 |
| 2.13 | `[ ]` Ingestion worker deployment + job state machine + retry/backoff | 2.12 |
| 2.14 | `[ ]` Document list / delete endpoints with ownership + admin checks | 2.12 |

**DoD:** a fixture corpus (PDF with tables and images, XLSX, MD, PNG, MP3, MP4) ingests end to end
in the local stack; every Qdrant point asserted to have all ten payload fields non-null; a planted
injection payload in a PDF is quarantined and never indexed; re-uploading the same file produces no
new points; a modified file produces v2 and retires v1; an over-ceiling chunk is rejected by our own
guard **before** reaching the gateway (proving we do not depend on the gateway to reject it); an
image-only document is retrievable via its generated description and its citation resolves to the
original image.

---

## Phase 3 — Deterministic gates and retrieval primitives

The rails the agent runs inside. Built first, deliberately, so autonomy is never added to an
unguarded system.

| ID | Task | Depends |
|---|---|---|
| 3.1 | `[ ]` Pre-flight chain: auth → input validation (length, encoding, language detect) → direct injection scan → cache lookup | 1.6 |
| 3.2 | `[ ]` Semantic + deterministic cache with the full composite key (incl. `agent_version`, `tool_registry_hash`) | 3.1 |
| 3.3 | `[ ]` Retrieval primitive via the tenancy chokepoint: ANN + payload filter, top-k cap | 1.7 |
| 3.4 | `[ ]` Metadata filter + relevance threshold primitives | 3.3 |
| 3.5 | `[ ]` Context builder: dedupe, order, token budget, per-document dominance cap, untrusted-content delimiters | 3.4 |
| 3.6 | `[ ]` Prompt registry backed by `prompt_releases` (versioned, checksummed) | 1.8 |
| 3.7 | `[ ]` Model routing + fallback chain | 3.5 |
| 3.8 | `[ ]` Generation client (Euri/OpenAI) with the grounded system prompt | 3.7 |
| 3.9 | `[ ]` **Citation validator** — post-flight gate; every citation maps to a retrieved `chunk_id` or is removed | 3.8 |
| 3.10 | `[ ]` Output guardrail gate: prompt-leak, PII/secret redaction, HTML/script sanitation | 3.9 |
| 3.11 | `[ ]` Confidence scoring (documented, reproducible formula) | 3.9 |
| 3.12 | `[ ]` Token/cost recorder → `request_usage` | 3.8 |
| 3.13 | `[ ]` Conversation + message persistence | 1.8 |

**DoD:** the pre-flight and post-flight chains are covered by tests proving they cannot be bypassed
by any input; refusal string is byte-identical to the constant; a test feeds a fabricated citation
into the validator and proves it is stripped; cross-tenant probe returns zero chunks; cache tests
prove differing permission scopes never share an entry.

---

## Phase 3A — Agentic core (LangGraph)

| ID | Task | Depends |
|---|---|---|
| 3A.1 | `[ ]` `AgentState` typed model: question, principal, evidence[], plan history, budgets, terminal reason | 3.1 |
| 3A.2 | `[ ]` Tool registry + `ToolSpec` contract (args schema, returns schema, allowed roles, cost class, per-request call cap, `read_only=True`) | 3A.1 |
| 3A.3 | `[ ]` Principal injection layer — strips and audits any model-supplied `tenant_id`/`department`/`role`/`owner_id` | 3A.2 |
| 3A.4 | `[ ]` Budget manager: iterations, tool calls, per-tool calls, chunks, tokens, wall clock; breach → terminate + audit + metric | 3A.1 |
| 3A.5 | `[ ]` `planner` node + versioned planner prompt with an explicit instruction hierarchy | 3A.2, 3.6 |
| 3A.6 | `[ ]` `tool_router` + `tool_executor` nodes with strict validation and typed tool errors | 3A.2 |
| 3A.7 | `[ ]` `observation` node: normalize to typed evidence, rescan for injection, delimit, record provenance | 3A.6 |
| 3A.8 | `[ ]` `reflector` node: sufficiency critic with explicit criteria + versioned prompt | 3A.7 |
| 3A.9 | `[ ]` Loop detection (identical consecutive call) + terminal-reason recording | 3A.4 |
| 3A.10 | `[ ]` Graph wiring: planner ⇄ tools ⇄ reflector, exits to clarify / refuse / answer | 3A.5–3A.9 |
| 3A.11 | `[ ]` Scripted-planner test harness (deterministic routing tests without model calls) | 3A.10 |
| 3A.12 | `[ ]` `/chat` and `/search` endpoints wired through pre-flight → agent → post-flight, full response contract | 3A.10, 3.13 |

**Tools** (each: module, strict schemas, role gate, provenance, unit tests, registry entry):

| ID | Tool group |
|---|---|
| 3A.13 | `[ ]` Retrieval: `semantic_search`, `keyword_search`, `hybrid_search`, `fetch_chunk`, `expand_context`, `get_page` |
| 3A.14 | `[ ]` Document: `list_documents`, `get_document_metadata`, `summarize_document`, `compare_documents` |
| 3A.15 | `[ ]` Modality: `table_lookup`, `image_describe`, `media_locate`, `transcript_segment` |
| 3A.16 | `[ ]` Utility: `calculator` (sandboxed, no `eval`), `date_resolver`, `glossary_lookup`, `language_normalize`, `department_scope` |
| 3A.17 | `[ ]` Control: `request_clarification`, `refuse`, `escalate` |
| 3A.18 | `[ ]` Sub-agents as tools: `retrieval_specialist`, `media_specialist`, `tabular_specialist`, `comparison_specialist` (depth cap 2) |
| 3A.19 | `[ ]` Agent telemetry: per-iteration plan, per-tool spans, evidence set, reflector verdict, terminal reason → LangSmith + CloudWatch |

**DoD:**

- Contract test asserts all eleven response fields on every terminal path (cache hit, answered,
  refusal, clarification, limit exceeded).
- Tool-authorization matrix test: every tool × every role × in-scope/out-of-scope, all enforced.
- A test proves a model-emitted `tenant_id` argument is discarded, audited, and does not widen scope.
- A test proves an unregistered tool name yields a typed error and no execution.
- Budget tests prove each cap terminates the loop and records the correct terminal reason.
- A multi-hop golden test shows the agent autonomously chaining search → compare → calculator with
  every figure cited.
- No tool in the registry is anything other than read-only (asserted programmatically).

---

## Phase 4 — Guardrails and AI security hardening

| ID | Task |
|---|---|
| 4.1 | `[ ]` Attack corpus: direct injection, indirect injection, prompt extraction, instruction override, delimiter escape, cross-tenant probing, poisoning, oversized input, malicious filenames, unsafe HTML |
| 4.1a | `[ ]` Agent attack corpus: unregistered-tool invocation, principal-argument override, loop-cap abuse / denial-of-wallet, tool-output injection, sub-agent scope confusion, reasoning-trace extraction, tool-registry disclosure |
| 4.2 | `[ ]` Red-team test suite executing the corpus against `/chat`, `/search`, `/documents/upload` |
| 4.3 | `[ ]` Token/cost quotas per request, user, tenant, day |
| 4.4 | `[ ]` Sensitive-data output scanner (keys, credentials, PII patterns) |
| 4.5 | `[ ]` Guardrail decision logging + CloudWatch metrics + audit events |
| 4.6 | `[ ]` Documented guardrail bypass-report process in `SECURITY.md` |

**DoD:** 100% of the attack corpus is blocked or safely refused; every block emits an audit event
with a correlation ID; no guardrail is disabled or weakened to make any test pass.

---

## Phase 5 — Observability

| ID | Task |
|---|---|
| 5.1 | `[ ]` CloudWatch JSON log shipping, encrypted log groups, retention policy |
| 5.2 | `[ ]` Custom metrics: request count, 4xx, 5xx, p50/p95, cache hit ratio, auth failures, model failures, Qdrant failures, failed ingestion jobs, tokens, cost |
| 5.2a | `[ ]` Agent metrics: iterations/request, tool calls/request, per-tool latency and error rate, loop-cap breaches, loop-detection triggers, clarification rate, refusal rate by terminal reason, sub-agent invocations |
| 5.3 | `[ ]` EKS pod CPU/memory metrics via Container Insights |
| 5.4 | `[ ]` Alarms: unhealthy workloads, 5xx rate, latency, deployment failure, DB connection pressure |
| 5.5 | `[ ]` LangSmith integration: traces, retrieval spans, prompt version, routing, tokens, feedback |
| 5.6 | `[ ]` Correlation-ID propagation proven across API, logs, ALB, LangSmith, audit, errors |
| 5.7 | `[ ]` CloudWatch dashboard per environment |

**DoD:** one request traced end to end, with the same id visible in the response header, a
CloudWatch Logs Insights query, the LangSmith run and the `request_usage` row — screenshots or
query output pasted.

---

## Phase 6 — Evaluation framework

| ID | Task |
|---|---|
| 6.1 | `[ ]` `backend/evals/dataset.jsonl` (≥ 60 records incl. refusal, cross-tenant, multi-hop, tabular and media cases; optional `expected_tools` per record) |
| 6.2 | `[ ]` `run_eval.py` computing all nine core metrics plus agent metrics (tool-selection accuracy, tool calls per question, loop efficiency, clarification appropriateness, terminal-reason correctness) |
| 6.3 | `[ ]` Thresholds config + regression gate |
| 6.4 | `[ ]` Results pushed to LangSmith, comparable across prompt versions |
| 6.5 | `[ ]` CI job running evals on PRs touching prompts, retrieval or the graph |

**DoD:** `make eval` prints all nine metrics; deliberately degrading the relevance threshold makes
the gate fail.

---

## Phase 7 — Frontend

Builds to [`docs/DESIGN-SYSTEM.md`](DESIGN-SYSTEM.md) — tokens extracted from the Euron CRM.

| ID | Task |
|---|---|
| 7.1 | `[ ]` Vite + React + TS scaffold, routing, API client with correlation-ID propagation |
| 7.2 | `[ ]` **Design system foundation**: Tailwind config mapping every token, `:root`/`.dark` blocks verbatim, self-hosted Geist + Geist Mono variable woff2 with unicode-range subsets, shadcn/ui at `--radius: 0.5rem`, lucide, sonner |
| 7.3 | `[ ]` Pre-paint theme + tenant-brand script, allowed by CSP **hash**; strict hex validation both sides; contrast floor with fallback |
| 7.4 | `[ ]` App shell: sticky header, collapsible sidebar (Sheet below `lg`), ⌘K command palette, minimal app footer (version + env badge) |
| 7.5 | `[ ]` Cognito auth flow, token refresh, no token in `localStorage` |
| 7.6 | `[ ]` Chat view: streaming, citation chips + citation panel, refusal card, clarification card, confidence meter, response metadata row, agent activity strip (coarse whitelisted steps only) |
| 7.7 | `[ ]` Documents view: upload, job status badges, quarantine banner, versions, delete |
| 7.8 | `[ ]` Admin view: metrics, jobs, grants, audit log, prompt releases |
| 7.9 | `[ ]` Feedback capture wired to `/feedback` |
| 7.10 | `[ ]` i18n, RTL support, accessible components, `prefers-reduced-motion` |
| 7.11 | `[ ]` Responsive pass at 320 / 640 / 768 / 1024 / 1440 / 1920 px |
| 7.12 | `[ ]` Vitest unit tests + Playwright e2e happy path, refusal path and mobile viewport |

**DoD:** Playwright proves login → ask → cited answer → click citation → source opens; unauthorized
department → refusal card shown with zero chunks. Light and dark both pass an axe scan and a
contrast audit. No horizontal body scroll at any tested width. A keyboard-only walkthrough
completes the chat flow. The agent activity strip is asserted to contain no tool names, arguments
or plan text.

---

## Phase 8 — Infrastructure (Terraform)

| ID | Task |
|---|---|
| 8.1 | `[ ]` Remote state: S3 bucket + DynamoDB lock table, per environment |
| 8.2 | `[ ]` `network` module: VPC, public/private subnets, NAT, endpoints, security groups |
| 8.3 | `[ ]` `eks` module: cluster, managed node groups, IRSA, add-ons, Container Insights |
| 8.4 | `[ ]` `ecr` module: repos, scan-on-push, lifecycle, `prevent_destroy` |
| 8.5 | `[ ]` `s3` module: documents + derived buckets, SSE-KMS, versioning, BPA, TLS-only, `prevent_destroy` |
| 8.6 | `[ ]` `elasticache` module: Redis in private subnets, encryption in transit/at rest, AUTH |
| 8.7 | `[ ]` `cognito` module: user pool, app client, groups (`user`, `admin`), custom attributes, `prevent_destroy` |
| 8.8 | `[ ]` `secrets` module: Secrets Manager entries, KMS key, rotation-ready, `prevent_destroy`, no delete path |
| 8.9 | `[ ]` `iam` module: least-privilege IRSA roles, no wildcard resources |
| 8.10 | `[ ]` `observability` module: log groups (KMS, retention), metric filters, alarms, dashboards, SNS |
| 8.11 | `[ ]` `waf` module: managed rule sets + rate rules on the ALB |
| 8.12 | `[ ]` `codedeploy` module: application, deployment groups, alarm-gated auto-rollback |
| 8.13 | `[ ]` `envs/dev` and `envs/prod` compositions with consistent `Project=ekba` tagging |
| 8.14 | `[ ]` tflint + checkov/tfsec in CI; `terraform destroy` never wired into any workflow |

**DoD:** `terraform plan` clean on both environments; `checkov` passes with no HIGH findings; a test
asserts every project resource carries `Project=ekba`; grep proves no `destroy` in `.github/`.

---

## Phase 9 — Kubernetes and packaging

| ID | Task |
|---|---|
| 9.1 | `[ ]` Multi-stage Dockerfiles (api, worker), non-root, read-only rootfs, no secrets, distroless/slim |
| 9.2 | `[ ]` Manifests/Helm: Deployments, Services, Ingress, HPA, PDB, NetworkPolicies, probes, resource limits |
| 9.3 | `[ ]` External Secrets Operator wired to Secrets Manager |
| 9.4 | `[ ]` Qdrant deployment (per the resolved open question) with backups |
| 9.5 | `[ ]` Argo Rollouts (or chosen mechanism) for in-cluster blue/green traffic shift |
| 9.6 | `[ ]` Namespace-scoped RBAC; no cluster-admin for workloads |

**DoD:** pods run as non-root (`kubectl get pod -o jsonpath` evidence); no secret material appears
in `docker history` or image layers; probes green; a NetworkPolicy test shows the worker cannot
reach the internet except the gateway endpoints.

---

## Phase 10 — CI/CD blue/green

| ID | Task |
|---|---|
| 10.1 | `[ ]` PR workflow: lint, type, unit, security tests, terraform validate/plan |
| 10.2 | `[ ]` Build workflow: image build, ECR scan gate, SBOM, sign, push by digest |
| 10.3 | `[ ]` Secret seeding step: `.env` → Secrets Manager, first-run create then update-only |
| 10.4 | `[ ]` Deploy-dev workflow: apply, migrate, blue/green shift, smoke tests |
| 10.5 | `[ ]` Deploy-prod workflow: manual approval gate, blue/green canary 10% → 100%, bake window |
| 10.6 | `[ ]` Alarm-gated automatic rollback |
| 10.7 | `[ ]` Documented, tested manual rollback path |
| 10.8 | `[ ]` OIDC federation for GitHub → AWS (no long-lived keys in CI) |

**DoD:** a green deploy to dev shifts traffic with zero failed requests during the shift; a
deliberately broken image triggers automatic rollback and the previous version serves traffic; the
rollback demonstrably deleted no infrastructure and no secret.

---

## Phase 11 — Hardening, docs, handover

| ID | Task |
|---|---|
| 11.1 | `[ ]` Load test to validate the p95 targets |
| 11.2 | `[ ]` Full `e2e-verification` skill run against prod |
| 11.3 | `[ ]` Cost report per tenant per day; alarm on cost spike |
| 11.4 | `[ ]` Runbooks: incident, rollback, key rotation, ingestion backlog, Qdrant recovery |
| 11.5 | `[ ]` Backup/restore drill for Postgres, Qdrant and S3 |
| 11.6 | `[ ]` Final security review; `SECURITY.md` updated with verified control status |
| 11.7 | `[ ]` Credential rotation plan to split dev and prod secrets |

**DoD:** all v1 acceptance criteria in `REQUIREMENTS.md` §8 demonstrated with evidence.

---

## Cross-cutting reminders

- Never delete non-project AWS infrastructure. Never delete a Secrets Manager secret.
- Never bypass the tenancy chokepoint or weaken a guardrail to pass a test.
- Never add a tool that writes, deletes, executes code, fetches arbitrary URLs, runs SQL or touches
  the filesystem. Every tool is read-only, forever.
- Never let the model supply a principal field, and never enforce a budget in a prompt.
- Never commit a secret; never print one.
- Report failures honestly and paste the output.
