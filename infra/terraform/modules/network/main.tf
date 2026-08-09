/**
 * VPC for EKBA. Deliberately a NEW VPC — this account already has three, none of
 * them ours, and reusing one would entangle us with workloads we must not touch.
 *
 * Private subnets for everything stateful. VPC endpoints so S3/ECR/Secrets traffic
 * never leaves the AWS network (and cuts NAT cost).
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "project" { type = string }
variable "environment" { type = string }
variable "cidr_block" { type = string }
variable "az_count" {
  type    = number
  default = 2
}
variable "single_nat_gateway" {
  type        = bool
  default     = true
  description = "true in dev to save ~$32/month; false in prod for AZ resilience"
}
variable "tags" { type = map(string) }

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name      = "${var.project}-${var.environment}"
  azs       = slice(data.aws_availability_zones.available.names, 0, var.az_count)
  nat_count = var.single_nat_gateway ? 1 : var.az_count
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(var.tags, { Name = local.name })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${local.name}-igw" })
}

# Public subnets: load balancers and NAT only. No workloads.
resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.cidr_block, 4, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false # ELBs get addresses explicitly; nothing else needs one

  tags = merge(var.tags, {
    Name                     = "${local.name}-public-${local.azs[count.index]}"
    "kubernetes.io/role/elb" = "1"
  })
}

# Private subnets: nodes, pods, ElastiCache. No route to the internet except via NAT.
resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.cidr_block, 4, count.index + 8)
  availability_zone = local.azs[count.index]

  tags = merge(var.tags, {
    Name                              = "${local.name}-private-${local.azs[count.index]}"
    "kubernetes.io/role/internal-elb" = "1"
  })
}

resource "aws_eip" "nat" {
  count  = local.nat_count
  domain = "vpc"
  tags   = merge(var.tags, { Name = "${local.name}-nat-${count.index}" })
}

resource "aws_nat_gateway" "this" {
  count         = local.nat_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = merge(var.tags, { Name = "${local.name}-nat-${count.index}" })
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(var.tags, { Name = "${local.name}-public" })
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = var.az_count
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[var.single_nat_gateway ? 0 : count.index].id
  }
  tags = merge(var.tags, { Name = "${local.name}-private-${count.index}" })
}

resource "aws_route_table_association" "private" {
  count          = var.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ---------------------------------------------------------------- VPC endpoints
# Keeps S3/ECR/Secrets/CloudWatch traffic off the public internet and off the NAT bill.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id
  tags              = merge(var.tags, { Name = "${local.name}-s3" })
}

resource "aws_security_group" "endpoints" {
  name        = "${local.name}-vpce"
  description = "Interface VPC endpoints"
  vpc_id      = aws_vpc.this.id
  tags        = merge(var.tags, { Name = "${local.name}-vpce" })
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_https" {
  security_group_id = aws_security_group.endpoints.id
  cidr_ipv4         = var.cidr_block
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS from inside the VPC"
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset([
    "ecr.api", "ecr.dkr", "secretsmanager", "logs", "sts", "kms",
  ])

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  tags                = merge(var.tags, { Name = "${local.name}-${each.value}" })
}

data "aws_region" "current" {}

output "vpc_id" { value = aws_vpc.this.id }
output "vpc_cidr" { value = aws_vpc.this.cidr_block }
output "public_subnet_ids" { value = aws_subnet.public[*].id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "nat_public_ips" {
  value       = aws_eip.nat[*].public_ip
  description = "Add these to the Supabase IP allow-list"
}
