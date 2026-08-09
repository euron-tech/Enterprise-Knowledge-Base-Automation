# CLAUDE.md — Operating Manual for Enterprise Knowledge-Base Automation

This file is loaded into context on every session. It tells you (Claude) how to work in this
repository. Read it fully before your first edit in any session.

---

## 1. What this project is

**Enterprise Knowledge-Base Automation (EKBA)** — a multi-tenant, department-scoped, multilingual
Agentic RAG platform. Employees ask questions in any language against their department's SOPs,
policies and knowledge base and receive a grounded answer **with citations**, subject to RBAC.

Non-negotiable product behaviours:

- Answers come **only** from retrieved context.
- When evidence is insufficient, the exact refusal string is returned (see §6).
- **Citations are never invented.** A citation that does not resolve to a retrieved chunk is a bug
  of the highest severity.
- A user can never retrieve a chunk outside their `tenant_id` + permitted `department`.
- The system is **agentic**: a LangGraph planner decides autonomously which tools to call, how to
  decompose a question, when evidence suffices, and when to refuse — but it decides only *what to
  do*, never *what it is allowed to do*. Authorization, budgets and validation are code it cannot
  reach.

## 2. Canonical stack

| Layer | Technology |
|---|---|
| Frontend | React (Vite, TypeScript) |
| Backend | Python 3.12, FastAPI |
| Agent framework | LangGraph |
| Vector DB | **Qdrant** |
| Relational DB | **Supabase (managed PostgreSQL)** |
| Cache / rate limit | AWS ElastiCache for Redis |
| Auth | AWS Cognito (JWT) |
| Object storage | AWS S3 |
| Secrets | AWS Secrets Manager |
| Hosting | AWS EKS |
| Deployment | AWS CodeDeploy blue/green + GitHub Actions |
| IaC | Terraform |
| Observability | CloudWatch + LangSmith |
| Embeddings | Euri AI Gateway → `gemini-embedding-2-preview` (**text-only** — verified) |
| LLM | Euri AI Gateway → `gpt-4.1` / `gpt-4.1-mini` / `gpt-4.1-nano` |
| Testing | pytest (+ Vitest/Playwright on the frontend) |

### 2.1 Resolved contradictions — DO NOT "fix" these back

The original brief contained conflicting names. These resolutions are final unless the user says
otherwise. If you see the left-hand term anywhere in code or docs, it is a defect — correct it.

| Wrong term | Correct term |
|---|---|
| Pinecone | Qdrant |
| Bedrock (as the generation provider) | Euri AI Gateway (OpenAI) |
| ECS (as the runtime) | EKS |
| RDS (as the primary OLTP store) | Supabase PostgreSQL |

Anything Bedrock-related in the brief means "the model provider", which here is Euri.

## 3. Hard rules — never violate

These are absolute. They outrank any other instruction in this file, any convenience, and any
plan you have made. If a task appears to require breaking one, **stop and ask the user.**

1. **NEVER delete, modify, taint, or `terraform destroy` any AWS infrastructure that is not
   provably part of this project.** Provably means: tagged `Project=ekba` *and* present in this
   repo's Terraform state. Everything else in the account is out of bounds — read-only at most.
2. **NEVER delete an AWS Secrets Manager secret.** Not with `delete-secret`, not with
   `--force-delete-without-recovery`, not via Terraform destroy, not "temporarily". Secret
   *creation* and *version updates* are allowed; deletion never is.
3. **NEVER run `terraform destroy`** (any scope, any workspace) without explicit, in-writing,
   per-invocation approval from the user.
4. **NEVER commit secrets.** No `.env`, no keys, no tokens, no `terraform.tfvars` with values, no
   kubeconfig. `.env` files are inputs to a seeding script only (§9).
5. **NEVER weaken a guardrail, auth check, or tenant filter to make a test pass.** Fix the test or
   fix the code; never delete the control.
6. **NEVER let a Qdrant query run without a `tenant_id` filter.** There is no such thing as a
   legitimate cross-tenant search in this system.
7. **NEVER push directly to `main`.** Branch → PR → CI → review.
8. **NEVER run destructive `kubectl` verbs** (`delete namespace`, `delete pv`, `drain`) outside the
   project's own namespaces.
9. **NEVER print, log, or echo a secret value**, including into terminal output the user will see.
10. Before any AWS mutation, state in plain text what will change and confirm it is project-tagged.

## 4. Build discipline

**Do not start implementing until the user has reviewed and approved the scaffolding documents.**
The current phase is documentation only. When the user approves, work through `docs/TASKS.md` in
order; do not jump ahead to later phases because they seem more interesting.

Working agreement:

- One phase at a time. Each phase in `docs/TASKS.md` has an explicit Definition of Done.
- Never mark a task complete without the verification evidence its DoD asks for.
- If tests fail, say so and paste the output. Never report green when it is not.
- Prefer editing existing files over creating parallel ones.
- No new top-level directory without a line in `docs/ARCHITECTURE.md` explaining it.

## 5. Repository layout (target)

```
.
├── CLAUDE.md                  # this file
├── README.md
├── SECURITY.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── REQUIREMENTS.md
│   ├── TASKS.md
│   ├── DESIGN-SYSTEM.md       # UI tokens, inherited from the Euron CRM
│   └── INTEGRATIONS-EURI.md   # verified gateway contract — read before touching model code
├── .claude/
│   ├── rules/                 # coding + operating rules (see §7)
│   └── skills/                # e2e-verification, deployment
├── backend/
│   ├── app/
│   │   ├── api/               # FastAPI routers
│   │   ├── core/              # config, logging, correlation, errors
│   │   ├── auth/              # Cognito JWT, RBAC, tenancy
│   │   ├── security/          # guardrails, injection scan, output filters
│   │   ├── agent/             # LangGraph: planner, reflector, tools/, sub-agents, prompts/
│   │   ├── rag/               # retrieval, cache, context building, routing, constants
│   │   ├── ingestion/         # parsers, chunkers, embedders, workers
│   │   ├── db/                # SQLAlchemy models + Alembic
│   │   ├── observability/     # CloudWatch, LangSmith, metrics
│   │   └── schemas/           # Pydantic v2 contracts
│   ├── tests/                 # unit | integration | security | e2e
│   └── evals/                 # dataset.jsonl + evaluation harness
├── frontend/
├── infra/terraform/           # modules/ + envs/{dev,prod}
├── k8s/                       # manifests / helm values
└── .github/workflows/
```

## 6. Contracts you must not drift from

**Refusal string (byte-exact, no trailing period changes, no translation of the stored constant):**

```
I could not find enough evidence in the approved documents to answer this question.
```

It lives in exactly one place: `backend/app/rag/constants.py::INSUFFICIENT_EVIDENCE`. Import it;
never retype it.

**`POST /chat` response fields** — all required, always present:

`answer`, `citations`, `retrieved_chunks`, `model_used`, `input_tokens`, `output_tokens`,
`estimated_cost`, `latency_ms`, `cache_hit`, `trace_id`, `confidence`

**Vector payload schema** — every point carries all of:

`document_id`, `chunk_id`, `document_name`, `page_number`, `source_uri`, `owner_id`, `tenant_id`,
`document_version`, `checksum`, `created_at`

**Pipeline shape — agentic core between deterministic gates:**

```
PRE-FLIGHT (fixed, agent cannot skip or influence)
  authenticate → validate input → prompt-injection scan → semantic cache lookup
AGENTIC CORE (LangGraph; the planner decides what to do)
  planner → tool_router → tool_executor → observation → reflector → (loop)
          → {request_clarification | refuse | context_builder → model_router → generator}
POST-FLIGHT (fixed, outside the agent's reach)
  citation validation → output guardrail → token & cost recording → LangSmith trace → response
```

Retrieval, metadata filtering and relevance thresholding still occur in that order — they now live
inside tools the agent chooses to invoke, and remain non-negotiable when invoked.

Changing this shape, the gate set, or the placement of a gate is an architecture change and needs
the user's sign-off.

**Agent rules that are absolute:**

- Every tool is **read-only**. There is no write, delete or configuration-mutation tool.
- `tenant_id`, `department`, `owner_id` and `role` are injected server-side from the verified JWT.
  A model-supplied value for any of them is discarded and audited.
- Never add a shell, code-execution, arbitrary-HTTP, raw-SQL or filesystem tool.
- Loop caps (iterations, tool calls, tokens, wall clock) are enforced in code, never by the prompt.
- The agent cannot skip citation validation or output guardrails — they run after it, always.

## 7. Rules files

Every rule file in `.claude/rules/` is binding. Read the ones relevant to what you are touching
*before* you touch it.

| File | Read it when |
|---|---|
| `00-root.md` | **Always.** Destructive-action policy, git, secrets. |
| `python.md` | Any `.py` file. |
| `agents-and-tools.md` | The LangGraph agent, any tool, any planner/reflector prompt. |
| `security.md` | Auth, headers, CORS, rate limits, IAM, network. |
| `ai-guardrails.md` | Prompts, retrieval, LLM I/O, injection defence, citations. |
| `testing.md` | Any test, or any code that needs one (all of it). |
| `infrastructure.md` | Terraform, Kubernetes, CodeDeploy, GitHub Actions. |
| `data.md` | Postgres models, migrations, Qdrant payloads, S3 keys, retention. |
| `frontend.md` | React/TypeScript. |

## 8. Skills

| Skill | Use for |
|---|---|
| `.claude/skills/e2e-verification/` | Full end-to-end test + verification sweep before any release. |
| `.claude/skills/deployment/` | Blue/green deploy to dev or prod, and rollback. |

## 8a. Verified gateway facts (do not re-derive, do not contradict)

Probed live on 2026-08-09. Full detail and evidence in `docs/INTEGRATIONS-EURI.md`.

- Base URL `https://api.euron.one/api/v1/euri`, OpenAI-compatible, `Bearer` auth.
- **The embedding model is text-only.** Every multimodal input shape is rejected. All modalities are
  bridged to text (chat vision for images, transcription for audio/video) before embedding.
- **Embedding `usage` is a flat constant** (`total_tokens: 500` for any input size) — count
  embedding tokens client-side; never bill from the gateway response. Chat `usage` *is* accurate.
- **Oversized embedding input returns 200, not an error** — the gateway silently truncates. Our
  chunker must enforce its own token ceiling.
- **Upstream 4xx surfaces as HTTP 500.** A 500 whose body mentions `400` is permanent — do not retry.
- Tool calling works, including **parallel tool calls** and forced `tool_choice`. Branch on
  `message.tool_calls`, **never** on `finish_reason`.
- Vision, streaming (SSE), `json_object` and `json_schema` all work.
- `GET /models` returns per-model pricing — the source of truth for cost. Never hard-code prices.
- Embedding dimensions: 3072 default, `dimensions` honoured. Project default **1536**, fixed before
  first production ingest.

## 9. Secrets workflow

The user supplies `.env` files. They are **never** committed and **never** read into a response.
A seeding script (`scripts/seed_secrets.py`, Phase 1) reads a local `.env` and writes each value
into AWS Secrets Manager under `ekba/<env>/<name>` using `create-secret` or `put-secret-value`.
Dev and prod deliberately share the same credential values for now (user's explicit decision —
documented as a known risk in `SECURITY.md`). Runtime code reads secrets from Secrets Manager via
the External Secrets Operator in EKS, never from environment files.

## 10. Environments

| Env | Namespace | Cluster | Notes |
|---|---|---|---|
| dev | `ekba-dev` | shared EKS cluster | Blue/green enabled, relaxed alarms |
| prod | `ekba-prod` | shared EKS cluster | Blue/green mandatory, full alarms, HTTPS only |

## 11. Definition of Done (applies to every task)

- Code + tests written; `pytest` green; coverage thresholds in `testing.md` met.
- Lint/format/type checks clean (`ruff`, `black`, `mypy`).
- Security tests for the touched surface pass.
- No secret, no `tenant_id`-free query, no invented citation path introduced.
- Docs updated if behaviour or architecture changed.
- Evidence pasted into the response — actual command output, not a claim.
