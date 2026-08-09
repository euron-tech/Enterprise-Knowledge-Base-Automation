/**
 * Workload identities (IRSA). One role per service account, least privilege.
 *
 * The API pod may read only its own project's secrets and its own buckets.
 * Nothing here can delete anything — the never-destroy policy is attached to both.
 */

locals {
  oidc_url = module.eks.oidc_provider_url
}

data "aws_caller_identity" "me" {}

# ---------------------------------------------------------------- external-secrets
data "aws_iam_policy_document" "eso_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:sub"
      values   = ["system:serviceaccount:ekba-dev:ekba-external-secrets"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "external_secrets" {
  name               = "ekba-dev-external-secrets"
  assume_role_policy = data.aws_iam_policy_document.eso_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "eso_read" {
  statement {
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    # Scoped to this project and environment. It cannot read the account's other
    # secrets — euron-api-server-secrets, openai-api-key and the rest are unreachable.
    resources = [
      "arn:aws:secretsmanager:ap-south-1:${data.aws_caller_identity.me.account_id}:secret:ekba/dev/*",
    ]
  }
  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [module.secrets.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "eso_read" {
  name   = "read-project-secrets"
  role   = aws_iam_role.external_secrets.id
  policy = data.aws_iam_policy_document.eso_read.json
}

resource "aws_iam_role_policy_attachment" "eso_never_destroy" {
  role       = aws_iam_role.external_secrets.name
  policy_arn = module.iam.never_destroy_policy_arn
}

# ---------------------------------------------------------------- api pod
data "aws_iam_policy_document" "api_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:sub"
      values   = ["system:serviceaccount:ekba-dev:ekba-api"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "ekba-dev-api"
  assume_role_policy = data.aws_iam_policy_document.api_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "api_permissions" {
  # Documents in, derived artifacts out. No bucket-level delete.
  statement {
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = [
      "arn:aws:s3:::ekba-dev-documents/*",
      "arn:aws:s3:::ekba-dev-derived/*",
    ]
  }
  statement {
    effect  = "Allow"
    actions = ["s3:ListBucket"]
    resources = [
      "arn:aws:s3:::ekba-dev-documents",
      "arn:aws:s3:::ekba-dev-derived",
    ]
  }
  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [module.secrets.kms_key_arn]
  }
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:ap-south-1:${data.aws_caller_identity.me.account_id}:log-group:/ekba/dev/*"]
  }
  statement {
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["EKBA/dev"]
    }
  }
}

resource "aws_iam_role_policy" "api_permissions" {
  name   = "api-runtime"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_permissions.json
}

resource "aws_iam_role_policy_attachment" "api_never_destroy" {
  role       = aws_iam_role.api.name
  policy_arn = module.iam.never_destroy_policy_arn
}

output "api_role_arn" { value = aws_iam_role.api.arn }
output "external_secrets_role_arn" { value = aws_iam_role.external_secrets.arn }
