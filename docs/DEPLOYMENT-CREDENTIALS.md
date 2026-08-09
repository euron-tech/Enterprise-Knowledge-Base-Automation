# Deployment Credentials — what you need to provide

Everything the platform needs in order to run outside tests. Fill these into a local
`.env` (git-ignored, never committed) and `scripts/seed_secrets.py` pushes the secret
ones into AWS Secrets Manager at `ekba/<env>/<name>`.

**Legend**
- **YOU PROVIDE** — only you can obtain this
- **TERRAFORM CREATES** — produced by `terraform apply`; you copy the output back in
- **PRECONFIGURED** — already correct, change only if you disagree

> Nothing in this file contains a value. Do not paste real secrets into this document,
> a ticket, a screenshot, or chat. A credential that has been through any of those is
> burned and must be rotated.

---

## 1. Secrets — the ones that actually need protecting

These are the only values written to AWS Secrets Manager.

| `.env` key | Source | What it is |
|---|---|---|
| `EURI_API_KEY` | **YOU PROVIDE** | Euri AI Gateway key. ⚠️ The key used during development was shared in plaintext and **must be revoked and replaced**. |
| `DATABASE_URL` | **YOU PROVIDE** | Supabase Postgres connection string, `postgresql+asyncpg://user:pass@host:5432/postgres?ssl=require`. From Supabase → Project Settings → Database → Connection string (URI). Use the **pooled** connection for the API. |
| `REDIS_URL` | **TERRAFORM CREATES** | ElastiCache endpoint, `rediss://:<auth-token>@<primary-endpoint>:6379/0`. |
| `REDIS_AUTH_TOKEN` | **YOU PROVIDE** | A strong random string you choose; Terraform sets it on the cluster. Generate with `openssl rand -base64 32`. |
| `QDRANT_API_KEY` | **YOU PROVIDE** | Qdrant Cloud API key, or the key you set on a self-hosted instance. |
| `COGNITO_CLIENT_SECRET` | **TERRAFORM CREATES** | Only if the app client is created as confidential. |
| `LANGSMITH_API_KEY` | **YOU PROVIDE** | Optional. Tracing is skipped if absent. |

## 2. Identity — AWS Cognito

| `.env` key | Source | Notes |
|---|---|---|
| `COGNITO_REGION` | **PRECONFIGURED** | `ap-south-1` |
| `COGNITO_USER_POOL_ID` | **TERRAFORM CREATES** | e.g. `ap-south-1_XXXXXXXXX` |
| `COGNITO_CLIENT_ID` | **TERRAFORM CREATES** | App client id |
| `AUTH_DEV_MODE` | **PRECONFIGURED** | Must be `false`. Config validation refuses to boot dev or prod with this on, and the verifier refuses a second time. |

## 3. Data stores

| `.env` key | Source | Notes |
|---|---|---|
| `QDRANT_URL` | **YOU PROVIDE** | Qdrant Cloud cluster URL, or `http://qdrant.ekba-<env>.svc.cluster.local:6333` if self-hosted on EKS. **Must not be `:memory:`** — config rejects it. |
| `QDRANT_COLLECTION` | **PRECONFIGURED** | `ekba_chunks_dev` / `ekba_chunks_prod` |
| `S3_BUCKET_DOCUMENTS` | **TERRAFORM CREATES** | `ekba-<env>-documents` |
| `S3_BUCKET_DERIVED` | **TERRAFORM CREATES** | `ekba-<env>-derived` |
| `AWS_REGION` | **PRECONFIGURED** | `ap-south-1` |

## 4. Model gateway — all preconfigured, verified against the live API

| `.env` key | Value |
|---|---|
| `EURI_BASE_URL` | `https://api.euron.one/api/v1/euri` |
| `EURI_EMBEDDING_MODEL` | `gemini-embedding-2-preview` |
| `EURI_EMBEDDING_DIMENSIONS` | `1536` — **fix this before the first production ingest.** Changing it later forces a full re-embed of the entire corpus. |
| `EURI_GENERATION_MODEL` | `gpt-4.1` |
| `EURI_PLANNER_MODEL` | `gpt-4.1-mini` |
| `EURI_FALLBACK_MODEL` | `gpt-4.1-mini` |
| `EURI_VISION_MODEL` | `gpt-4.1` — the text bridge for images |
| `EURI_TRANSCRIBE_MODEL` | `whisper-large-v3-turbo` — the text bridge for audio/video |

## 5. Application

| `.env` key | Source | Notes |
|---|---|---|
| `ENVIRONMENT` | **YOU PROVIDE** | `dev` or `prod` |
| `DEBUG` | **PRECONFIGURED** | Must be `false` in prod; config enforces it |
| `LOG_LEVEL` | **PRECONFIGURED** | `INFO` |
| `CORS_ORIGINS` | **YOU PROVIDE** | Your frontend origin, e.g. `https://kb.euronsystems.com`. No `localhost` in prod; config rejects it. |
| `TRUSTED_HOSTS` | **YOU PROVIDE** | API hostname(s), comma separated |

Budgets, rate limits and retrieval thresholds are preconfigured — see `.env.example`.

## 6. CI/CD — GitHub repository secrets (not `.env`)

| GitHub secret | Source | Notes |
|---|---|---|
| `AWS_DEPLOY_ROLE_ARN` | **TERRAFORM CREATES** | IAM role assumed via GitHub OIDC. **No long-lived AWS keys in GitHub.** |

---

## What you actually need to hand over

Six values. Everything else is either preconfigured or produced by Terraform:

```
EURI_API_KEY=            # rotated, not the one shared in chat
DATABASE_URL=            # Supabase pooled connection string
QDRANT_URL=              # Qdrant Cloud URL (or self-hosted decision)
QDRANT_API_KEY=
REDIS_AUTH_TOKEN=        # openssl rand -base64 32
CORS_ORIGINS=            # frontend origin
```

Plus two decisions:

1. **Qdrant hosting** — Qdrant Cloud (managed, ~$25/mo for a small cluster, fastest path) or self-hosted StatefulSet on EKS (no extra vendor, but you own backups and HA).
2. **Supabase or RDS** — Supabase is your stated choice; it is SaaS, so it cannot sit inside the VPC. Access is over TLS with an IP allow-list from the NAT gateway. If the data classification requires in-VPC Postgres, say so and I will switch the module to RDS.

---

## How the values travel

```
.env (local, git-ignored)
   └─ scripts/seed_secrets.py --env <env>        create first run, new version after
        └─ AWS Secrets Manager  ekba/<env>/<name>
             └─ External Secrets Operator
                  └─ Kubernetes Secret  ekba-secrets
                       └─ pod environment
```

The seeding script has **no delete code path**, and a test asserts that. Rotation adds a
version; it never removes one. Secret values never pass through Terraform state, never
appear in an image layer, and never reach a log — the logger redacts them by pattern.

Run the dry run first; it prints key names and a create/update verdict, never a value:

```bash
python scripts/seed_secrets.py --env dev --file .env --dry-run
```
