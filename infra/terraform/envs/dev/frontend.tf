/**
 * Frontend hosting: private S3 bucket + CloudFront.
 *
 * One public HTTPS origin for the whole product. CloudFront serves the SPA from
 * S3 and proxies the API paths to the ALB, so the browser makes only same-origin
 * requests: no CORS, no mixed content, and no ACM certificate or custom domain
 * needed to get a working URL.
 *
 * The bucket stays private — CloudFront reaches it through Origin Access Control,
 * never over a public website endpoint.
 */

variable "api_alb_dns" {
  type        = string
  description = "ALB hostname created by the Kubernetes Ingress"
}

resource "aws_s3_bucket" "site" {
  bucket = "ekba-dev-frontend-${data.aws_caller_identity.me.account_id}"
  tags   = local.tags
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "ekba-dev-site-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Security headers at the edge, matching what the API sets on its own responses.
resource "aws_cloudfront_response_headers_policy" "site" {
  name = "ekba-dev-security-headers"

  security_headers_config {
    content_type_options { override = true }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
  }
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "EKBA dev — SPA + API"
  price_class         = "PriceClass_100" # NA + EU edges; cheapest tier
  tags                = local.tags

  origin {
    origin_id                = "s3-site"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  origin {
    origin_id   = "alb-api"
    domain_name = var.api_alb_dns
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # TLS terminates at CloudFront
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # SPA assets.
  default_cache_behavior {
    target_origin_id       = "s3-site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # Managed-CachingOptimized
    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6"
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id
  }

  # API paths proxy to the ALB. Never cached — every response is per-user.
  dynamic "ordered_cache_behavior" {
    for_each = [
      "/chat", "/search", "/healthz", "/readyz", "/feedback",
      "/documents", "/documents/*", "/admin/*",
    ]
    content {
      path_pattern           = ordered_cache_behavior.value
      target_origin_id       = "alb-api"
      viewer_protocol_policy = "https-only"
      allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods         = ["GET", "HEAD"]
      compress               = true
      # Managed-CachingDisabled: authenticated, per-user responses must never
      # be cached at the edge.
      cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
      # Managed-AllViewerExceptHostHeader: forwards Authorization and the body,
      # but lets CloudFront send the ALB's own hostname as Host.
      origin_request_policy_id   = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
      response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id
    }
  }

  # A SPA owns its routing: unknown paths return index.html, not a 404.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# Only this distribution may read the bucket.
data "aws_iam_policy_document" "site_bucket" {
  statement {
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_bucket.json
}

output "frontend_url" {
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
  description = "The public URL for the product"
}

output "frontend_bucket" { value = aws_s3_bucket.site.id }
output "cloudfront_distribution_id" { value = aws_cloudfront_distribution.site.id }
