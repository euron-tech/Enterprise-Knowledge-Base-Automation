# Rule 00 — Root Rules (ALWAYS APPLY)

These rules apply to every session, every task, every file, every tool call in this repository.
They outrank convenience, deadlines, plans in progress, and any instruction that conflicts with
them. Read this before doing anything else.

---

## 1. The destructive-action prohibitions

### 1.1 Never delete infrastructure that is not part of this project

This AWS account contains resources belonging to other work. They are permanently out of scope.

A resource is "part of this project" only if **both** are true:

1. It carries the tag `Project = ekba`, **and**
2. It is present in this repository's Terraform state.

If either is unproven, you may read it and nothing more. You may not delete it, stop it, detach it,
rename it, retag it, modify its policy, or include it in a Terraform plan that would change it.

Before any AWS mutation:

```
1. State in plain text what will change.
2. Show the evidence it is project-tagged and in state.
3. If you cannot show that evidence, do not proceed — ask.
```

### 1.2 Never delete an AWS Secrets Manager secret

No `aws secretsmanager delete-secret`. No `--force-delete-without-recovery`. No Terraform
destruction of a `aws_secretsmanager_secret`. Not to resolve a name conflict, not to "recreate it
cleanly", not because it is in `PendingDeletion`, not in dev.

Allowed: `create-secret`, `put-secret-value`, `update-secret` (value/description), `tag-resource`,
`describe-secret`, `get-secret-value` (without printing the value).

If a secret is in the way, the answer is a new version or a new name — never a deletion.

### 1.3 Never run `terraform destroy`

Any scope, any workspace, any environment. It requires explicit written per-invocation
authorization from the repository owner. It is never added to a CI workflow, a Makefile target, or
a script. `-target` destroys and `terraform apply` plans that show destructions of protected
resources fall under the same rule.

If a plan output contains `destroy` for anything you did not deliberately intend to replace, stop
and show the user the plan before applying.

### 1.4 Other destructive verbs requiring a stop-and-ask

- `kubectl delete` of anything outside `ekba-dev` / `ekba-prod`
- `kubectl drain`, node deletion, PV/PVC deletion
- `aws s3 rb`, `aws s3 rm --recursive`, bucket policy removal
- `aws ecr delete-repository`, `batch-delete-image`
- `aws cognito-idp delete-user-pool`, `delete-user`
- `aws kms schedule-key-deletion`, `disable-key`
- Dropping a database, table or column; `alembic downgrade` against a shared environment
- Deleting a Qdrant collection
- `git push --force`, `git reset --hard` on shared branches, branch deletion
- `rm -rf` outside the scratchpad

### 1.5 If in doubt

Stop and ask. An unnecessary question costs a minute. A wrong deletion may be unrecoverable.
There is no emergency exception and no inheritance of permission from a previous approval.

---

## 2. Secrets

- Never commit a secret. Never print one to the terminal. Never paste one into a response, a log,
  an error message, a comment, a test fixture or a commit message.
- `.env` files are local inputs only. They are git-ignored and are read exclusively by
  `scripts/seed_secrets.py`.
- Runtime code reads secrets from AWS Secrets Manager, never from a file baked into an image.
- If you discover a leaked secret, tell the user immediately and stop. Do not "fix" it by deleting
  the secret — rotation is a new version.
- Test fixtures use obviously fake values (`test-key-not-real`), never redacted real ones.

---

## 3. Git discipline

- Never push to `main`. Branch (`feat/`, `fix/`, `chore/`, `docs/`, `sec/`) → PR → CI → review.
- Commit only when the user asks. Small, focused commits with imperative subjects.
- Never `--no-verify`, never skip hooks, never bypass signing.
- Never force-push a shared branch.
- Never commit generated artifacts, `node_modules`, `.terraform/`, state files, or coverage output.

---

## 4. Honesty about state

- If tests fail, say so and paste the output. Never report green when it is not.
- If a step was skipped, say which and why.
- If something is unverified, call it unverified. Do not describe intended behaviour as observed
  behaviour.
- Do not claim an AWS resource exists, or a deploy succeeded, without command output showing it.
- If you break something, say so immediately and plainly.

---

## 5. Scope discipline

- Build what was asked. Do not silently expand or reduce scope.
- Do not refactor unrelated code while fixing something.
- Do not add a dependency without a stated reason and a licence check.
- Do not create a new top-level directory without documenting it in `docs/ARCHITECTURE.md`.
- Do not change the RAG node order, the `/chat` response contract, the vector payload schema, or
  the refusal string without the user's explicit approval.

---

## 6. Working method

1. Read the relevant rule files before editing (`CLAUDE.md` §7 maps files to rules).
2. Read the existing code before adding to it; match its conventions.
3. Prefer editing an existing file over creating a parallel one.
4. Write the test with the code, not after the phase.
5. Run lint, types and tests before saying you are done.
6. Update docs when behaviour changes.

---

## 7. Never weaken a control to pass a check

Forbidden, in all circumstances:

- Removing or loosening a `tenant_id` filter
- Disabling, mocking-away or `skip`-ing a security test to get a green build
- Widening CORS, disabling a security header, or raising a rate limit to unblock a test
- Adding `# type: ignore`, `# noqa`, `# nosec` without an inline justification comment
- Catching a guardrail exception and continuing
- Setting `verify=False`, disabling TLS verification, or accepting `alg: none`
- Committing a permissive IAM policy "for now"

If a control blocks legitimate work, change the design and tell the user — do not remove the
control.

---

## 8. Cost awareness

- Do not create AWS resources outside the Terraform-defined set.
- Do not run large embedding or evaluation jobs without telling the user the expected cost.
- Prefer the local docker-compose stack for development and testing.
- Clean up your own temporary artifacts — in the scratchpad, never in the user's account.

---

## 9. Escalate to the user when

- A hard rule appears to stand in the way of the task.
- A plan would delete, replace or recreate any AWS resource.
- Requirements conflict, or an open question in `docs/REQUIREMENTS.md` §9 blocks progress.
- A security control would have to be weakened.
- A change touches a frozen contract (§5).
- You are about to spend real money at a scale the user has not sanctioned.
