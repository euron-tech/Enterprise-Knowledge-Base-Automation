# Enterprise Knowledge-Base Automation (EKBA)

A multi-tenant, department-scoped, multilingual **Agentic RAG** platform for enterprise knowledge
bases. Employees ask questions in their own language against their department's SOPs, policies,
manuals and records, and get an answer **grounded in retrieved documents with verifiable
citations** — or an explicit refusal when the evidence isn't there.

> **Status: scaffolding / design phase.** No application code exists yet. Implementation begins
> only after the documents in `docs/` are reviewed and approved.

---

## The problem

A company of any size has HR, Finance, Legal, Operations, Engineering and more. Each department
owns thousands of documents — PDFs, spreadsheets, scanned tables, architecture diagrams, training
videos, recorded calls. Nobody can find anything. Search is keyword-based and blind to images,
audio and video. Access control is a shared drive folder and hope.

## What EKBA does

- **Ingests anything** — PDF, DOCX, XLSX, PPTX, TXT, MD, HTML, CSV, images, audio, video.
  Text, tables, diagrams and media are all made retrievable.
- **Stores raw files in S3**, embeddings in **Qdrant**, and all operational state in
  **Supabase PostgreSQL**.
- **Answers in any language**, citing the exact document, version and page.
- **Enforces authorization at retrieval time** — you cannot retrieve what your tenant and
  department grant don't cover. Not "filtered from the UI"; never returned from the vector store.
- **Refuses honestly** when the retrieved evidence is insufficient, with a fixed refusal message,
  rather than guessing.
- **Defends itself** against prompt injection (direct and embedded in uploaded documents), system
  prompt extraction, retrieval poisoning, and cross-tenant probing.
- **Costs are measured, not estimated** — every request records tokens, model, route, cache
  status and dollar cost.

## Architecture at a glance

```
React SPA ──► ALB/Ingress ──► FastAPI on EKS ──► LangGraph agent (planner + tools)
                  │                │                     │
             Cognito JWT      ElastiCache            Qdrant (vectors)
                              (cache + rate limit)   Supabase (OLTP)
                                                     S3 (raw docs)
                                                     Euri AI Gateway
                                                       ├─ Gemini embeddings
                                                       └─ OpenAI generation
                       Observability: CloudWatch + LangSmith (shared correlation ID)
```

Full detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## The agentic RAG pipeline

This is not a fixed retrieve-then-generate chain. A **LangGraph agent** decides, per question,
what to do: how to decompose it, which tools to call and how often, whether the evidence is
sufficient, whether to ask a clarifying question, and whether to refuse.

That autonomy sits inside deterministic gates it cannot skip or influence:

```
PRE-FLIGHT   authenticate → validate input → prompt-injection scan → semantic cache lookup
             ↓
AGENT LOOP   planner → tool_router → tool_executor → observation → reflector ─┐
             ↑───────────────── evidence insufficient, budget remains ────────┘
             └→ request_clarification | refuse | context_builder → model_router → generator
             ↓
POST-FLIGHT  citation validation → output guardrail → usage recording → LangSmith trace → response
```

An agent that could *decide* to skip authorization or citation validation would be a
vulnerability, not a feature. The model plans; it never decides what it is permitted to do.

### Agent tools

All tools are **read-only**, role-filtered, and executed with the caller's identity injected
server-side — never with a tenant or department the model supplied.

| Group | Tools |
|---|---|
| Retrieval | `semantic_search` · `keyword_search` · `hybrid_search` · `fetch_chunk` · `expand_context` · `get_page` |
| Document | `list_documents` · `get_document_metadata` · `summarize_document` · `compare_documents` |
| Modality | `table_lookup` · `image_describe` · `media_locate` · `transcript_segment` |
| Utility | `calculator` · `date_resolver` · `glossary_lookup` · `language_normalize` · `department_scope` |
| Control | `request_clarification` · `refuse` · `escalate` |
| Sub-agents | `retrieval_specialist` · `media_specialist` · `tabular_specialist` · `comparison_specialist` |

Deliberately absent, forever: shell, code execution, arbitrary HTTP fetch, raw SQL, filesystem
access, and anything that writes, deletes or changes permissions.

Loop budgets (iterations, tool calls, tokens, wall clock) are enforced in code, not requested in a
prompt.

Every `/chat` response carries: `answer`, `citations`, `retrieved_chunks`, `model_used`,
`input_tokens`, `output_tokens`, `estimated_cost`, `latency_ms`, `cache_hit`, `trace_id`,
`confidence`.

## Technology

| Layer | Choice |
|---|---|
| Frontend | React + TypeScript (Vite) |
| API | Python 3.12 + FastAPI |
| Agents | LangGraph |
| Vector store | Qdrant |
| Relational | Supabase (PostgreSQL) |
| Cache & rate limiting | AWS ElastiCache for Redis |
| Auth | AWS Cognito (JWT, RBAC, tenancy) |
| Object storage | AWS S3 (SSE-KMS) |
| Secrets | AWS Secrets Manager |
| Runtime | AWS EKS |
| Release | AWS CodeDeploy blue/green, GitHub Actions |
| IaC | Terraform |
| Observability | CloudWatch + LangSmith |
| Embeddings / LLM | Euri AI Gateway — `gemini-embedding-2-preview` + `gpt-4.1` family |
| UI design system | Tailwind + shadcn/ui, Geist Variable, tokens inherited from the Euron CRM |
| Tests | pytest, Vitest, Playwright |

## Repository layout

```
docs/           ARCHITECTURE.md · REQUIREMENTS.md · TASKS.md
.claude/        rules/ (binding engineering rules) · skills/ (e2e-verification, deployment)
backend/        FastAPI service, LangGraph agent + tools, ingestion, DB models, tests, evals
frontend/       React SPA
infra/terraform modules/ and envs/{dev,prod}
k8s/            Kubernetes manifests / Helm values
.github/        CI/CD workflows
```

## Getting started

Nothing to run yet. The intended local flow once Phase 1 lands:

```bash
cp .env.example .env          # fill in real values; never commit
make dev-up                   # docker compose: qdrant, redis, localstack, postgres
make migrate                  # alembic upgrade head
make api                      # uvicorn on :8000
make test                     # pytest
```

## Documentation map

| Document | Contents |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | How Claude works in this repo; hard rules; contracts |
| [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) | Functional + non-functional requirements, open questions |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Component, data, deployment and security architecture |
| [`docs/TASKS.md`](docs/TASKS.md) | Phased build plan with Definitions of Done |
| [`docs/INTEGRATIONS-EURI.md`](docs/INTEGRATIONS-EURI.md) | Euri AI Gateway contract, **verified against the live API** |
| [`docs/DESIGN-SYSTEM.md`](docs/DESIGN-SYSTEM.md) | UI tokens, typography and responsive contract, inherited from the Euron CRM |
| [`SECURITY.md`](SECURITY.md) | Threat model, controls, AI security, disclosure |
| [`.claude/rules/`](.claude/rules/) | Root, Python, agents & tools, security, guardrails, testing, infra, data, frontend |

## Operational guarantees

- No AWS resource outside this project is ever modified or deleted by project tooling.
- AWS Secrets Manager secrets are created and updated, **never deleted**.
- `terraform destroy` requires explicit human authorization, every time.

## License

Proprietary — internal use.
