/** dev composition. Every resource carries Project=ekba. */
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }

  # Remote state. Create the bucket + lock table once, out of band.
  backend "s3" {
    bucket         = "ekba-tfstate"
    key            = "dev/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "ekba-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = local.tags
  }
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

locals {
  project     = "ekba"
  environment = "dev"
  tags = {
    Project     = "ekba" # the tag every destructive-action rule keys off
    Environment = "dev"
    ManagedBy   = "terraform"
    Owner       = "platform"
  }
}

resource "aws_sns_topic" "alarms" {
  name = "${local.project}-${local.environment}-alarms"
  tags = local.tags
}

module "secrets" {
  source      = "../../modules/secrets"
  project     = local.project
  environment = local.environment
  tags        = local.tags
  secret_names = [
    "euri-api-key",
    "database-url",
    "redis-url",
    "qdrant-api-key",
    "cognito-client-secret",
    "langsmith-api-key",
  ]
}

module "s3" {
  source      = "../../modules/s3"
  project     = local.project
  environment = local.environment
  kms_key_arn = module.secrets.kms_key_arn
  tags        = local.tags
}

module "ecr" {
  source       = "../../modules/ecr"
  project      = local.project
  environment  = local.environment
  repositories = ["api", "ingest-worker"]
  tags         = local.tags
}

module "observability" {
  source          = "../../modules/observability"
  project         = local.project
  environment     = local.environment
  kms_key_arn     = module.secrets.kms_key_arn
  retention_days  = 30
  alarm_topic_arn = aws_sns_topic.alarms.arn
  tags            = local.tags
}

output "buckets" { value = module.s3.bucket_names }
output "ecr" { value = module.ecr.repository_urls }
output "secret_arns" { value = module.secrets.secret_arns }
