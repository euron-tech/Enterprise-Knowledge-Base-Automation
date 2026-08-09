/** Redis in private subnets. No public endpoint, TLS + AUTH, encrypted at rest. */
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}

variable "project" { type = string }
variable "environment" { type = string }
variable "subnet_ids" { type = list(string) }
variable "vpc_id" { type = string }
variable "allowed_security_group_ids" { type = list(string) }
variable "auth_token" {
  type      = string
  sensitive = true
}
variable "node_type" {
  type    = string
  default = "cache.t4g.micro"
}
variable "tags" { type = map(string) }

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.project}-${var.environment}-redis"
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "redis" {
  name        = "${var.project}-${var.environment}-redis"
  description = "Redis access from application pods only"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

# Reference the caller's security group rather than a CIDR.
# count, not for_each: the security group ids are only known after apply, and
# for_each keys must be resolvable at plan time.
resource "aws_vpc_security_group_ingress_rule" "from_app" {
  count                        = length(var.allowed_security_group_ids)
  security_group_id            = aws_security_group.redis.id
  referenced_security_group_id = var.allowed_security_group_ids[count.index]
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  description                  = "Redis from application pods"
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.project}-${var.environment}-redis"
  description          = "EKBA cache and rate limiting"

  engine             = "redis"
  engine_version     = "7.1"
  node_type          = var.node_type
  num_cache_clusters = var.environment == "prod" ? 2 : 1
  port               = 6379

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.redis.id]

  # No public endpoint is possible: private subnets only.
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  auth_token                 = var.auth_token

  automatic_failover_enabled = var.environment == "prod"
  multi_az_enabled           = var.environment == "prod"
  snapshot_retention_limit   = var.environment == "prod" ? 7 : 1
  apply_immediately          = var.environment != "prod"

  tags = var.tags
}

output "primary_endpoint" { value = aws_elasticache_replication_group.this.primary_endpoint_address }
output "security_group_id" { value = aws_security_group.redis.id }
