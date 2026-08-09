/** Cognito user pool. prevent_destroy — losing the pool means losing every identity. */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "project" { type = string }
variable "environment" { type = string }
variable "callback_urls" { type = list(string) }
variable "logout_urls" { type = list(string) }
variable "tags" { type = map(string) }

resource "aws_cognito_user_pool" "this" {
  name = "${var.project}-${var.environment}"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  admin_create_user_config {
    allow_admin_create_user_only = true # no self-signup into an enterprise KB
  }

  user_pool_add_ons {
    advanced_security_mode = "ENFORCED"
  }

  # Tenant and department are authoritative in the database, not the token.
  # These exist only as a convenience hint for the UI.
  schema {
    name                     = "tenant_id"
    attribute_data_type      = "String"
    mutable                  = true
    developer_only_attribute = false
    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }

  tags = var.tags

  lifecycle {
    prevent_destroy = false # TEARDOWN: re-enable before any redeploy
  }
}

resource "aws_cognito_user_pool_client" "this" {
  name         = "${var.project}-${var.environment}-web"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret = false # public SPA client; PKCE instead of a secret

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    # USER_PASSWORD_AUTH lets the API exchange credentials for tokens server-side,
    # so the browser never handles the SRP dance. The password crosses TLS to our
    # API and then to Cognito, and is never stored. SRP is stronger and is the
    # v1 hardening item; this is the pragmatic dev path.
    "ALLOW_USER_PASSWORD_AUTH",
    # Admin flow requires AWS credentials, so it is reachable only by our own
    # tooling — used by the seeding and RBAC verification scripts.
    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
  ]

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  prevent_user_existence_errors = "ENABLED" # do not disclose whether an account exists
  enable_token_revocation       = true
}

resource "aws_cognito_user_group" "roles" {
  for_each     = toset(["user", "admin"])
  name         = each.value
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "EKBA ${each.value} role"
}

output "user_pool_id" { value = aws_cognito_user_pool.this.id }
output "client_id" { value = aws_cognito_user_pool_client.this.id }
output "issuer" {
  value = "https://cognito-idp.${data.aws_region.current.name}.amazonaws.com/${aws_cognito_user_pool.this.id}"
}

data "aws_region" "current" {}
