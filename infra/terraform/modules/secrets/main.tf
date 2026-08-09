/**
 * Secrets Manager containers + KMS key.
 *
 * Terraform creates the CONTAINERS. Values are seeded by scripts/seed_secrets.py,
 * which has no delete code path. Nothing here may ever destroy a secret.
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "project" { type = string }
variable "environment" { type = string }
variable "secret_names" {
  type        = list(string)
  description = "Logical names, created at ekba/<env>/<name>"
}
variable "tags" { type = map(string) }

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# The key is used by three different services. Without an explicit policy the
# default only permits IAM principals, so CloudWatch Logs and EKS are refused at
# CreateLogGroup / cluster creation time with AccessDeniedException.
data "aws_iam_policy_document" "kms" {
  # Root retains administration, otherwise the key becomes unmanageable.
  statement {
    sid       = "EnableIAMPolicies"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid    = "AllowCloudWatchLogs"
    effect = "Allow"
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${data.aws_region.current.name}.amazonaws.com"]
    }
    # Scope to this account's log groups so the key cannot be used by another.
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values = [
        "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:*",
      ]
    }
  }

  # EKS envelope-encrypts Kubernetes Secrets with this key.
  statement {
    sid    = "AllowEksSecretsEncryption"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
      "kms:CreateGrant",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "secrets" {
  description             = "${var.project}-${var.environment} secrets, logs and EKS envelope encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms.json
  tags                    = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/${var.project}-${var.environment}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

resource "aws_secretsmanager_secret" "this" {
  for_each = toset(var.secret_names)

  name       = "${var.project}/${var.environment}/${each.value}"
  kms_key_id = aws_kms_key.secrets.arn
  # Long window so an accidental removal from state is still recoverable.
  recovery_window_in_days = 30
  tags                    = var.tags

  lifecycle {
    # Absolute rule: secrets are never destroyed by this project.
    # See SECURITY.md §9 and .claude/rules/00-root.md §1.2.
    prevent_destroy = true
  }
}

output "secret_arns" {
  value = { for k, v in aws_secretsmanager_secret.this : k => v.arn }
}

output "kms_key_arn" {
  value = aws_kms_key.secrets.arn
}

# Deliberately NO output of secret values, and no aws_secretsmanager_secret_version
# resource — values never pass through Terraform state.
