---
name: deployment
description: Deploy the EKBA platform to AWS with a blue/green release — Terraform apply, secret seeding into AWS Secrets Manager, image build and scan, EKS rollout via CodeDeploy with canary traffic shift, alarm-gated bake, smoke verification, and rollback. Use when the user asks to deploy, release, ship, roll out, promote, or roll back, for dev or prod.
---

# Blue/Green Deployment

Ships a version to `dev` or `prod` with zero downtime and an automatic rollback path.

## Absolute rules for this skill

1. **Never delete AWS infrastructure that is not tagged `Project=ekba` and in this repo's Terraform
   state.** Other workloads share this account.
2. **Never delete an AWS Secrets Manager secret.** Create and version only — no `delete-secret`, no
   `--force-delete-without-recovery`, not to fix a name conflict, not in dev.
3. **Never run `terraform destroy`.** It requires explicit written per-invocation authorization from
   the owner and is never part of a deploy or a rollback.
4. **Read every plan before applying.** Any unintended `destroy` or `must be replaced` stops the
   deploy — show the user and wait.
5. **Rollback restores a previous version. It never deletes infrastructure, secrets or state.**
6. **Prod requires explicit user confirmation** before the apply and before the traffic shift.
7. Never print a secret value at any point.

---

## Inputs

Confirm before starting:

| Input | Notes |
|---|---|
| Environment | `dev` \| `prod` |
| Git ref | Branch, tag or SHA; must be CI-green |
| Migration in this release? | If yes, confirm it is expand/contract-safe |
| Secret changes in this release? | If yes, seed before deploy |

---

## Phase 1 — Pre-flight

```bash
git rev-parse --short HEAD
gh run list --commit <sha> --limit 5      # CI must be green for this exact SHA
aws sts get-caller-identity                # confirm the intended account
kubectl config current-context             # confirm the intended cluster
```

Checklist — all must be true:

- [ ] CI green on this exact SHA (lint, types, unit, security, agent suites)
- [ ] `e2e-verification` skill passed against `dev` for this SHA (mandatory before a prod deploy)
- [ ] Migration, if any, is backward-compatible — both blue and green run simultaneously
- [ ] Correct AWS account and cluster
- [ ] Alarm set is healthy now (a deploy into an already-firing alarm cannot be alarm-gated)
- [ ] For prod: the user has explicitly approved this release

Record the current live image digest — this is the rollback target. Write it down before anything
changes.

---

## Phase 2 — Secrets (only if secrets changed)

```bash
python scripts/seed_secrets.py --env <env> --file .env --dry-run   # ALWAYS dry-run first
python scripts/seed_secrets.py --env <env> --file .env
```

- The script creates a secret on first run and adds a version thereafter. It has **no delete path**.
- Dev and prod deliberately share credential values for now (recorded in `SECURITY.md` §7).
- Never echo a value. The dry run prints key names and a create/update verdict only.
- If a secret exists and the value differs, that is an update (new version) — never a recreate.

---

## Phase 3 — Infrastructure

```bash
terraform -chdir=infra/terraform/envs/<env> init
terraform -chdir=infra/terraform/envs/<env> plan -out=tfplan
terraform -chdir=infra/terraform/envs/<env> show tfplan
```

**Read the plan.** Stop and show the user if it contains:

- Any `destroy` you did not deliberately intend
- Any `must be replaced` on a stateful resource (S3, secrets, KMS, Cognito, ECR, ElastiCache)
- Any change to `prevent_destroy` protections
- Any IAM widening

Then:

```bash
terraform -chdir=infra/terraform/envs/<env> apply tfplan
```

Prod is never `-auto-approve`.

---

## Phase 4 — Build, scan, push

```bash
docker build -t $ECR/ekba-api:$SHA backend/
docker build -t $ECR/ekba-worker:$SHA backend/ -f backend/Dockerfile.worker
```

Gates before push — all must pass:

- [ ] ECR/Trivy scan: no HIGH or CRITICAL without a documented, expiring exception
- [ ] Image runs as non-root
- [ ] No secret strings in `docker history` or any layer
- [ ] SBOM generated and stored; image signed

Push by digest and reference by digest everywhere. Never `:latest`.

---

## Phase 5 — Migrations (if any)

Expand/contract only. Run the expand step **before** the traffic shift, so blue and green both work:

```bash
kubectl -n ekba-<env> run migrate-$SHA --image=$ECR/ekba-api@$DIGEST --restart=Never \
  --command -- alembic upgrade head
kubectl -n ekba-<env> logs -f job/migrate-$SHA
```

Never run a destructive migration (drop column, drop table, narrow a type) in the same release as
the code change. The contract step ships a release later.

---

## Phase 6 — Blue/green rollout

```bash
# Deploy green alongside blue
kubectl -n ekba-<env> apply -f k8s/<env>/            # or helm upgrade with the new digest
```

Before any traffic shift, smoke-test green directly (bypassing the service):

1. `/healthz` and `/readyz` green
2. Auth: a valid token succeeds, an invalid token 401s
3. One question producing a cited answer with all eleven response fields
4. One question producing the exact refusal string
5. One cross-tenant probe returning zero chunks
6. One agent multi-tool question completing within budget

**A failed smoke test aborts before any user traffic moves.** Delete the green replica set only —
never touch blue, never touch infrastructure.

Then shift traffic:

```
canary 10%  →  bake (watch the alarm set)  →  100%  →  bake  →  retire blue
```

- Dev: bake ~5 minutes per step. Prod: ~15 minutes per step.
- The CodeDeploy deployment group is wired to the alarm set — any alarm during bake triggers an
  automatic rollback.
- Watch during bake: 5xx rate, p95 latency, auth failures, model failures, Qdrant failures, cache
  hit ratio, agent loop-cap breaches, cost per request.
- Blue is retired only after the full bake at 100% completes clean.

---

## Phase 7 — Post-deploy verification

```bash
kubectl -n ekba-<env> get pods
kubectl -n ekba-<env> rollout status deploy/ekba-api
```

- Re-run the smoke set against the live endpoint
- Confirm a fresh correlation ID appears in the response, the app logs, the LangSmith trace and
  `request_usage`
- Confirm every alarm is `OK`
- Compare error rate, latency and cost per request against the pre-deploy baseline
- For prod, run the `e2e-verification` skill's read-only prod subset

Record: SHA, image digest, deploy time, the previous digest (rollback target), migration applied,
and the observed metric deltas.

---

## Rollback

Trigger immediately on: 5xx rate above baseline, p95 latency regression, an alarm firing, auth or
model failure spikes, or any correctness failure in the smoke set. Rolling back early is cheap;
debugging in production is not.

**Automatic** — CodeDeploy reverts to blue when a deployment alarm fires during bake. Verify it
completed, then investigate.

**Manual:**

```bash
kubectl -n ekba-<env> rollout undo deploy/ekba-api        # or shift traffic back to blue
kubectl -n ekba-<env> rollout status deploy/ekba-api
```

Rollback rules:

- Restores the previously recorded image digest. **Deletes nothing.**
- Never deletes a secret, a bucket, a collection, or any infrastructure.
- Never runs `terraform destroy`.
- If a migration shipped: because migrations are expand/contract, the old code still works against
  the new schema. **Do not run `alembic downgrade` to "undo" a deploy** — that is a data-loss risk.
  If a schema revert is genuinely required, stop and ask the user.
- After rollback: confirm health, confirm alarms clear, write up what failed, and fix forward.

---

## Deployment report

```
EKBA DEPLOYMENT
Environment: <env>       Commit: <sha>        Image digest: <digest>
Previous digest (rollback target): <digest>
Started: <ts>            Completed: <ts>

Terraform:     <N added, N changed, 0 destroyed>
Secrets:       <N created, N versioned, 0 deleted>
Migration:     <none | expand step applied>
Smoke (green): <6/6 passed>
Traffic shift: 10% <ts> → 100% <ts>, bake clean
Alarms:        all OK
Metrics:       5xx <x%> (baseline <y%>) · p95 <x ms> (baseline <y ms>) · cost/req <x>

RESULT: SUCCESS | ROLLED BACK

NOTES / FOLLOW-UPS
- <...>
```

Always report `0 destroyed` explicitly for both Terraform and secrets. If either is non-zero,
explain exactly what and why — and if it was not deliberately approved by the user, treat it as an
incident.
