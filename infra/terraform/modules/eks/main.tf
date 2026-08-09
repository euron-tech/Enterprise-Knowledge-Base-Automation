/**
 * EKS cluster with IRSA, private nodes, encrypted secrets and audit logging.
 * prevent_destroy on the cluster — deleting it takes every workload with it.
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
    tls = { source = "hashicorp/tls", version = "~> 4.0" }
  }
}

variable "project" { type = string }
variable "environment" { type = string }
variable "kubernetes_version" {
  type    = string
  default = "1.31"
}
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "public_subnet_ids" { type = list(string) }
variable "kms_key_arn" { type = string }
variable "node_instance_types" {
  type    = list(string)
  default = ["t3.medium"]
}
variable "node_desired_size" {
  type    = number
  default = 2
}
variable "node_min_size" {
  type    = number
  default = 1
}
variable "node_max_size" {
  type    = number
  default = 4
}
variable "public_endpoint_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach the API server. Narrow this in prod."
  default     = ["0.0.0.0/0"]
}
variable "tags" { type = map(string) }

locals {
  name = "${var.project}-${var.environment}"
}

# ---------------------------------------------------------------- cluster role
data "aws_iam_policy_document" "cluster_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${local.name}-cluster"
  assume_role_policy = data.aws_iam_policy_document.cluster_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "cluster" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
  ])
  role       = aws_iam_role.cluster.name
  policy_arn = each.value
}

resource "aws_security_group" "cluster" {
  name        = "${local.name}-cluster"
  description = "EKS control plane"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${local.name}-cluster" })
}

resource "aws_vpc_security_group_egress_rule" "cluster_all" {
  security_group_id = aws_security_group.cluster.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "control plane egress"
}

resource "aws_cloudwatch_log_group" "cluster" {
  name              = "/aws/eks/${local.name}/cluster"
  retention_in_days = var.environment == "prod" ? 400 : 30
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

resource "aws_eks_cluster" "this" {
  name     = "${local.name}-cluster"
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = concat(var.private_subnet_ids, var.public_subnet_ids)
    security_group_ids      = [aws_security_group.cluster.id]
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.public_endpoint_cidrs
  }

  # Kubernetes Secrets encrypted with our own CMK, not just AWS-managed.
  encryption_config {
    provider {
      key_arn = var.kms_key_arn
    }
    resources = ["secrets"]
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  access_config {
    authentication_mode                         = "API_AND_CONFIG_MAP"
    bootstrap_cluster_creator_admin_permissions = true
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.cluster,
    aws_cloudwatch_log_group.cluster,
  ]

  lifecycle {
    prevent_destroy = false # TEARDOWN: re-enable before any redeploy
  }
}

# ---------------------------------------------------------------- IRSA
data "tls_certificate" "oidc" {
  url = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "oidc" {
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.oidc.certificates[0].sha1_fingerprint]
  tags            = var.tags
}

# ---------------------------------------------------------------- node group
data "aws_iam_policy_document" "node_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${local.name}-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${local.name}-ng"
  node_role_arn   = aws_iam_role.node.arn
  # Private subnets only — nodes are never directly reachable from the internet.
  subnet_ids = var.private_subnet_ids

  instance_types = var.node_instance_types
  capacity_type  = "ON_DEMAND"
  disk_size      = 50

  scaling_config {
    desired_size = var.node_desired_size
    min_size     = var.node_min_size
    max_size     = var.node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy_attachment.node]

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size] # HPA/karpenter owns this
  }
}

# ---------------------------------------------------------------- add-ons
resource "aws_eks_addon" "this" {
  for_each = {
    "vpc-cni"                = null
    "coredns"                = null
    "kube-proxy"             = null
    "eks-pod-identity-agent" = null
  }
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = each.key
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = var.tags
  depends_on                  = [aws_eks_node_group.this]
}

output "cluster_name" { value = aws_eks_cluster.this.name }
output "cluster_endpoint" { value = aws_eks_cluster.this.endpoint }
output "oidc_provider_arn" { value = aws_iam_openid_connect_provider.oidc.arn }
output "oidc_provider_url" { value = replace(aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "") }
output "cluster_security_group_id" { value = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id }
output "node_role_arn" { value = aws_iam_role.node.arn }
