/**
 * dev composition.
 *
 * This is a SHARED PRODUCTION ACCOUNT. Every resource here is tagged Project=ekba
 * and lives in its own new VPC. Nothing outside this configuration is referenced,
 * modified or destroyed.
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    tls    = { source = "hashicorp/tls", version = "~> 4.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }

  backend "s3" {
    bucket         = "ekba-tfstate-471112700629"
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

variable "github_repository" {
  type    = string
  default = "euron-tech/Enterprise-Knowledge-Base-Automation"
}

# Generated rather than supplied. Terraform has to know this value to configure the
# cluster, so a human-supplied one would end up in state regardless — generating it
# removes a credential from the hand-over list and guarantees it is strong.
# State lives in an encrypted, versioned, private bucket.
resource "random_password" "redis_auth" {
  length  = 48
  special = false # ElastiCache rejects several punctuation characters
}

variable "frontend_origin" {
  type    = string
  default = "http://localhost:5173"
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
    "qdrant-url",
    "langsmith-api-key",
  ]
}

module "network" {
  source      = "../../modules/network"
  project     = local.project
  environment = local.environment
  cidr_block  = "10.42.0.0/16" # deliberately distinct from the account's existing VPCs
  az_count    = 2
  # dev runs one NAT to save roughly $32/month; prod uses one per AZ
  single_nat_gateway = true
  tags               = local.tags
}

module "eks" {
  source              = "../../modules/eks"
  project             = local.project
  environment         = local.environment
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  public_subnet_ids   = module.network.public_subnet_ids
  kms_key_arn         = module.secrets.kms_key_arn
  node_instance_types = ["t3.medium"]
  node_desired_size   = 2
  node_min_size       = 1
  node_max_size       = 4
  tags                = local.tags
}

module "elasticache" {
  source                     = "../../modules/elasticache"
  project                    = local.project
  environment                = local.environment
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.eks.cluster_security_group_id]
  auth_token                 = random_password.redis_auth.result
  node_type                  = "cache.t4g.micro"
  tags                       = local.tags
}

module "cognito" {
  source        = "../../modules/cognito"
  project       = local.project
  environment   = local.environment
  callback_urls = ["${var.frontend_origin}/auth/callback"]
  logout_urls   = [var.frontend_origin]
  tags          = local.tags
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

module "iam" {
  source            = "../../modules/iam"
  project           = local.project
  environment       = local.environment
  github_repository = var.github_repository
  # This account already has a GitHub OIDC provider in use by other pipelines.
  create_oidc_provider = false
  tags                 = local.tags
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

# ---------------------------------------------------------------- outputs
output "cluster_name" { value = module.eks.cluster_name }
output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "buckets" { value = module.s3.bucket_names }
output "ecr_repositories" { value = module.ecr.repository_urls }
output "cognito_user_pool_id" { value = module.cognito.user_pool_id }
output "cognito_client_id" { value = module.cognito.client_id }
output "redis_primary_endpoint" { value = module.elasticache.primary_endpoint }
output "deploy_role_arn" {
  value       = module.iam.deploy_role_arn
  description = "Set as the AWS_DEPLOY_ROLE_ARN GitHub secret"
}
output "nat_public_ips" {
  value       = module.network.nat_public_ips
  description = "Add to the Supabase IP allow-list"
}
