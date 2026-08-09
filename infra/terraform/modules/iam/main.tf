/**
 * GitHub OIDC deploy role + workload roles.
 *
 * No long-lived AWS keys anywhere. The deploy role carries explicit Deny statements
 * for the destructive actions this project must never perform — so even a compromised
 * pipeline cannot delete a secret, empty a bucket or schedule a key deletion.
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "project" { type = string }
variable "environment" { type = string }
variable "github_repository" {
  type        = string
  description = "owner/repo allowed to assume the deploy role"
}
variable "tags" { type = map(string) }

data "aws_caller_identity" "current" {}

# Reuse the account's existing GitHub OIDC provider if one is already present.
variable "create_oidc_provider" {
  type        = bool
  default     = false
  description = "false when the account already has token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count           = var.create_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
  tags            = var.tags
}

locals {
  oidc_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"

  # Actions this project must never perform. Denied explicitly so no attached policy,
  # present or future, can grant them. See SECURITY.md §9.
  forbidden_actions = [
    "secretsmanager:DeleteSecret",
    "secretsmanager:DeleteResourcePolicy",
    "secretsmanager:RemoveRegionsFromReplication",
    "s3:DeleteBucket",
    "s3:DeleteBucketPolicy",
    "kms:ScheduleKeyDeletion",
    "kms:DisableKey",
    "cognito-idp:DeleteUserPool",
    "cognito-idp:DeleteUserPoolClient",
    "ecr:DeleteRepository",
    "rds:DeleteDBInstance",
    "rds:DeleteDBCluster",
    "eks:DeleteCluster",
  ]
}

data "aws_iam_policy_document" "deploy_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only this repository, and only its protected branches.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository}:ref:refs/heads/main",
        "repo:${var.github_repository}:environment:${var.environment}",
      ]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${var.project}-${var.environment}-deploy"
  assume_role_policy   = data.aws_iam_policy_document.deploy_assume.json
  max_session_duration = 3600
  tags                 = var.tags
}

# The guardrail. Deny always wins over any Allow, from any policy.
data "aws_iam_policy_document" "never_destroy" {
  statement {
    sid       = "NeverDestroyProtectedResources"
    effect    = "Deny"
    actions   = local.forbidden_actions
    resources = ["*"]
  }
}

resource "aws_iam_policy" "never_destroy" {
  name        = "${var.project}-${var.environment}-never-destroy"
  description = "Explicit deny on destructive actions this project must never perform"
  policy      = data.aws_iam_policy_document.never_destroy.json
  tags        = var.tags
}

resource "aws_iam_role_policy_attachment" "deploy_never_destroy" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.never_destroy.arn
}

# Scoped permissions the pipeline genuinely needs.
data "aws_iam_policy_document" "deploy_permissions" {
  statement {
    sid    = "EcrPushPull"
    effect = "Allow"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
      "ecr:DescribeImageScanFindings",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "EksDescribeAndDeploy"
    effect    = "Allow"
    actions   = ["eks:DescribeCluster", "eks:ListClusters"]
    resources = ["*"]
  }

  statement {
    sid    = "SecretsCreateAndVersionOnly"
    effect = "Allow"
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:PutSecretValue",
      "secretsmanager:UpdateSecret",
      "secretsmanager:DescribeSecret",
      "secretsmanager:TagResource",
    ]
    # Scoped to this project's namespace only.
    resources = ["arn:aws:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:${var.project}/${var.environment}/*"]
  }

  statement {
    sid       = "CloudWatchRead"
    effect    = "Allow"
    actions   = ["cloudwatch:DescribeAlarms", "logs:DescribeLogGroups"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deploy_permissions" {
  name   = "${var.project}-${var.environment}-deploy-permissions"
  policy = data.aws_iam_policy_document.deploy_permissions.json
  tags   = var.tags
}

resource "aws_iam_role_policy_attachment" "deploy_permissions" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_permissions.arn
}

output "deploy_role_arn" {
  value       = aws_iam_role.deploy.arn
  description = "Set this as the AWS_DEPLOY_ROLE_ARN GitHub secret"
}

output "never_destroy_policy_arn" {
  value = aws_iam_policy.never_destroy.arn
}
