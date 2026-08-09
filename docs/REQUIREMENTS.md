# Requirements — Enterprise Knowledge-Base Automation

Version 0.1 · Status: awaiting user review · Every requirement is testable and IDed.

Priority: **M** = must have (v1), **S** = should have, **C** = could have (post-v1).

---

## 1. Scope

In scope for v1: multi-tenant document ingestion (all listed modalities), department-scoped RBAC
retrieval, multilingual grounded Q&A with citations, admin metrics, AI + API security controls,
CloudWatch/LangSmith observability, evaluation harness, Terraform IaC, blue/green CI/CD on AWS.

Out of scope for v1: fine-tuning, on-prem deployment, SSO providers other than Cognito, real-time
collaborative editing, mobile apps, automated document authoring.

---

## 2. Actors and roles

| Role | Description |
|---|---|
| `user` | Member of one tenant, granted zero or more departments. Asks questions, uploads (if granted), sees own history, gives feedback. |
| `admin` | Tenant administrator. All user rights within the tenant, plus grants, ingestion control, document deletion, operational metrics, audit log. |

**R-ROLE-1 (M)** A user belongs to exactly one `tenant_id`.
**R-ROLE-2 (M)** Department grants are per-user, per-tenant, and stored server-side; the JWT is not
trusted as the sole source of grants — grants are re-read from the database per request.
**R-ROLE-3 (M)** Admins can read operational metrics only for their own tenant. Cross-tenant
metrics require a separate platform-operator role (post-v1).

---

## 3. Functional requirements

### 3.1 Ingestion

| ID | Pri | Requirement |
|---|---|---|
| R-ING-1 | M | Accept PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, HTML, PNG, JPG, TIFF, MP3, WAV, M4A, MP4, MOV. |
| R-ING-2 | M | Reject any other MIME type or extension, with a clear 415, before any parsing occurs. |
| R-ING-3 | M | Validate real content type by magic-byte sniffing, not by extension or client-declared type. |
| R-ING-4 | M | Store the raw file unmodified in S3 at the canonical key; S3 is the system of record. |
| R-ING-5 | M | Extract text, tables, images, diagrams from documents; transcribe audio; transcribe + keyframe video. |
| R-ING-6 | M | Preserve table structure through chunking — never split a table row across chunks. |
| R-ING-7 | M | Chunk structure-aware with page/slide/segment attribution; every chunk knows its page or time offset. |
| R-ING-8 | M | Embed every chunk via the Euri AI Gateway Gemini embedding model. |
| R-ING-8a | M | **Because the gateway's embedding model is verified text-only**, every non-text modality is bridged to text before embedding: images/diagrams via chat vision description, audio via transcription, video via keyframe descriptions + transcript. The raw asset remains the citation target. |
| R-ING-8b | M | The chunker enforces its own hard token ceiling before every embedding call. The gateway silently truncates oversized input rather than erroring, so relying on it would produce silently wrong embeddings. Chunks approaching the limit are logged and alarmed. |
| R-ING-8c | M | Embedding token counts are computed client-side. The gateway's embedding `usage` is a flat constant (`total_tokens: 500` for every input size) and must never be used for cost. |
| R-ING-8d | M | Embedding dimensionality is a fixed configuration value (default 1536, from the model's 3072 default) decided before the first production ingest; changing it requires a full re-embed and is a migration, not a config tweak. |
| R-ING-9 | M | Write every chunk to Qdrant with all ten mandatory payload fields populated (no nulls). |
| R-ING-10 | M | Scan every extracted element for indirect prompt injection; quarantine and flag on detection, never index silently. |
| R-ING-11 | M | Idempotent: identical `checksum` + tenant + department is a no-op returning the existing document. |
| R-ING-12 | M | Changed content creates `version + 1`; prior version's points are marked `retired` and excluded from retrieval, then purged after the retention window. |
| R-ING-13 | M | Every ingestion attempt writes an `ingestion_jobs` row with stage, attempts, and a machine-readable error code on failure. |
| R-ING-14 | M | Failed jobs are retryable by an admin without re-uploading the file. |
| R-ING-15 | M | Sanitize filenames (path traversal, control characters, unicode homoglyphs, overlong names) before use in any key or log. |
| R-ING-16 | S | Ingestion is asynchronous; upload returns a job id immediately and the UI polls status. |
| R-ING-17 | S | Per-tenant ingestion quotas (documents, bytes, monthly embedding tokens). |

### 3.2 Retrieval and Q&A

| ID | Pri | Requirement |
|---|---|---|
| R-RAG-1 | M | The pipeline is agentic (see §3.2a) but the deterministic pre-flight and post-flight gates defined in `ARCHITECTURE.md` §3.4 always execute, in order, and the agent can neither skip nor reorder them. |
| R-RAG-2 | M | Every Qdrant query carries a `tenant_id` filter and a permitted-department filter. A query built without them must fail closed at runtime, not just in review. |
| R-RAG-3 | M | Answers are generated **only** from retrieved context. No parametric knowledge, no outside facts. |
| R-RAG-4 | M | When no chunk clears the relevance threshold, or citation validation leaves no valid citation, return exactly: `I could not find enough evidence in the approved documents to answer this question.` |
| R-RAG-5 | M | Every factual statement in an answer carries at least one citation resolving to a retrieved `chunk_id`. |
| R-RAG-6 | M | A citation that does not map to a retrieved chunk is removed; if that empties the citation set, the refusal is returned. Citations are never fabricated. |
| R-RAG-7 | M | Questions in any language are answered in the language of the question, citing source documents in their original language. |
| R-RAG-8 | M | `POST /chat` returns all eleven contract fields, always. |
| R-RAG-9 | M | `confidence` is computed from retrieval scores, citation coverage and guardrail outcomes, and is documented and reproducible. |
| R-RAG-10 | M | `POST /search` returns ranked chunks with metadata and no generated text, under the same authorization rules. |
| R-RAG-11 | S | Streamed responses for `/chat`, with citations emitted after validation completes. |
| R-RAG-12 | S | Conversation memory scoped to a conversation, tenant and user; prior turns never bypass retrieval authorization. |

### 3.2a Agent behaviour and tools

The system is agentic: a LangGraph planner autonomously decides how to answer each question.

| ID | Pri | Requirement |
|---|---|---|
| R-AGT-1 | M | A LangGraph agent plans each request: it selects tools, decides call order and repetition, judges evidence sufficiency, and chooses to answer, clarify or refuse. No hard-coded retrieve-then-generate path. |
| R-AGT-2 | M | The agent operates strictly between the deterministic pre-flight and post-flight gates and cannot skip, reorder, disable or influence any gate. |
| R-AGT-3 | M | Graph state is a typed model; every node is independently unit-testable without the graph. |
| R-AGT-4 | M | Only tools present in the central registry are exposed to the model, and the registry is filtered by the caller's role before exposure. |
| R-AGT-5 | M | **Every tool is read-only.** No tool writes, deletes, retires, or changes grants, tenants or configuration. |
| R-AGT-6 | M | `tenant_id`, `department`, `owner_id`, `role`, `user_id` and `correlation_id` are injected server-side from the verified JWT. A model-supplied value for any of these is discarded and audited as a security event. |
| R-AGT-7 | M | Tool arguments are validated by a strict Pydantic schema (`extra="forbid"`); invalid arguments return a typed tool error, never a partial execution or a stack trace. |
| R-AGT-8 | M | Each tool re-checks role and department authorization at call time; authorization is never inherited from the planner's decision. |
| R-AGT-9 | M | Every content-returning tool returns full provenance. Content lacking provenance cannot be cited and must not be used in an answer. |
| R-AGT-10 | M | Tool results are treated as untrusted data: rescanned for indirect injection, wrapped in untrusted-content delimiters, never interpreted as instructions. |
| R-AGT-11 | M | Hard caps enforced in code, not by prompt: ≤ 8 planner iterations, ≤ 12 tool calls per request, ≤ 4 calls to any one tool, ≤ 40 chunks in context, ≤ 60k tokens per request, ≤ 45 s wall clock (prod), ≤ 2 sub-agent recursion depth. |
| R-AGT-12 | M | Cap breaches terminate the loop, answer from validated evidence or refuse, and emit an audit event and a metric. They are never silently retried. |
| R-AGT-13 | M | Loop detection: an identical tool call with identical arguments twice in succession terminates that branch. |
| R-AGT-14 | M | The following tools must never exist: shell/code execution, arbitrary HTTP fetch, raw SQL, filesystem access, and any write, delete or permission-mutation tool. |
| R-AGT-15 | M | The tool catalogue of `ARCHITECTURE.md` §3.4.1 is implemented: retrieval, document, modality, utility and control groups. |
| R-AGT-16 | M | Sub-agents (`retrieval_specialist`, `media_specialist`, `tabular_specialist`, `comparison_specialist`) are compiled subgraphs inheriting the caller's Principal and the same budgets. |
| R-AGT-17 | M | The agent may request clarification at most once per conversation turn, via the `request_clarification` control tool. |
| R-AGT-18 | M | Every request records exactly one terminal reason: `answered`, `refused_insufficient_evidence`, `refused_guardrail`, `refused_out_of_scope`, `clarification_requested`, `limit_exceeded`, `upstream_failure`. |
| R-AGT-19 | M | Planner and reflector prompts are versioned in `prompt_releases` and referenced by version in every trace and usage record. |
| R-AGT-20 | M | The trace for every request shows the plan per iteration, every tool call with redacted arguments and outcome, the evidence set, the reflector verdict and the terminal reason. |
| R-AGT-21 | M | A scripted-planner test harness allows graph routing to be tested deterministically without model calls. |
| R-AGT-22 | S | Golden-path tests assert the expected tool sequence for representative question archetypes (single-hop, multi-hop, tabular, media, comparison, out-of-scope). |
| R-AGT-23 | S | The agent decomposes multi-part questions and answers each part with its own citations, or refuses the unsupported parts explicitly. |

### 3.2b Model gateway integration

Contract verified against the live API on 2026-08-09 — see [`INTEGRATIONS-EURI.md`](INTEGRATIONS-EURI.md).

| ID | Pri | Requirement |
|---|---|---|
| R-GW-1 | M | All model access goes through one typed Euri client. No route, node or tool calls the gateway directly. |
| R-GW-2 | M | The API key is read from Secrets Manager at `ekba/<env>/euri-api-key`. Never from a file, never logged, never in an image. |
| R-GW-3 | M | Model prices are read from `GET /models` and cached with a TTL. Prices are never hard-coded. |
| R-GW-4 | M | Retry policy distinguishes permanent from transient: a 500 whose body reports an upstream `400` is permanent and must not be retried; 401/403/400 fail immediately; 429 and genuine 5xx retry with bounded jittered backoff. |
| R-GW-5 | M | The agent detects tool calls by the presence of `message.tool_calls`, never by `finish_reason` (which is unreliable when `tool_choice` is forced). |
| R-GW-6 | M | Explicit connect and read timeouts on every call; a per-model circuit breaker routes to the fallback and emits a metric on trip. |
| R-GW-7 | M | Chat `usage` is recorded as returned; embedding tokens are counted client-side (R-ING-8c). |
| R-GW-8 | M | The client is stateless with respect to cookies — the gateway sets an `AWSALB` cookie which must never be shared across tenants or requests. |
| R-GW-9 | M | Parallel tool calls returned in a single response are executed concurrently, within the per-request tool-call budget. |
| R-GW-10 | M | `scripts/probe_euri.py` re-verifies the full contract in this document and prints no secret values. |

### 3.2c Frontend design system

| ID | Pri | Requirement |
|---|---|---|
| R-UI-1 | M | The UI implements the design tokens in [`DESIGN-SYSTEM.md`](DESIGN-SYSTEM.md) verbatim — colours, radius, shadows, breakpoints — in both light and dark themes. |
| R-UI-2 | M | Typography uses self-hosted Geist Variable / Geist Mono Variable with unicode-range subsets; identifiers (chunk/document/trace/correlation ids, checksums, costs) render in the mono stack. |
| R-UI-3 | M | Theme and tenant brand colour are applied before first paint to avoid a flash; the inline script is permitted by a **CSP hash**, never `unsafe-inline`. |
| R-UI-4 | M | Tenant brand hex is strictly validated on the server and the client, and is rejected (with fallback + admin warning) if it fails the contrast floor. |
| R-UI-5 | M | Fully responsive at 320, 640, 768, 1024, 1440 and 1920 px. The page body never scrolls horizontally; wide tables, code and diagrams scroll inside their own containers. |
| R-UI-6 | M | Touch targets ≥ 44×44 px on coarse pointers; `dvh` used for full-height panels; safe-area insets respected. |
| R-UI-7 | M | Citations, refusals, clarifications, confidence and agent activity render as the first-class components specified in `DESIGN-SYSTEM.md` §8. A refusal is never styled as an error. |
| R-UI-8 | M | The agent activity strip shows only coarse whitelisted steps — never the plan, tool arguments or raw tool output. |
| R-UI-9 | M | WCAG AA contrast on every pairing, in both themes; colour is never the only signal; `prefers-reduced-motion` honoured. |
| R-UI-10 | M | RTL layout support via logical properties — the product answers in any language. |

### 3.3 Caching

| ID | Pri | Requirement |
|---|---|---|
| R-CACHE-1 | M | Deterministic cache lookup precedes semantic lookup. |
| R-CACHE-2 | M | The cache key includes normalized question, `tenant_id`, permission-scope hash, KB version, prompt version, agent version, tool-registry hash, model, and generation parameters. |
| R-CACHE-3 | M | A cache entry can never be served to a user whose permission scope differs from the one that produced it. |
| R-CACHE-4 | M | Any ingest, version change or delete within a tenant bumps `kb_version`, invalidating that tenant's cached answers. |
| R-CACHE-5 | M | `cache_hit` in the response reflects reality; a semantic hit is reported distinctly from a deterministic hit in metrics. |
| R-CACHE-6 | S | Configurable TTL per tenant; default 24 hours. |

### 3.4 Documents and lifecycle

| ID | Pri | Requirement |
|---|---|---|
| R-DOC-1 | M | Listing returns only documents in the caller's tenant and permitted departments. |
| R-DOC-2 | M | Deletion requires ownership or the admin role, verified server-side against the database. |
| R-DOC-3 | M | Deleting a document removes its Qdrant points and marks the S3 objects for lifecycle deletion; the audit event is written first. |
| R-DOC-4 | M | Presigned download URLs are issued only after the same authorization check as retrieval, with a short TTL. |
| R-DOC-5 | S | Document version history is visible in the UI with a diff of extracted text. |

### 3.5 Admin and audit

| ID | Pri | Requirement |
|---|---|---|
| R-ADM-1 | M | `GET /admin/metrics` exposes request counts, latency percentiles, cache hit ratio, error rates, token and cost totals, ingestion job states — scoped to the tenant. |
| R-ADM-2 | M | Every security-relevant action writes an `audit_events` row: login failure, authz denial, upload, delete, grant change, injection detection, guardrail block, admin metric access. |
| R-ADM-3 | M | Audit events are append-only; no API path updates or deletes them. |
| R-ADM-4 | S | Admins can retry, cancel and inspect ingestion jobs. |

### 3.6 Feedback and evaluation

| ID | Pri | Requirement |
|---|---|---|
| R-EVAL-1 | M | Users can rate an answer and give a reason; feedback links to `message_id` and `trace_id`. |
| R-EVAL-2 | M | `backend/evals/dataset.jsonl` exists with records of `question`, `expected_answer`, `expected_document`, `expected_keywords`. |
| R-EVAL-3 | M | The evaluation script measures retrieval precision, retrieval recall/hit rate, answer relevance, faithfulness, citation correctness, refusal correctness, latency, token consumption and estimated cost. |
| R-EVAL-3a | M | Agent-specific evaluation metrics: tool-selection accuracy against the expected tool set, average tool calls per question, loop efficiency (iterations to answer), clarification appropriateness, and terminal-reason correctness. |
| R-EVAL-4 | M | The dataset includes negative cases whose correct behaviour is the refusal string, and cross-tenant probes whose correct behaviour is zero retrieved chunks. |
| R-EVAL-5 | M | Evaluation runs in CI and fails the build on regression beyond configured thresholds. |
| R-EVAL-6 | S | Evaluation results are pushed to LangSmith and comparable across prompt versions. |

---

## 4. Security requirements

Full detail in [`SECURITY.md`](../SECURITY.md). Requirements here are the testable contract.

### 4.1 Authentication (Cognito)

| ID | Pri | Requirement |
|---|---|---|
| R-AUTH-1 | M | Verify JWT signature against the Cognito JWKS, with key caching and rotation handling. |
| R-AUTH-2 | M | Verify `iss` matches the configured user pool. |
| R-AUTH-3 | M | Verify `aud` (or `client_id` for access tokens) matches the configured app client. |
| R-AUTH-4 | M | Verify `exp` and `nbf` with minimal clock skew tolerance. |
| R-AUTH-5 | M | Verify `token_use` matches the expected token type. |
| R-AUTH-6 | M | Reject `alg: none`, algorithm confusion, and unsigned or malformed tokens. |
| R-AUTH-7 | M | Authentication failures are logged as audit events and counted as a CloudWatch metric. |

### 4.2 Authorization

| ID | Pri | Requirement |
|---|---|---|
| R-AUTHZ-1 | M | Users access only documents in their own tenant. |
| R-AUTHZ-2 | M | Department grants gate retrieval, listing, download and deletion. |
| R-AUTHZ-3 | M | Only admins reach operational metrics. |
| R-AUTHZ-4 | M | Ownership (or admin) is verified before deletion. |
| R-AUTHZ-5 | M | Every Qdrant query applies a `tenant_id` filter — enforced by a single chokepoint function that cannot be bypassed. |
| R-AUTHZ-6 | M | Authorization is enforced server-side; no client-supplied `tenant_id`, `department`, `role` or `owner_id` is ever trusted. |

### 4.3 AI security and guardrails

| ID | Pri | Requirement |
|---|---|---|
| R-AI-1 | M | Detect and block direct prompt injection in user input. |
| R-AI-2 | M | Detect indirect prompt injection embedded in uploaded documents, at ingestion and again at context construction. |
| R-AI-3 | M | Block attempts to reveal the system prompt. |
| R-AI-4 | M | Block attempts to override system instructions ("ignore previous", role-play escapes, delimiter breaking). |
| R-AI-5 | M | Prevent cross-tenant document access through prompt manipulation. |
| R-AI-6 | M | Reject oversized input at the middleware layer before it reaches the model. |
| R-AI-7 | M | Reject unsupported file types. |
| R-AI-8 | M | Sanitize malicious file names. |
| R-AI-9 | M | Strip or neutralize unsafe HTML/script content in both ingested documents and generated output. |
| R-AI-10 | M | Defend against retrieval poisoning: quarantine flagged chunks, cap per-document dominance of the context window, and treat all retrieved text as untrusted data with explicit delimiters. |
| R-AI-11 | M | Cap token consumption per request, per user, per tenant, per day; refuse with a clear error when exceeded. |
| R-AI-12 | M | Scan output for sensitive-information leakage (credentials, keys, PII patterns) and redact before responding. |
| R-AI-13 | M | Guardrail decisions are logged with correlation ID and surfaced as metrics. |
| R-AI-14 | M | **Tool-call injection:** no prompt or document content can cause a call to a tool outside the registry, or outside the caller's role-filtered subset. |
| R-AI-15 | M | **Scope escalation via tool arguments:** no prompt can override the server-injected principal fields; attempts are blocked and audited. |
| R-AI-16 | M | **Agent loop abuse:** no prompt can raise a cap, disable loop detection, or induce unbounded tool calling; caps are code-side. |
| R-AI-17 | M | **Tool-output injection:** instructions embedded in a tool result (including sub-agent output) are treated as data and never followed. |
| R-AI-18 | M | **Sub-agent confusion:** a sub-agent cannot be induced to run with a different Principal, a wider scope, or beyond depth 2. |
| R-AI-19 | M | The agent cannot be induced to disclose the tool registry entries for roles the caller does not hold, or the internal arguments of another user's request. |

### 4.4 API and infrastructure security

| ID | Pri | Requirement |
|---|---|---|
| R-SEC-1 | M | Request schema validation on every endpoint (Pydantic, strict mode). |
| R-SEC-2 | M | Secure HTTP headers: HSTS, `X-Content-Type-Options`, `X-Frame-Options`/frame-ancestors, CSP, `Referrer-Policy`, `Permissions-Policy`. |
| R-SEC-3 | M | CORS allow-list per environment; no wildcard with credentials. |
| R-SEC-4 | M | Rate limiting per §3.3 of the brief: `/chat` 20/min/user, `/search` 40/min/user, `/documents/upload` 5/min/user, stricter limits on unauthenticated endpoints. |
| R-SEC-5 | M | Request body size limits and separate upload size limits, enforced at ingress and in the app. |
| R-SEC-6 | M | Trusted-host validation. |
| R-SEC-7 | M | Generic error responses in production; details only in logs, keyed by correlation ID. |
| R-SEC-8 | M | Correlation ID on every request, response and log line. |
| R-SEC-9 | M | HTTPS-only in production, with redirect and HSTS. |
| R-SEC-10 | M | Least-privilege IAM; one role per workload via IRSA; no wildcard resource ARNs in project policies. |
| R-SEC-11 | M | ElastiCache in private subnets, no public endpoint, encryption in transit and at rest, AUTH enabled. |
| R-SEC-12 | M | No public database endpoint; database access restricted by network allow-list and TLS. |
| R-SEC-13 | M | S3 buckets encrypted (SSE-KMS), versioned, public access blocked, TLS-only policy. |
| R-SEC-14 | M | Database storage encrypted at rest. |
| R-SEC-15 | M | CloudWatch log groups encrypted with KMS where practical, with defined retention. |
| R-SEC-16 | M | ECR image scanning on push; builds fail on HIGH/CRITICAL findings without a documented exception. |
| R-SEC-17 | M | Containers run as a non-root user with a read-only root filesystem and dropped capabilities. |
| R-SEC-18 | M | No secrets in Docker images, image layers, build args or environment files. |
| R-SEC-19 | M | Secrets are read at runtime from AWS Secrets Manager. |

### 4.5 Destructive-action policy (binding on humans and agents)

| ID | Pri | Requirement |
|---|---|---|
| R-DEL-1 | M | No tooling, pipeline or agent in this project may delete AWS infrastructure that is not tagged `Project=ekba` and tracked in this repo's Terraform state. |
| R-DEL-2 | M | No tooling, pipeline or agent may delete an AWS Secrets Manager secret, under any circumstance. |
| R-DEL-3 | M | `terraform destroy` requires explicit per-invocation human authorization and is never wired into CI. |
| R-DEL-4 | M | S3 buckets, Secrets Manager secrets, ECR repositories and the Cognito user pool carry `prevent_destroy`. |
| R-DEL-5 | M | Rollback procedures restore prior versions; they never delete infrastructure or secrets. |

---

## 5. Observability requirements

| ID | Pri | Requirement |
|---|---|---|
| R-OBS-1 | M | Structured JSON application logs to CloudWatch, with correlation ID, tenant, user, route, status, latency. Never log secrets, tokens, full document text or full user questions in production. |
| R-OBS-2 | M | Metrics: EKS pod CPU/memory, request count, 4xx, 5xx, p50 and p95 latency, failed ingestion jobs, model failures, Qdrant failures, cache hit ratio, authentication failures. |
| R-OBS-3 | M | Alarms: unhealthy EKS workloads, high 5xx rate, high latency, deployment failure, database connection pressure. |
| R-OBS-4 | M | For every model request record: model, input tokens, output tokens, estimated cost, latency, cache status, route selected, fallback usage, tenant, prompt version, trace ID. |
| R-OBS-5 | M | LangSmith traces cover end-to-end runs, retrieval, prompt versions, retrieved document IDs, model routing, latency, token usage, user feedback and evaluation results. |
| R-OBS-6 | M | The same correlation ID appears in the API response, application logs, gateway/ALB logs, LangSmith trace and error records. |
| R-OBS-7 | M | Agent metrics: planner iterations per request, tool calls per request, per-tool latency, per-tool error rate by error code, loop-cap breaches, loop-detection triggers, clarification rate, refusal rate by terminal reason, sub-agent invocation counts. |
| R-OBS-8 | M | Alarms on: loop-cap breach rate, tool error rate, refusal-rate anomaly, and agent cost per request exceeding budget. |

---

## 6. Non-functional requirements

| ID | Pri | Requirement |
|---|---|---|
| R-NFR-1 | M | p95 latency for a cached `/chat` ≤ 800 ms; uncached ≤ 6 s at the 95th percentile under nominal load. |
| R-NFR-2 | M | Availability target 99.5% for the API in prod. |
| R-NFR-3 | M | Zero-downtime deploys; automatic rollback on alarm during the bake window. |
| R-NFR-4 | M | Horizontal scaling of API and ingestion workers via HPA. |
| R-NFR-5 | M | Test coverage ≥ 85% overall, 100% on auth, tenancy filtering, guardrails, citation validation, the tool registry and tool authorization. |
| R-NFR-6 | M | All infrastructure defined in Terraform; no manual console changes to project resources. |
| R-NFR-7 | S | Ingestion throughput: a 100-page PDF fully indexed within 5 minutes. |
| R-NFR-8 | S | Cost per answered question tracked and reportable per tenant per day. |

---

## 7. Environments and secrets

| ID | Pri | Requirement |
|---|---|---|
| R-ENV-1 | M | Two environments: `dev` and `prod`, separate namespaces, separate Terraform state, separate S3 buckets and Qdrant collections. |
| R-ENV-2 | M | Secrets supplied via a local `.env` are seeded into AWS Secrets Manager by an idempotent script that creates or updates, never deletes. |
| R-ENV-3 | M | Dev and prod use the same credential values for now — an explicit, user-accepted decision recorded as a risk with a rotation plan. |
| R-ENV-4 | M | `.env`, `*.tfvars`, kubeconfigs and key material are git-ignored and blocked by a pre-commit secret scanner. |

---

## 8. Acceptance criteria for v1

1. An authorized user asks a question in a non-English language and receives a grounded answer
   with citations that resolve to real chunks in a document they are permitted to see.
2. A user asks about a department they are not granted and receives the exact refusal string, with
   zero retrieved chunks and an audit event.
3. A user from tenant A cannot retrieve, list, download or reference any artifact of tenant B —
   proven by automated cross-tenant tests, not inspection.
4. A document containing an embedded injection payload is quarantined at ingestion and, if forced
   into context, does not alter model behaviour.
5. Every `/chat` response contains all eleven contract fields with truthful values.
6. The evaluation harness runs in CI and reports all nine core metrics plus the agent metrics.
6a. A multi-hop question ("compare the leave policy in the v3 and v4 handbooks and compute the
   difference in accrual days") causes the agent to autonomously chain `hybrid_search` →
   `compare_documents` → `calculator`, and every figure in the answer carries a citation.
6b. An adversarial prompt attempting to make the agent call an unregistered tool, override its
   `tenant_id` argument, or exceed its loop caps fails, is audited, and does not degrade the answer
   for legitimate parts of the question.
7. A blue/green deployment to prod completes with a traffic shift, and a deliberately failing
   deployment rolls back automatically without deleting infrastructure or secrets.
8. CloudWatch shows all required metrics and alarms; LangSmith shows a trace whose id matches the
   `trace_id` in the API response and the `correlation_id` in the logs.

---

## 9. Open questions

Blocking Phase 2 (see `ARCHITECTURE.md` §10 for context):

1. Qdrant hosting: self-managed on EKS vs Qdrant Cloud.
2. ~~Whether the Euri Gemini embedding endpoint accepts raw audio/video~~ — **RESOLVED 2026-08-09
   by live probing: it is text-only.** Describe/transcribe-then-embed is now the design.
   Remaining sub-question: which transcription service (Euri does not expose one — AWS Transcribe
   vs a self-hosted Whisper on EKS)?
3. Supabase private networking approach (managed SaaS cannot sit in our VPC as RDS would).
4. Blue/green mechanism on EKS: Argo Rollouts orchestrated by CodeDeploy vs moving the API to ECS.
5. Retention periods per data class, and any regional data-residency requirement.
6. Expected tenant count, document volume and QPS for capacity sizing.
7. Whether SSO federation into Cognito (Okta/Entra) is needed in v1.
8. Agent autonomy budget: are the default caps (8 iterations / 12 tool calls / 45 s) acceptable for
   prod, or should complex questions be allowed a higher tier at higher cost?
9. Should the agent be allowed to ask clarifying questions in v1, or should ambiguous questions
   always be answered best-effort with a stated assumption?
10. Which model tier drives the planner (routing quality vs cost) — same model as generation, or a
   cheaper planner with an escalation path?
