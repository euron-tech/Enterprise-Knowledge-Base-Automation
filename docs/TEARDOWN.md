# Teardown record — dev environment, 2026-08-09

The repository owner authorized destroying every `Project=ekba` resource. This is
the record of what happened, including what did not go smoothly.

## Result

All active EKBA resources removed. Verified by direct API call per service rather
than the resource-tagging index, which lags and reported deleted resources as
still present for some minutes:

| Service | Remaining |
|---|---|
| EKS clusters, node groups | 0 |
| VPC, subnets, NAT, EIPs, endpoints | 0 |
| ElastiCache | 0 |
| Load balancers | 0 |
| CloudFront distribution | 0 |
| S3 buckets | 0 |
| ECR repositories | 0 |
| Cognito user pool | 0 |
| DynamoDB lock table | 0 |
| CloudWatch log groups and alarms | 0 |
| SNS topics | 0 |
| IAM roles | 0 |

The account's existing estate was untouched: 56 S3 buckets, 19 ECR repositories,
9 non-project secrets and 3 VPCs all still present, including
`euri-main-server-prod-secrets`, `openai-api-key` and the RDS master credentials.

## Pending, by design

Six secrets are scheduled for deletion with a 30-day recovery window, and the KMS
key is in `PendingDeletion` until 2026-09-08. Both were left recoverable
deliberately rather than force-deleted.

To purge immediately instead:

```bash
for s in database-url euri-api-key langsmith-api-key qdrant-api-key qdrant-url redis-url; do
  aws secretsmanager delete-secret --secret-id ekba/dev/$s --force-delete-without-recovery
done
aws kms schedule-key-deletion --key-id <id> --pending-window-in-days 7
```

**Deleting a Secrets Manager entry does not revoke the credential.** The Euri key,
Supabase password and Qdrant JWT are still live at those providers and must be
rotated at source.

## What did not go smoothly

**The first destroy failed on ECR.** `force_delete = true` was set in the module,
but a destroy plan uses the value stored in state, which was still `false`.
Changing that attribute requires an update apply before the destroy. The failure
cascaded and left 21 resources behind, including a NAT gateway. Fixed by deleting
the images by digest — including untagged manifest layers, which
`list-images` did not return — and re-running.

**Two resources were orphaned because they were created outside Terraform.** The
ALB controller's IAM role and policy were created by hand with the AWS CLI during
deployment, so `terraform destroy` never knew about them, and EKS created its own
cluster log group. Both were removed manually afterwards.

This is a real process failure, not just an inconvenience: `REQUIREMENTS.md`
R-NFR-6 says all infrastructure is defined in Terraform with no manual changes.
Anything created by hand is invisible to teardown and to drift detection. If this
environment is rebuilt, the ALB controller's IAM should be a Terraform module.

## Before any redeploy

`prevent_destroy` was set to `false` across bootstrap, cognito, ecr, eks, s3 and
secrets to permit this teardown, and ECR `force_delete` was set to `true`. Commit
`6cb94ea` records that. **Restore both before redeploying** — those guards are the
reason a stray plan cannot quietly delete a bucket or a secret.

## Still running, outside AWS

Qdrant Cloud and Supabase are third-party and were never Terraform-managed. They
continue to run and bill until removed in their own consoles.
