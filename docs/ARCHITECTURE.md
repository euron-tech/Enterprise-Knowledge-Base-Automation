# Architecture — Enterprise Knowledge-Base Automation

Version 0.1 (design, pre-implementation) · Owner: platform team

---

## 1. Goals and constraints

**Goals**

- Department-scoped, tenant-isolated retrieval over heterogeneous enterprise documents.
- **Agentic reasoning**: a LangGraph planner autonomously decides which tools to call, how to
  decompose a question, when evidence suffices, and when to refuse — inside hard, code-enforced
  authorization and budget limits.
- Grounded, cited, multilingual answers; honest refusal when evidence is thin.
- Defensible security posture: authz enforced at the data layer, not the UI.
- Every request measurable — latency, tokens, cost, cache, route — and traceable end to end.
- Zero-downtime releases with automated rollback.

**Constraints**

- AWS only. EKS runtime. Terraform for all infrastructure.
- Model access exclusively via the **Euri AI Gateway** (Gemini embeddings, OpenAI generation).
  No direct provider SDK calls, no Bedrock.
- Vector store is **Qdrant**; OLTP is **Supabase PostgreSQL**; cache is **ElastiCache Redis**.
- Secrets only from AWS Secrets Manager. Dev and prod share credential values initially.

---

## 2. System context

```
┌────────────┐        ┌──────────────┐
│  Employee  │        │    Admin     │
└─────┬──────┘        └──────┬───────┘
      │  HTTPS               │
      ▼                      ▼
┌──────────────────────────────────────┐
│  React SPA (CloudFront + S3)         │
└────────────────┬─────────────────────┘
                 │ Bearer JWT (Cognito)
                 ▼
┌──────────────────────────────────────┐
│  ALB  →  Ingress  →  FastAPI (EKS)   │
└──┬───────┬────────┬────────┬─────────┘
   │       │        │        │
   ▼       ▼        ▼        ▼
Qdrant  Supabase  Redis     S3
   │                          │
   └──────────► Euri AI Gateway ◄── embeddings + generation
                 │
        CloudWatch + LangSmith
```

Actors:

| Actor | Capabilities |
|---|---|
| `user` | Ask questions, view own conversations, upload documents into permitted departments (if granted), give feedback |
| `admin` | Everything a user can do within their tenant, plus operational metrics, ingestion job control, document deletion, department grant management |

Tenancy: **`tenant_id` = company**. **`department`** is a scope *inside* a tenant. A user has one
`tenant_id` and a set of permitted departments. Both are enforced on every retrieval.

---

## 3. Component architecture

### 3.1 Frontend (React + TypeScript, Vite)

Visual language is inherited from the Euron CRM so the two products read as one suite — Tailwind +
shadcn/ui on Radix, lucide icons, Geist Variable / Geist Mono self-hosted, and the CRM's exact
colour, radius, shadow and breakpoint tokens including its multi-tenant brand override. Full token
set and responsive contract: [`DESIGN-SYSTEM.md`](DESIGN-SYSTEM.md).

- Cognito Hosted UI / Amplify auth → JWT in memory (not `localStorage`), refresh via secure
  http-only cookie where possible.
- Views: Chat (streamed answer + citation panel), Documents (upload, status, versions),
  Admin (metrics, jobs, grants, audit log), Feedback.
- Citation panel renders `source_uri` presigned links + page number; clicking a citation scrolls
  the source preview. A citation that fails to resolve renders as an error, never silently.
- Served as static assets from S3 behind CloudFront; API calls go to the ALB origin.

### 3.2 API service (FastAPI on EKS)

Routers:

| Route | Purpose | Rate limit |
|---|---|---|
| `POST /chat` | RAG answer | 20/min/user |
| `POST /search` | Retrieval only, no generation | 40/min/user |
| `POST /documents/upload` | Presigned upload + ingestion job | 5/min/user |
| `GET /documents` | List within tenant + permitted departments | 60/min/user |
| `DELETE /documents/{id}` | Ownership/admin verified delete | 10/min/user |
| `GET /conversations`, `/messages` | History | 60/min/user |
| `POST /feedback` | Thumbs / rating / comment | 30/min/user |
| `GET /admin/metrics` | Operational metrics (admin only) | 30/min/user |
| `GET /healthz`, `/readyz` | Probes (unauthenticated, tightly limited) | 60/min/IP |

Middleware order (outermost → innermost):

1. Correlation ID (accept inbound `X-Correlation-ID` or mint a UUIDv4)
2. Trusted host
3. HTTPS redirect / HSTS (prod)
4. CORS allow-list
5. Security headers
6. Request body size limit
7. Structured JSON access log
8. Exception handler (generic messages in prod, correlation ID always echoed)
9. Auth dependency (per-route)
10. Rate limiter (per-route policy, Redis token bucket)

### 3.3 Ingestion subsystem

```
upload (presigned PUT to S3)
   → S3 event / job row → ingestion worker (K8s Deployment, queue-driven)
   → virus/type/filename validation
   → parse by modality
   → normalize to a Document Element tree (text | table | image | av-segment)
   → indirect-injection scan per element
   → chunk (structure-aware)
   → embed via Euri (Gemini multimodal)
   → upsert to Qdrant with full payload
   → write documents + ingestion_jobs rows
   → emit metrics/trace
```

Modality handling:

> **Verified constraint:** the Euri gateway's embedding model is **text-only** — every multimodal
> input shape was rejected during live probing (see `INTEGRATIONS-EURI.md` §3). Every modality is
> therefore bridged into text before embedding. The raw asset stays in S3 and is still what a
> citation points at; only the *retrievable representation* is text.

| Modality | Approach |
|---|---|
| PDF / DOCX / PPTX | Layout-aware text + table extraction; embedded images extracted as image elements |
| XLSX / CSV | Sheet → table elements; each table serialized to Markdown for embedding |
| Images / diagrams | **Chat vision (`gpt-4.1`, verified working) generates a description → the description is embedded as text.** Description stored so the citation resolves to the real image |
| Audio | Transcribe → timestamped text segments (each segment is a chunk with a time offset) |
| Video | Keyframes → vision descriptions, plus audio transcript; both embedded as text, linked by timestamp |
| MD / TXT / HTML | Sanitized, heading-aware chunking |

**Embedding size guard.** The gateway returns HTTP 200 for absurdly oversized input rather than an
error, meaning it silently truncates. The chunker enforces its own hard token ceiling before every
call; a silently truncated embedding is a silently wrong embedding. See `INTEGRATIONS-EURI.md` §3.

**Embedding dimensions.** `gemini-embedding-2-preview` returns 3072 dimensions by default; the
`dimensions` parameter is honoured (768 verified). Project default is **1536**, benchmarked against
3072 in Phase 6. This must be fixed before the first production ingest — changing it later forces a
full re-embed.

Chunking: structure-aware (headings, table boundaries, slide/page boundaries), target ~800 tokens
with ~120 overlap, never splitting a table row. Every chunk gets a deterministic
`chunk_id = sha256(document_id || document_version || ordinal)`.

Idempotency: `checksum` (SHA-256 of the raw object) plus `document_version`. Re-uploading an
identical file is a no-op; a changed file creates a new version and the old version's points are
retired (soft-deleted by payload flag, then purged).

### 3.4 Agentic RAG graph (LangGraph)

The pipeline is **agentic**: an LLM planner decides, per question, what to do — which tools to
call, in what order, how many times, whether the evidence is sufficient, whether to ask a
clarifying question, and whether to refuse. It is not a fixed retrieve-then-generate chain.

The autonomy is deliberately sandwiched between deterministic gates:

```
┌─ DETERMINISTIC PRE-FLIGHT ─ the agent cannot skip, reorder or influence these ─┐
│  1 authenticate → 2 validate_input → 3 injection_scan → 4 cache_lookup         │
└───────────────────────────────────────┬────────────────────────────────────────┘
                                        ▼
┌─ AGENTIC CORE (LangGraph) ─────────────────────────────────────────────────────┐
│                                                                                 │
│   planner ──► tool_router ──► tool_executor ──► observation ──► reflector       │
│      ▲                                                             │            │
│      └──────────────── not sufficient, budget remains ─────────────┘            │
│      │                                                                          │
│      ├──► request_clarification  (ambiguous question)                           │
│      ├──► refuse                 (out of scope / no evidence possible)          │
│      └──► context_builder ──► model_router ──► generator                        │
│                                                                                 │
│   Every tool call: Principal injected server-side, tenant filter mandatory,     │
│   read-only, provenance-carrying, budget-counted, individually traced.          │
└───────────────────────────────────────┬────────────────────────────────────────┘
                                        ▼
┌─ DETERMINISTIC POST-FLIGHT ─ outside the agent's reach by design ──────────────┐
│  validate_citations → output_guardrail → record_usage → trace → respond        │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Why the sandwich.** An agent that can *decide* to skip authentication, tenant filtering or
citation validation is not an agent, it is a vulnerability. Planning is delegated to the model;
enforcement never is. The originally specified stage order is fully preserved — retrieval, metadata
filtering and relevance thresholding still happen in that order, but now inside tools the agent
chooses to invoke, and they remain non-negotiable when invoked.

#### Nodes of the agentic core

| Node | Responsibility | Exit conditions |
|---|---|---|
| `planner` | Given the question, the principal's scope, the evidence so far and the remaining budget, emit the next action | tool call · clarify · refuse · answer |
| `tool_router` | Look up the tool in the registry (filtered by role), validate arguments strictly, enforce per-tool call caps | typed tool error on failure |
| `tool_executor` | Execute with the server-side `Principal` injected; capture result, provenance and telemetry | typed error, never a stack trace |
| `observation` | Normalize the result into typed evidence, rescan for indirect injection, wrap in untrusted-content delimiters | quarantine on detection |
| `reflector` | Sufficiency critic — is every part of the question supported by permitted, above-threshold evidence? What is still missing? | continue · answer · refuse |
| `context_builder` | Order, dedupe, token budget, per-document dominance cap, delimiting | — |
| `model_router` | Complexity/cost-based route + fallback chain | — |
| `generator` | Grounded, cited answer from the assembled evidence only | fallback on upstream failure |

#### Sub-agents

Specialist subgraphs, invoked by the planner as tools, inheriting the same Principal and the same
budget:

| Sub-agent | Handles |
|---|---|
| `retrieval_specialist` | Query decomposition, multi-hop lookup, reformulation across languages |
| `media_specialist` | Diagrams, screenshots, audio and video — locate, describe, timestamp |
| `tabular_specialist` | Filtering and aggregating extracted tables with row-level provenance |
| `comparison_specialist` | Version and cross-document comparison (e.g. policy v3 vs v4) |

Sub-agent recursion depth is capped at 2.

#### Hard limits (code-enforced, never prompt-enforced)

| Limit | Default |
|---|---|
| Planner iterations per request | 8 |
| Tool calls per request | 12 |
| Calls to any single tool | 4 |
| Chunks entering final context | 40 |
| Tokens per request (in + out) | tenant policy, hard cap 60k |
| Wall clock per request | 45 s prod |
| Sub-agent recursion depth | 2 |

On breach the loop terminates and the agent answers from validated evidence, or returns the
refusal string. Breaches are audited and alarmed. Identical consecutive tool calls terminate the
branch (loop detection).

#### Terminal reasons

Every request ends with exactly one recorded terminal reason: `answered`,
`refused_insufficient_evidence`, `refused_guardrail`, `refused_out_of_scope`,
`clarification_requested`, `limit_exceeded`, `upstream_failure`.

The refusal string is a constant. `confidence` derives from retrieval score distribution, citation
coverage of the answer, reflector verdict and guardrail outcomes — reported honestly, never used to
justify fabrication.

### 3.4.1 Tool catalog

All tools are **read-only**, registered in a single registry, filtered by role before the model
sees them, and executed with a server-injected `Principal`. Full contract in
`.claude/rules/agents-and-tools.md`.

| Group | Tools |
|---|---|
| Retrieval | `semantic_search`, `keyword_search`, `hybrid_search`, `fetch_chunk`, `expand_context`, `get_page` |
| Document | `list_documents`, `get_document_metadata`, `summarize_document`, `compare_documents` |
| Modality | `table_lookup`, `image_describe`, `media_locate`, `transcript_segment` |
| Utility | `calculator` (sandboxed), `date_resolver`, `glossary_lookup`, `language_normalize`, `department_scope` |
| Control | `request_clarification`, `refuse`, `escalate` |
| Sub-agents | `retrieval_specialist`, `media_specialist`, `tabular_specialist`, `comparison_specialist` |

**Tools that must never exist:** shell or code execution, arbitrary HTTP fetch, raw SQL,
filesystem access, any write/delete/grant-mutation tool, and any tool accepting `tenant_id`,
`department`, `owner_id` or `role` as a model-supplied argument.

Every content-returning tool returns full provenance (`document_id`, `chunk_id`,
`document_version`, `page_number` or `time_offset_ms`, `source_uri`). Content without provenance
cannot be cited, and therefore cannot be used in an answer.

### 3.5 Model routing

```
route = f(question_length, retrieved_context_tokens, complexity_signal, tenant_tier)
  → primary model (Euri/OpenAI)
  → on 429/timeout/transient 5xx → fallback model
  → on total failure → 503 with correlation ID, recorded as model_failure metric
```

Verified tiers and prices (from `GET /models`, which returns pricing per model — the source of
truth for cost recording; never hard-code prices):

| Tier | Model | Input / output USD per 1M |
|---|---|---|
| Planner / cheap classification | `gpt-4.1-mini` | 0.40 / 1.60 |
| Trivial classification | `gpt-4.1-nano` | 0.10 / 0.40 |
| Generation (default) | `gpt-4.1` | per `/models` |
| Vision (ingestion bridge) | `gpt-4.1` | per `/models` |

**Retry caveat:** the gateway wraps upstream 4xx as HTTP 500. A 500 whose body mentions `400` is
permanent and must not be retried — see `INTEGRATIONS-EURI.md` §5. Blind 5xx retry burns budget on
invalid requests.

**Tool-call detection:** with a forced `tool_choice` the gateway reports `finish_reason: "stop"`
while still returning `tool_calls`. The agent branches on the presence of `message.tool_calls`,
never on `finish_reason`.

Route selection, fallback usage and cost land in `request_usage` and CloudWatch metrics. Chat
`usage` is accurate and used directly; **embedding `usage` is not** (always `total_tokens: 500`),
so embedding tokens are counted client-side.

### 3.6 Caching

Two layers in Redis:

1. **Deterministic** — exact hit on the composite key.
2. **Semantic** — embedding of the normalized question; nearest neighbour within the same key
   namespace above a similarity floor.

Cache key components (all of them, hashed):

```
normalized_question | tenant_id | permission_scope_hash | kb_version | prompt_version
                    | agent_version | tool_registry_hash | model | temperature
                    | top_k | relevance_threshold
```

`agent_version` and `tool_registry_hash` are included because a change to the planner prompt or to
the available tools can change the answer for an identical question — a stale cached answer from a
previous agent generation must not be served.

`permission_scope_hash` is the sorted set of permitted departments — this makes a cache hit
impossible across differing permission sets. `kb_version` bumps on any ingest/delete within the
tenant, invalidating stale answers. TTL default 24h, configurable per tenant.

### 3.7 Rate limiting

Redis token bucket keyed on `sub` (user) for authenticated routes and on hashed IP for
unauthenticated ones. Policies per §3.2. `429` responses include `Retry-After` and are counted as
a CloudWatch metric per route and per tenant.

---

## 4. Data architecture

### 4.1 PostgreSQL (Supabase) — tables

| Table | Key columns |
|---|---|
| `tenants` | `id`, `name`, `status`, `settings_json`, `kb_version`, `created_at` |
| `users` | `id` (Cognito `sub`), `tenant_id`, `email`, `role`, `departments[]`, `status`, `last_login_at` |
| `documents` | `id`, `tenant_id`, `department`, `name`, `s3_uri`, `mime_type`, `size_bytes`, `checksum`, `version`, `owner_id`, `status`, `page_count`, `created_at`, `retired_at` |
| `ingestion_jobs` | `id`, `document_id`, `tenant_id`, `state`, `stage`, `attempts`, `error_code`, `error_detail`, `started_at`, `finished_at`, `chunks_written` |
| `conversations` | `id`, `tenant_id`, `user_id`, `title`, `created_at`, `last_message_at` |
| `messages` | `id`, `conversation_id`, `role`, `content`, `citations_json`, `trace_id`, `created_at` |
| `request_usage` | `id`, `tenant_id`, `user_id`, `route`, `model`, `input_tokens`, `output_tokens`, `estimated_cost`, `latency_ms`, `cache_status`, `route_selected`, `fallback_used`, `prompt_version`, `trace_id`, `created_at` |
| `user_feedback` | `id`, `message_id`, `user_id`, `tenant_id`, `rating`, `reason`, `comment`, `created_at` |
| `prompt_releases` | `id`, `name`, `version`, `template`, `checksum`, `active`, `released_by`, `released_at` |
| `audit_events` | `id`, `tenant_id`, `actor_id`, `action`, `resource_type`, `resource_id`, `outcome`, `correlation_id`, `ip`, `metadata_json`, `created_at` |

Every tenant-scoped table has `tenant_id NOT NULL` with an index leading on `tenant_id`, and (where
Supabase RLS is enabled) a row-level policy pinning it to the JWT claim. Migrations via Alembic.

### 4.2 Qdrant

One collection per environment (`ekba_chunks_{env}`), tenant separation by mandatory payload
filter plus a payload index on `tenant_id` and `department`.

Payload (all mandatory):

```
document_id, chunk_id, document_name, page_number, source_uri, owner_id,
tenant_id, document_version, checksum, created_at
```

Plus operational fields: `department`, `modality`, `time_offset_ms` (a/v), `element_type`,
`status` (`active` | `retired`), `lang`.

### 4.3 S3

```
s3://ekba-{env}-documents/{tenant_id}/{department}/{document_id}/v{version}/{original_filename}
s3://ekba-{env}-derived/{tenant_id}/{document_id}/v{version}/{element_id}.{ext}
```

SSE-KMS, versioning on, public access blocked, TLS-only bucket policy, lifecycle to IA at 90 days.
Downloads are presigned, short-TTL, and only issued after the same tenant/department check that
retrieval uses.

---

## 5. Security architecture

Detailed in [`SECURITY.md`](../SECURITY.md) and `.claude/rules/security.md`. Summary of layers:

| Layer | Controls |
|---|---|
| Edge | HTTPS only, HSTS, WAF (rate + common rule sets), trusted hosts, CORS allow-list |
| Identity | Cognito JWT: signature (JWKS), `iss`, `aud`/client id, `exp`/`nbf`, token use claim |
| Authorization | Role (`user`/`admin`) + `tenant_id` + department grant; enforced in the query filter |
| Input | Size limits, MIME sniffing, extension allow-list, filename normalization, schema validation |
| AI | Direct + indirect injection scanning, untrusted-content delimiting, system-prompt-leak detection, citation validation, output guardrails, retrieval poisoning defence |
| Agent | Read-only tool registry, role-filtered tool exposure, server-injected Principal (never model-supplied), strict tool-argument schemas, per-tool authorization re-check, iteration/tool-call/token/wall-clock caps, loop detection, no shell/HTTP/SQL/file/write tools |
| Data | KMS everywhere, private subnets for Redis, no public DB endpoint, least-privilege IAM per service account (IRSA) |
| Supply chain | ECR image scanning, pinned digests, SBOM, non-root containers, no secrets in images |
| Ops | Correlation IDs, audit events, generic prod errors, encrypted log groups |

---

## 6. Observability architecture

**Correlation ID** is minted at the edge and propagated through: HTTP response header
(`X-Correlation-ID`), every JSON log line, ALB access logs (request id correlation), LangSmith run
tags, `request_usage.trace_id`, `audit_events.correlation_id`, and error records.

**CloudWatch**

- Logs: structured JSON, encrypted log groups, per-service streams.
- Metrics: EKS pod CPU/memory, request count, 4xx, 5xx, p50/p95 latency, failed ingestion jobs,
  model failures, Qdrant failures, cache hit ratio, auth failures, tokens, cost.
- Alarms: unhealthy EKS pods/nodes, high 5xx rate, high p95 latency, deployment failure,
  database connection pressure, ingestion backlog, cost spike.

**LangSmith** — end-to-end traces, retrieval traces (query, filters, scores, chunk ids), prompt
version, model routing decisions, latency, token usage, user feedback, evaluation runs.

**Agent-specific telemetry** — every trace shows the plan at each iteration, each tool call with
redacted arguments and outcome, the accumulated evidence set, the reflector's sufficiency verdict,
and the terminal reason. Metrics: iterations per request, tool calls per request, per-tool latency
and error rate, tool-error rate by code, loop-cap breaches, clarification rate, and refusal rate
broken down by reason.

---

## 7. Deployment architecture

### 7.1 Runtime

EKS, one cluster, namespace per environment. Workloads: `api` (Deployment + HPA), `ingest-worker`
(Deployment + KEDA/HPA on queue depth), `qdrant` (StatefulSet or managed Qdrant Cloud — see open
questions), `frontend` served from CloudFront. IRSA per service account. Non-root, read-only root
filesystem, dropped capabilities, resource requests/limits, PodDisruptionBudgets, network policies.

### 7.2 Blue/green with AWS CodeDeploy

```
push to main → GitHub Actions
  → lint, type, unit, security tests
  → build image, scan (ECR + trivy), sign
  → terraform plan (review gate on prod)
  → CodeDeploy blue/green: green replica set up, health + smoke tests
  → traffic shift (canary 10% → 100%)
  → bake window with alarm watch
  → success: retire blue   |   alarm trips: automatic rollback to blue
```

Rollback is automatic on any CloudWatch alarm in the deployment alarm set during bake. Manual
rollback is a documented one-command path in the deployment skill. **Rollback never deletes
infrastructure or secrets.**

### 7.3 Terraform layout

```
infra/terraform/
├── modules/{network,eks,ecr,s3,elasticache,cognito,secrets,observability,codedeploy,iam,waf}
└── envs/{dev,prod}/   # backend.tf (S3 + DynamoDB lock), main.tf, variables.tf
```

State in S3 with DynamoDB locking, per-environment. `prevent_destroy` lifecycle on S3 buckets,
Secrets Manager secrets, ECR repositories and the Cognito user pool.

---

## 8. Evaluation architecture

`backend/evals/dataset.jsonl` — records of `question`, `expected_answer`, `expected_document`,
`expected_keywords`. The harness (`backend/evals/run_eval.py`) reports retrieval precision,
retrieval recall/hit rate, answer relevance, faithfulness, citation correctness, refusal
correctness, latency, token consumption and estimated cost, writes results to LangSmith, and fails
CI when a metric regresses beyond its threshold.

---

## 9. Key design decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Qdrant, not Pinecone | Stated stack choice; self-hostable on EKS, payload filtering fits tenancy model |
| D2 | Euri AI Gateway, not Bedrock | Single gateway for Gemini embeddings + OpenAI generation; one auth path, one cost ledger |
| D3 | Tenant filter in the vector query, not post-filter | Post-filtering leaks existence; ANN with a mandatory filter cannot return foreign chunks |
| D4 | Permission scope in the cache key | Prevents a cache hit from crossing a permission boundary |
| D5 | Citation validation as a post-flight gate, not a prompt instruction and not an agent step | Prompts are advisory; a validator outside the agent's reach is enforcement |
| D5a | Agentic core sandwiched between deterministic gates | The model plans; it never decides what it is permitted to do |
| D5b | All tools read-only, with server-injected Principal | Removes the entire class of "model talked itself into wider scope" and "model deleted data" failures |
| D5c | Hard caps in code (iterations, tool calls, tokens, wall clock) | Agent loops are the primary cost and latency risk; prompts cannot bound them |
| D6 | Multimodal embeddings + text descriptions | Hybrid recall for diagrams/screenshots that pure image embeddings miss |
| D7 | Blue/green over rolling | Instant rollback, alarm-gated traffic shift |
| D8 | Secrets seeded once, read via External Secrets Operator | No secrets in images, env files or Git |
| D9 | Describe/transcribe-then-embed for all non-text modalities | The gateway's embedding model is verified text-only; chat vision is the bridge |
| D10 | Client-side token counting for embeddings | Gateway embedding `usage` is a flat constant and unusable for cost |
| D11 | Chunker enforces its own token ceiling | The gateway silently truncates oversized input instead of erroring |
| D12 | 1536 embedding dimensions (from 3072 default) | 2× memory saving in Qdrant; benchmarked in Phase 6 before lock-in |
| D13 | Model prices read from `GET /models`, cached | Prices are served by the gateway; hard-coding them guarantees drift |
| D14 | UI inherits the Euron CRM design system | One visual language across the suite; tokens already proven in production |

---

## 10. Open questions (need user input before Phase 2)

1. **Qdrant hosting** — self-managed StatefulSet on EKS, or Qdrant Cloud? Affects backup, HA cost.
2. ~~**Audio/video embedding path**~~ — **RESOLVED by live probing 2026-08-09.** The gateway's
   embedding model is text-only; all multimodal input shapes were rejected. Chat vision works and
   accepts data URIs. Design is now describe/transcribe-then-embed. See `INTEGRATIONS-EURI.md` §3.
3. **Supabase networking** — Supabase is a managed SaaS Postgres; "private RDS networking" cannot
   apply literally. Options: Supabase private networking/IP allow-list from the NAT gateway, or
   switch to RDS. Default assumption: Supabase with IP allow-list + TLS + RLS.
4. **CodeDeploy on EKS** — CodeDeploy's native blue/green supports ECS/Lambda/EC2. For EKS the
   options are (a) Argo Rollouts / Flagger driven by the pipeline, (b) CodeDeploy EC2 blue/green
   against node groups, (c) move the API to ECS. Default assumption: CodeDeploy orchestrates the
   pipeline stage while Argo Rollouts performs the in-cluster traffic shift.
5. **Dev/prod sharing credentials** — accepted as an explicit user decision; recorded as a risk.
6. **Data residency / retention** — any regional or per-tenant retention requirements?
