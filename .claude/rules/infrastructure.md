# Rule — Infrastructure (Terraform, Kubernetes, CI/CD)

Applies to `infra/terraform/`, `k8s/`, `.github/workflows/`, Dockerfiles and deployment scripts.
Read with `00-root.md` and `security.md`.

---

## 1. The destructive-action rules restated (they matter most here)

1. **Never delete, modify or destroy AWS infrastructure that is not tagged `Project=ekba` and
   present in this repo's Terraform state.** Other workloads share this account.
2. **Never delete an AWS Secrets Manager secret.** Create and version only.
3. **Never run `terraform destroy`** without explicit written per-invocation authorization. It is
   never in a workflow, a Makefile target or a script.
4. **Always read the plan.** If `terraform plan` shows a `destroy` or a `must be replaced` you did
   not intend, stop and show the user before applying.
5. `prevent_destroy = true` on: S3 buckets, Secrets Manager secrets, their KMS keys, ECR
   repositories, the Cognito user pool, the Terraform state bucket and lock table.

---

## 2. Terraform structure

```
infra/terraform/
├── modules/
│   ├── network/ eks/ ecr/ s3/ elasticache/ cognito/
│   ├── secrets/ iam/ observability/ codedeploy/ waf/
└── envs/
    ├── dev/   backend.tf main.tf variables.tf outputs.tf terraform.tfvars(gitignored)
    └── prod/  ...
```

Rules:

- Modules are reusable and environment-agnostic. Environments compose modules and supply values.
- No hard-coded account ids, ARNs, regions or CIDRs inside modules — variables with validation.
- Remote state in S3 with DynamoDB locking, one state per environment, versioning and encryption on.
- Pin the Terraform version and every provider version.
- `terraform fmt` and `terraform validate` clean; `tflint` and `checkov`/`tfsec` pass in CI.
- No `local-exec` provisioners doing work that belongs in a pipeline step.
- Every resource carries `Project=ekba`, `Environment`, `ManagedBy=terraform`, `Owner`.
- Outputs never contain secret values.

---

## 3. Terraform workflow

```
terraform init      # never with -reconfigure against prod without saying why
terraform fmt -check
terraform validate
terraform plan -out=tfplan   # READ IT
terraform show tfplan        # confirm zero unintended destroys/replacements
terraform apply tfplan       # only after review; prod requires human approval
```

- Never `-auto-approve` in prod. Dev only when the plan was reviewed in the same session.
- Never `terraform state rm` or `import` against prod without user approval.
- Never edit state by hand.
- Never commit `.tfstate`, `.terraform/`, or a populated `.tfvars`.
- Drift is fixed in code, never by a console change.

---

## 4. Secrets in infrastructure

- Terraform creates the Secrets Manager **containers** and the KMS keys. It does not carry values
  in code.
- Values are seeded by `scripts/seed_secrets.py` from a local `.env`: create on first run, add a
  version thereafter. No delete path exists in that script, and a test asserts it.
- `ignore_changes = [secret_string]` on secret version resources so Terraform never reverts a
  rotated value.
- Dev and prod currently share credential values by explicit user decision — recorded in
  `SECURITY.md` §7 with the rotation plan in Phase 11.
- Never output a secret, never write one to state deliberately, never echo one in a pipeline log.

---

## 5. Kubernetes

- Namespaces: `ekba-dev`, `ekba-prod`. Nothing this project does touches any other namespace, and
  destructive verbs are scoped to these two.
- Every workload: resource requests **and** limits, liveness/readiness/startup probes, PDB, HPA,
  topology spread, and a dedicated ServiceAccount with IRSA.
- Pod security: non-root UID, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`,
  all capabilities dropped, seccomp `RuntimeDefault`.
- NetworkPolicies: default-deny egress; allow only the Euri gateway, Supabase, Qdrant, Redis and
  the AWS endpoints in use. This is the SSRF backstop for the agent.
- Secrets arrive via the External Secrets Operator from Secrets Manager. Never a plain `Secret`
  manifest with a value, never a value in a ConfigMap.
- No `cluster-admin` for workloads. Namespace-scoped RBAC, least privilege.
- Images referenced by digest, never `:latest`.

---

## 6. Containers

- Multi-stage build; slim or distroless runtime.
- Non-root user created in the image; `USER` set.
- No secrets in `ARG`, `ENV`, layers or the build context (`.dockerignore` covers `.env`, `.git`,
  `*.tfvars`, keys).
- Pin base images by digest; rebuild on base updates rather than floating tags.
- Healthcheck defined; signals handled so pods terminate gracefully.
- CI asserts: runs as non-root, no secret strings in `docker history` or layers, scan gate passed.

---

## 7. CI/CD

Workflows: `pr.yml`, `build.yml`, `deploy-dev.yml`, `deploy-prod.yml`, `evals.yml`.

Rules:

- AWS access via GitHub OIDC with a scoped role. **No long-lived AWS keys in GitHub.**
- Least-privilege `permissions:` block in every workflow; `contents: read` by default.
- Pin actions by commit SHA.
- Secrets in GitHub Actions are only what is needed to reach AWS; application secrets live in
  Secrets Manager.
- **No workflow may run `terraform destroy`, delete a secret, or delete any AWS resource.** A CI
  test greps for these and fails the build if found.
- Prod deploys require a manual approval environment gate.
- Every pipeline step logs the correlation/run id and is idempotent where re-runnable.

---

## 8. Blue/green deployment

- CodeDeploy orchestrates the release stage; in-cluster traffic shifting is performed by the chosen
  mechanism (Argo Rollouts by default — see `ARCHITECTURE.md` §10 open question 4).
- Canary 10% → bake → 100%. Bake window watches the deployment alarm set.
- Automatic rollback on any alarm in that set during bake.
- **Rollback restores the previous version. It never deletes infrastructure, never deletes a
  secret, never destroys state.**
- Database migrations are backward-compatible (expand/contract). Never a destructive migration in
  the same release as the code that stops using a column.
- Smoke tests run against green before any traffic shift: health, auth, one cited answer, one
  refusal, one cross-tenant probe.

---

## 9. Observability infrastructure

- Log groups created in Terraform with KMS encryption and explicit retention (dev 30d, prod 400d).
- Metric filters and alarms in code, not the console.
- Alarm set: unhealthy workloads, 5xx rate, p95 latency, deployment failure, database connection
  pressure, ingestion backlog, agent loop-cap breach rate, cost spike.
- Alarms route to SNS; the deployment alarm set is referenced by the CodeDeploy deployment group.
- Dashboards per environment, defined in code.

---

## 10. Cost

- No resource created outside Terraform.
- Right-size node groups; use Spot for ingestion workers where interruption is tolerable.
- S3 lifecycle rules to IA/Glacier; log retention bounded.
- Budget alarms per environment; tag-based cost allocation via `Project=ekba`.
- Tell the user the expected cost before creating anything new or running a large embedding job.

---

## 11. Before you apply anything — checklist

- [ ] Plan reviewed; zero unintended `destroy` or `replace`
- [ ] Every touched resource is tagged `Project=ekba` and in this repo's state
- [ ] No secret deletion anywhere in the change
- [ ] `prevent_destroy` still present on protected resources
- [ ] IAM changes are least-privilege; no wildcards added
- [ ] Nothing outside `ekba-dev` / `ekba-prod` is affected
- [ ] The user approved, if this is prod or anything is being replaced
