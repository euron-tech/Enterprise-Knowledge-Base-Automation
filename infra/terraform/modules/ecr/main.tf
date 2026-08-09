/** Image registry with scan-on-push. prevent_destroy: images are release artifacts. */
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}

variable "project" { type = string }
variable "environment" { type = string }
variable "repositories" { type = list(string) }
variable "tags" { type = map(string) }

resource "aws_ecr_repository" "this" {
  for_each             = toset(var.repositories)
  name                 = "${var.project}/${each.value}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true # TEARDOWN

  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
  tags = var.tags

  lifecycle {
    prevent_destroy = false # TEARDOWN: re-enable before any redeploy
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 30 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

output "repository_urls" { value = { for k, v in aws_ecr_repository.this : k => v.repository_url } }
