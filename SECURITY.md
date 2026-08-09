# Security — Enterprise Knowledge-Base Automation

Version 0.1 · Status: design · Applies to code, infrastructure, pipelines and AI agents working
in this repository.

---

## 1. Security principles

1. **Authorization is enforced at the data layer.** A user must be unable to retrieve foreign data
   even if every layer above the query is compromised or bypassed.
2. **Retrieved content is untrusted input.** Documents are data, never instructions.
3. **Prompts are advisory; validators are enforcement.** Anything that must be true is checked in
   code after generation, not merely requested in a system prompt.
4. **Fail closed.** Missing tenant context, unresolvable citation, failed guardrail → refuse.
5. **Least privilege everywhere** — IAM, Kubernetes RBAC, database roles, network policy.
6. **No secret ever lives in Git, an image, a log, or a response.**
7. **Agents get autonomy over decisions, never over permissions.** The planner chooses what to do;
   authorization, budgets and validation are code the agent cannot reach. Every tool is read-only.
8. **Nothing outside this project is ever destroyed.** See §9 — this is absolute.

---

## 2. Threat model

### 2.1 Assets

| Asset | Sensitivity |
|---|---|
| Tenant documents (HR, Legal, Finance SOPs) | High — confidential business and personal data |
| Embeddings and extracted text | High — reconstructable content |
| Cognito identities and JWTs | High |
| AWS credentials, Euri gateway keys, DB credentials | Critical |
| Audit log | High — integrity critical |
| Usage/cost data | Medium |

### 2.2 Trust boundaries

Browser → CloudFront/ALB → API pods → {Qdrant, Supabase, Redis, S3, Euri gateway}.
Uploaded documents cross a boundary **into** the model's context and are treated as hostile.

### 2.3 Threats and mitigations

| # | Threat | Mitigation |
|---|---|---|
| T1 | Cross-tenant data access | Mandatory `tenant_id` filter enforced by a single chokepoint; server-side grant lookup; RLS; cross-tenant test suite |
| T2 | Direct prompt injection | Input classifier + instruction-hierarchy prompt + output validators |
| T3 | Indirect injection in documents | Scan at ingestion (quarantine) and again at context construction; delimited untrusted blocks; per-document dominance cap |
| T4 | System-prompt extraction | Output scanner for prompt fragments; refusal; audit event |
| T5 | Instruction override / jailbreak | Layered defence: classifier, structured prompt, citation validator, output guardrail |
| T6 | Retrieval poisoning | Ingestion authorization (only granted users can add to a department), quarantine flags, score floors, dominance cap, provenance in every citation |
| T7 | Citation fabrication | Post-generation validator mapping every citation to a retrieved `chunk_id`; unmapped → strip → possibly refuse |
| T8 | Token/cost exhaustion (DoW) | Per-request, per-user, per-tenant, per-day token caps; rate limits; context budget; alarm on cost spike |
| T9 | Malicious upload (zip bomb, polyglot, malware) | Size limits, magic-byte sniffing, extension allow-list, decompression limits, filename sanitation, quarantine bucket |
| T10 | XSS via document content or model output | HTML sanitation on ingest and on output; CSP; React escaping; no `dangerouslySetInnerHTML` |
| T11 | Token theft / replay | Short-lived tokens, HTTPS only, no token in `localStorage`, `aud`/`iss`/`exp` verification |
| T12 | Privilege escalation via client-supplied claims | Never trust client `tenant_id`/`role`/`owner_id`; re-read from DB |
| T13 | Secret leakage in logs or answers | Output secret scanner, log redaction filters, pre-commit secret scanning, no secrets in images |
| T14 | Supply-chain compromise | Pinned digests, ECR scan gate, SBOM, dependency review, lockfiles |
| T15 | Insider/agent destructive action | §9 destructive-action policy, `prevent_destroy`, no destroy in CI, IAM denies on delete of secrets |
| T16 | Presigned URL leakage | Short TTL, authorization checked before issuance, no URLs in logs |
| T17 | SSRF via document URLs or gateway config | No fetching of user-supplied URLs during ingestion; egress restricted by NetworkPolicy to known endpoints |
| T18 | Audit tampering | Append-only table, no update/delete API path, restricted DB grants |
| T19 | **Tool-call injection** — prompt or document content induces a call to an unregistered or unauthorized tool | Central registry, role-filtered exposure, dispatch by registry lookup only, unknown tool → typed error + audit |
| T20 | **Scope escalation via tool arguments** — model emits its own `tenant_id`/`department`/`role` | Principal injected server-side; model-supplied principal fields discarded and audited; args schema forbids them |
| T21 | **Agent loop abuse / denial of wallet** — prompt drives unbounded tool calling | Code-enforced caps on iterations, tool calls, per-tool calls, tokens, wall clock; loop detection; cost alarms |
| T22 | **Tool-output injection** — instructions embedded in a tool result or sub-agent output | Results rescanned for injection, wrapped as untrusted data, never followed as instructions |
| T23 | **Sub-agent confusion** — inducing a subgraph to run with a wider scope or deeper recursion | Sub-agents inherit the immutable Principal; depth cap 2; same registry filtering |
| T24 | **Autonomous destructive action** | There is no write, delete or configuration tool. The agent physically cannot mutate anything |
| T25 | **Tool-result exfiltration** — content pulled into context then leaked via the answer | Output guardrails, citation validation, provenance requirement, no HTTP/egress tool |
| T26 | **Reasoning-trace leakage** — plan or internal arguments surfacing to the user | Only the final answer and validated citations are returned; traces go to LangSmith, not the response |

---

## 3. Authentication (AWS Cognito)

Every protected request presents a Cognito JWT. Verification is mandatory and complete:

| Check | Rule |
|---|---|
| Signature | Verified against the pool JWKS; keys cached with TTL and refreshed on `kid` miss |
| Algorithm | Allow-list `RS256` only; `none` and symmetric algorithms rejected |
| Issuer | Must equal `https://cognito-idp.<region>.amazonaws.com/<user_pool_id>` |
| Audience | `aud` (id token) or `client_id` (access token) must match the configured app client |
| Expiry | `exp` in the future, `nbf`/`iat` sane, ≤ 60 s clock skew |
| Token use | `token_use` must match the expected type for the route |
| Subject | `sub` must resolve to an active `users` row in the expected tenant |

Failures return a generic `401`, are written to `audit_events`, and increment the
`AuthenticationFailures` metric. Repeated failures per IP/user are rate limited.

---

## 4. Authorization

**Roles:** `user`, `admin`.

| Rule | Enforcement |
|---|---|
| Users access only documents in their tenant | `tenant_id` filter on every Qdrant query and every SQL read |
| Users access only their permitted departments | Department filter derived from server-side grants |
| Admins access operational metrics | `require_role("admin")` dependency + tenant scoping |
| Document ownership verified before deletion | DB lookup of `owner_id`; admin override allowed and audited |
| Qdrant queries always filter on `tenant_id` | Single chokepoint builder; runtime assertion; unit test that a filter without `tenant_id` raises |

Grants come from the database, not the token. A token claim may hint, but the server re-reads.

---

## 5. AI security and guardrails

### 5.1 Input side

- Length, encoding and schema validation before anything else.
- Direct prompt-injection classification (heuristics + model-based), with an audited block.
- Language detection; no bypass through encoding tricks (unicode confusables, base64 payloads,
  zero-width characters are normalized or rejected).

### 5.2 Document side

- Indirect-injection scanning at ingestion: imperative phrases addressed to an assistant, embedded
  instruction blocks, hidden text (white-on-white, zero-size fonts, off-canvas), HTML comments,
  metadata fields.
- Detected payloads → quarantine: the element is stored, flagged, excluded from indexing, and
  surfaced to the tenant admin. It is never silently indexed.
- All retrieved text is wrapped in explicit untrusted-content delimiters with a standing
  instruction that content inside is data only.

### 5.3 Generation side

- Grounded system prompt: answer only from context; cite every claim; refuse with the fixed string
  when evidence is insufficient; never reveal system instructions.
- Prompt versions are stored in `prompt_releases` and referenced in every trace and usage record.

### 5.4 Output side (all enforced in code)

| Guardrail | Action on trigger |
|---|---|
| Citation validation | Strip unmapped citations; refuse if none remain |
| System-prompt leak detection | Replace answer with refusal; audit |
| Secret/credential pattern scan | Redact; audit; alarm |
| PII scan (configurable per tenant) | Redact or refuse per policy |
| HTML/script sanitation | Strip active content |
| Token/cost cap | Truncate context or refuse with a clear error |

### 5.4a Agent and tool security

The system is agentic, which adds an attack surface that ordinary RAG does not have. The controls:

| Control | Rule |
|---|---|
| Sandwich architecture | Autonomy exists only between the deterministic pre-flight and post-flight gates. The agent cannot skip authentication, injection scanning, citation validation or output guardrails — they are not steps it chooses |
| Read-only tools | Every tool is read-only. There is no write, delete, retire, grant-change or configuration tool. A misbehaving agent cannot destroy or alter anything |
| Central registry | Only registered tools are dispatchable, and the registry is filtered by the caller's role before the model ever sees it |
| Server-injected Principal | `tenant_id`, `department`, `owner_id`, `role`, `user_id`, `correlation_id` come from the verified JWT. Model-supplied values are discarded and audited |
| Strict argument schemas | Pydantic strict, `extra="forbid"`; invalid arguments → typed tool error, never partial execution |
| Per-call authorization | Each tool re-checks role and department scope; nothing is inherited from the planner's intent |
| Provenance requirement | Content without full provenance cannot be cited, so it cannot be used |
| Untrusted tool output | Tool and sub-agent results are rescanned for injection and delimited as data |
| Code-enforced budgets | Iterations, tool calls, per-tool calls, chunks, tokens and wall clock are capped in code; a prompt cannot raise them |
| Loop detection | Repeated identical calls terminate the branch |
| Egress isolation | No shell, code-execution, HTTP-fetch, SQL or filesystem tool exists; NetworkPolicies block anything else |
| Full auditability | Every tool call is traced with redacted arguments, outcome, iteration index and correlation ID |

### 5.5 The refusal contract

```
I could not find enough evidence in the approved documents to answer this question.
```

Byte-exact, from a single constant. Returned on: no retrieval results, all results below threshold,
zero valid citations after validation, guardrail block that leaves no safe answer.

---

## 6. API and infrastructure security controls

| Control | Implementation |
|---|---|
| Request validation | Pydantic v2 strict models; unknown fields rejected |
| Secure headers | HSTS, CSP, `X-Content-Type-Options: nosniff`, frame-ancestors none, `Referrer-Policy`, `Permissions-Policy` |
| CORS | Per-environment allow-list; no wildcard with credentials |
| Rate limiting | Redis token bucket: `/chat` 20/min/user, `/search` 40/min/user, `/documents/upload` 5/min/user, health endpoints per IP |
| Body size limits | Ingress + app-level cap; separate, larger cap for uploads only |
| Upload limits | Max bytes, max pages, max decompression ratio, max duration for a/v |
| Trusted hosts | `TrustedHostMiddleware` with an explicit host list |
| Error responses | Generic message + correlation ID in prod; details only in logs |
| Correlation IDs | Minted at the edge, echoed in responses, present in every log, trace and audit row |
| HTTPS only | Redirect + HSTS in prod; ALB listener TLS 1.2+ |
| IAM | One IRSA role per workload; no wildcard resources; deny statements on secret deletion |
| Redis | Private subnets, no public endpoint, TLS, AUTH token from Secrets Manager |
| Database | No public endpoint; TLS enforced; network allow-list; least-privilege DB roles; storage encrypted |
| S3 | SSE-KMS, versioning, Block Public Access, TLS-only bucket policy, lifecycle rules |
| Logs | KMS-encrypted log groups with retention; redaction filters |
| Images | ECR scan on push; HIGH/CRITICAL gate; pinned digests; SBOM |
| Containers | Non-root UID, read-only root filesystem, dropped capabilities, no privilege escalation |
| Secrets | AWS Secrets Manager only, injected via External Secrets Operator; never in images, args or env files |
| Network | NetworkPolicies restrict pod egress to the gateway, AWS endpoints and data stores |
| WAF | Managed rule sets + rate rules in front of the ALB |

---

## 7. Secrets management

- Source of truth: **AWS Secrets Manager**, path `ekba/<env>/<name>`, KMS-encrypted.
- Seeding: `scripts/seed_secrets.py` reads a local `.env` and calls `CreateSecret` on first run,
  `PutSecretValue` thereafter. It has **no delete code path**, and a test asserts that.
- Dev and prod currently share credential values by explicit user decision. Recorded risk:
  a dev-side compromise is a prod-side compromise. Mitigations until split: dev is not internet
  exposed beyond authenticated users, all access is audited, and Phase 11 defines the rotation and
  split plan.
- Rotation: supported by design (Secrets Manager rotation + External Secrets refresh). Rotation
  never deletes a secret; it adds a version.

### 7.1 Known credential exposure — action required

The Euri AI Gateway key used for the 2026-08-09 contract verification was transmitted in plaintext
chat. **Treat it as compromised.** It must be revoked at the provider and replaced before any
deployment, and the replacement must be delivered through Secrets Manager, never through chat, a
ticket, or a file in the repository. The key appears in no file in this repository and in no log.

General rule: any credential that has been pasted into a chat, a ticket, a commit message or a
screenshot is burned. Rotate it — do not reason about whether it was "probably fine".
- CI authenticates to AWS via GitHub OIDC. No long-lived AWS keys in GitHub.
- `.env`, `*.tfvars`, kubeconfigs and key files are git-ignored and blocked by pre-commit scanning.

---

## 8. Logging, audit and privacy

- Logs are structured JSON, redacted, and contain no secrets, tokens, presigned URLs, full document
  text, or (in prod) raw user questions. Question hashes and lengths are logged instead.
- `audit_events` is append-only and records: authentication failures, authorization denials,
  uploads, deletions, grant changes, injection detections, guardrail blocks, admin metric access,
  and secret-seeding runs.
- Data subject requests (deletion/export) are served through the document lifecycle APIs plus a
  documented runbook; deleting a document removes its vectors, its derived artifacts and its S3
  objects, while leaving the audit trail intact.

---

## 9. Destructive-action policy — ABSOLUTE

This section binds every human, script, pipeline and AI agent operating in this repository.

1. **Do not delete, disable, modify or `terraform destroy` any AWS resource that is not both
   tagged `Project=ekba` and present in this repository's Terraform state.** Other workloads exist
   in this account. They are out of scope, permanently. Read-only access at most.
2. **Do not delete any AWS Secrets Manager secret. Ever.** Not `DeleteSecret`, not
   `--force-delete-without-recovery`, not via Terraform, not "temporarily", not to fix a conflict.
   Creating secrets and adding versions is allowed; deletion is not.
3. **`terraform destroy` is never run without explicit, written, per-invocation authorization from
   the repository owner**, and is never present in any CI workflow.
4. `prevent_destroy = true` is mandatory on: S3 buckets, Secrets Manager secrets, the KMS keys
   protecting them, ECR repositories, the Cognito user pool, and the Terraform state resources.
5. IAM policies for CI and application roles include explicit `Deny` on
   `secretsmanager:DeleteSecret`, `s3:DeleteBucket`, `kms:ScheduleKeyDeletion` and
   `cognito-idp:DeleteUserPool`.
6. Destructive `kubectl` verbs are restricted to the project's own namespaces.
7. Rollback restores a previous version. It never deletes infrastructure or secrets.
8. If a task appears to require violating this section, **stop and ask the owner**. There is no
   emergency exception, no "it's just dev", and no inference of permission from a prior approval.

---

## 10. Security testing

| Layer | Tests |
|---|---|
| Unit | JWT failure modes, filter chokepoint, guardrail units, sanitizers |
| Integration | Cross-tenant access attempts across every endpoint; rate limits; upload validation |
| Red team | The attack corpus of Phase 4 run against `/chat`, `/search`, `/documents/upload`, including agent-specific attacks: unregistered tool invocation, principal-argument override, loop-cap abuse, tool-output injection, sub-agent scope confusion |
| Agent | Scripted-planner routing tests, tool-authorization matrix tests (every tool × every role × in/out of scope), budget-enforcement tests, provenance-required tests |
| Static | ruff security rules, bandit, mypy, npm audit, dependency review |
| IaC | checkov/tfsec, tflint, tag policy test, "no destroy in CI" grep test |
| Container | Trivy/ECR scan gate, non-root assertion, no-secrets-in-layers assertion |
| Runtime | Alarm-backed anomaly detection on auth failures, 5xx, cost |

A security test is never deleted, skipped or weakened to make a build pass.

---

## 11. Incident response

1. Capture the correlation ID and the affected tenant scope.
2. Contain: revoke tokens / disable the user or app client / tighten WAF — never by deleting
   infrastructure.
3. Assess blast radius using audit events and LangSmith traces.
4. Rotate affected secrets by adding new versions (no deletion).
5. Roll back the deployment if a release is implicated.
6. Write a post-incident note in `docs/` with the timeline and the control that failed.

---

## 12. Reporting a vulnerability

Report privately to the repository owner. Include the correlation ID where relevant, reproduction
steps and impact. Do not open a public issue. Do not test against production tenants other than
your own.
