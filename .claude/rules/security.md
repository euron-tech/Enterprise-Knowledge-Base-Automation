# Rule — Application and Infrastructure Security

Applies to auth, authorization, HTTP surface, IAM, networking, containers and secrets.
Read with `00-root.md`, `ai-guardrails.md` and `agents-and-tools.md`.

---

## 1. Non-negotiables

1. Authorization is enforced at the data layer. If every layer above it were bypassed, a user still
   could not read another tenant's data.
2. Never trust a client-supplied `tenant_id`, `department`, `role`, `owner_id` or `user_id` — from
   a request body, a query string, a header, **or a model's tool call**.
3. Fail closed. Missing context, ambiguous scope, failed check → deny.
4. Never weaken a control to unblock work. Change the design and tell the user.
5. Never delete a secret or non-project infrastructure (`00-root.md` §1).

---

## 2. Authentication (Cognito JWT)

Implement all of these; a missing one is a vulnerability, not a nice-to-have:

```python
# Every check below is mandatory
verify_signature  # against JWKS, cached with TTL, refreshed on unknown kid
allowed_algs = {"RS256"}          # reject "none", HS*, and alg confusion
verify_issuer     # https://cognito-idp.<region>.amazonaws.com/<pool_id>
verify_audience   # aud (id token) or client_id (access token)
verify_exp / nbf / iat            # <= 60s leeway
verify_token_use  # matches the expected token type for the route
resolve_subject   # sub -> active users row, expected tenant
```

Rules:

- One verifier module. No route re-implements JWT parsing.
- Never decode without verifying (`options={"verify_signature": False}` is banned outside tests).
- JWKS fetch has a timeout, a cache, and a bounded refresh rate (no thundering herd, no
  attacker-triggered fetch storm).
- Auth failures: generic 401, audit event, metric. Never leak which check failed.
- No custom token formats, no API keys as a substitute, no "internal" bypass header. Service-to-service
  auth uses IAM/IRSA, not a shared secret in a header.

---

## 3. Authorization

- A single `Principal` object (`user_id`, `tenant_id`, `role`, `departments`, `correlation_id`) is
  built once per request from the verified token plus a database read of current grants. Grants
  come from the database, not the token.
- Route-level: `Depends(require_role(...))`, `Depends(require_department(...))`. Never an `if`
  buried in a handler.
- Data-level: every Qdrant query goes through the chokepoint filter builder, which takes a
  `Principal` and raises if `tenant_id` is missing. Building a filter by hand is a review failure.
- Every SQL read of a tenant-scoped table carries a `tenant_id` predicate; repositories take a
  `Principal`, not a bare id.
- Deletion: re-read `owner_id` from the database and compare, or require admin. Never trust the
  request.
- Admin metrics are tenant-scoped. There is no cross-tenant read in v1.
- **Agent tools**: authorization is re-checked inside each tool. The planner's decision is never
  treated as an authorization decision.

---

## 4. HTTP surface

| Control | Requirement |
|---|---|
| Validation | Pydantic v2 strict, `extra="forbid"`, on every request model |
| Headers | HSTS (prod), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` / CSP `frame-ancestors 'none'`, CSP, `Referrer-Policy: no-referrer`, `Permissions-Policy` minimal |
| CORS | Explicit per-environment origin allow-list. Never `*` with credentials. Never reflect the Origin header |
| Trusted hosts | `TrustedHostMiddleware` with an explicit list |
| Body size | Global cap at ingress and in-app; a separate, larger cap for the upload route only |
| Rate limits | `/chat` 20/min/user · `/search` 40/min/user · `/documents/upload` 5/min/user · other authenticated routes 60/min/user · unauthenticated/health per-IP and stricter. Every route declares a policy; a route without one fails a test |
| Errors | Prod: generic message + code + correlation ID. Never a stack trace, ORM error, upstream body or file path |
| Correlation ID | Accepted or minted at the edge; echoed in `X-Correlation-ID`; present in every log, trace and audit row |
| HTTPS | Prod is HTTPS-only with redirect and HSTS; TLS 1.2 minimum at the ALB |
| Methods | Only the methods a route needs; `OPTIONS` handled by CORS middleware |

Never expose: `/docs`, `/redoc`, `/openapi.json` in production without auth. Never return internal
identifiers that leak other tenants' existence.

---

## 5. IAM and AWS

- One IRSA role per workload (api, ingest worker, CI). No shared "app role".
- No `Action: "*"`, no `Resource: "*"` in project policies. Scope to exact ARNs and prefixes.
- Explicit `Deny` statements on: `secretsmanager:DeleteSecret`,
  `secretsmanager:DeleteResourcePolicy`, `s3:DeleteBucket`, `kms:ScheduleKeyDeletion`,
  `kms:DisableKey`, `cognito-idp:DeleteUserPool`, `ecr:DeleteRepository`.
- CI authenticates via GitHub OIDC. No long-lived AWS access keys anywhere.
- Tag every resource `Project=ekba`, `Environment=<env>`, `ManagedBy=terraform`, `Owner=<team>`.
- Never touch a resource lacking the project tag (`00-root.md` §1.1).

---

## 6. Networking

- Private subnets for ElastiCache and any self-hosted data store. No public endpoints.
- Security groups reference other security groups, not CIDRs, wherever possible. No `0.0.0.0/0`
  ingress except the ALB's 443.
- VPC endpoints for S3, ECR, Secrets Manager, CloudWatch — keep traffic off the public internet.
- Kubernetes NetworkPolicies: default deny egress; allow only the Euri gateway, Supabase, Qdrant,
  and the AWS endpoints in use. This is also the SSRF backstop for the agent.
- WAF in front of the ALB: AWS managed common rule set, known-bad-inputs, and a rate rule.

---

## 7. Data protection

- S3: SSE-KMS with a project CMK, versioning on, Block Public Access on, TLS-only bucket policy,
  lifecycle rules, access logging.
- Database: TLS enforced, storage encrypted, no public endpoint, network allow-list, least-privilege
  DB roles (the app role cannot `DROP`), RLS where Supabase supports it.
- Redis: encryption in transit and at rest, AUTH token from Secrets Manager, private subnets.
- Presigned URLs: issued only after the same authorization check as retrieval, TTL ≤ 5 minutes,
  never logged, never placed into the model's context.
- Backups encrypted; restores tested (Phase 11).

---

## 8. Containers and supply chain

- Multi-stage build; runtime image is slim/distroless.
- Non-root UID, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, all capabilities
  dropped, seccomp default profile.
- No secrets in `ENV`, `ARG`, layers or the image at all. Verified by a CI check on image history.
- Pin base images by digest. Pin dependencies with a lockfile.
- ECR scan-on-push; build fails on HIGH/CRITICAL unless an exception is documented with an expiry.
- Generate and store an SBOM per image; sign images.
- Resource requests and limits on every container; PDBs on every Deployment.

---

## 9. Secrets

- AWS Secrets Manager is the only source. Path `ekba/<env>/<name>`, KMS-encrypted.
- Injected into pods by the External Secrets Operator. Never baked, never in a ConfigMap, never in
  a `.env` inside an image.
- `scripts/seed_secrets.py` creates or updates. It contains **no delete code path** and a test
  asserts that.
- Never log, print, echo, or return a secret. Redact by key name in the logging filter.
- Pre-commit: `detect-secrets` + `gitleaks`. A blocked commit is never bypassed with `--no-verify`.
- Rotation adds a version; it never deletes.

---

## 10. Audit logging

Write an `audit_events` row for: authentication failure, authorization denial, document upload,
document deletion, grant change, injection detection, guardrail block, agent principal-override
attempt, unregistered-tool attempt, budget breach, admin metric access, secret seeding run.

Each row: `tenant_id`, `actor_id`, `action`, `resource_type`, `resource_id`, `outcome`,
`correlation_id`, `ip`, `metadata_json`, `created_at`. Append-only — no API path updates or deletes
these rows.

---

## 11. Review checklist (run before every PR touching this surface)

- [ ] Every new route declares auth, a rate-limit policy and a response model
- [ ] No client- or model-supplied principal field is trusted anywhere
- [ ] Every vector query goes through the chokepoint; every SQL read is tenant-scoped
- [ ] New tools (if any) are read-only, role-gated, strictly typed and registered
- [ ] No secret in code, tests, logs, fixtures or the image
- [ ] Errors are generic in prod and carry a correlation ID
- [ ] New AWS resources are tagged `Project=ekba` and least-privileged
- [ ] No control was weakened, skipped or mocked away to make a test pass
- [ ] No delete of a secret or of non-project infrastructure anywhere in the change
