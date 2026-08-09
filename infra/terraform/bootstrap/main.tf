/**
 * One-time bootstrap: Terraform remote state bucket + lock table.
 *
 * Run this once, with a local backend, before any environment can init.
 * Both resources carry prevent_destroy — losing state is losing the ability to
 * manage everything else safely.
 */

terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}

provider "aws" {
  region = var.region
  default_tags { tags = local.tags }
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

locals {
  tags = {
    Project     = "ekba"
    Environment = "shared"
    ManagedBy   = "terraform"
    Owner       = "platform"
  }
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "tfstate" {
  # Account-suffixed: S3 bucket names are globally unique.
  bucket = "ekba-tfstate-${data.aws_caller_identity.current.account_id}"
  tags   = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "tflock" {
  name         = "ekba-tflock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  attribute {
    name = "LockID"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

output "state_bucket" { value = aws_s3_bucket.tfstate.id }
output "lock_table" { value = aws_dynamodb_table.tflock.name }
