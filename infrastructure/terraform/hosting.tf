resource "aws_s3_bucket" "frontend" {
  count         = var.frontend_enabled ? 1 : 0
  bucket        = "${var.name}-${data.aws_caller_identity.current.account_id}-frontend"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  count                   = var.frontend_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.frontend[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  count  = var.frontend_enabled ? 1 : 0
  bucket = aws_s3_bucket.frontend[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  count                             = var.frontend_enabled ? 1 : 0
  name                              = "${var.name}-frontend-oac"
  description                       = "Origin access for the private LingoWave frontend bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_cache_policy" "frontend" {
  count       = var.frontend_enabled ? 1 : 0
  name        = "${var.name}-frontend-cache"
  min_ttl     = 0
  default_ttl = 86400
  max_ttl     = 31536000
  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_cache_policy" "api" {
  count       = var.frontend_enabled && var.api_image != "" ? 1 : 0
  name        = "${var.name}-api-no-cache"
  min_ttl     = 0
  default_ttl = 0
  max_ttl     = 0
  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_origin_request_policy" "api" {
  count   = var.frontend_enabled && var.api_image != "" ? 1 : 0
  name    = "${var.name}-api-origin"
  comment = "Forward API cookies, query strings, and viewer headers"
  cookies_config {
    cookie_behavior = "all"
  }
  headers_config {
    header_behavior = "allViewer"
  }
  query_strings_config {
    query_string_behavior = "all"
  }
}

resource "aws_cloudfront_distribution" "app" {
  count   = var.frontend_enabled ? 1 : 0
  enabled = true
  comment = "${var.name} frontend and API edge"

  origin {
    domain_name              = aws_s3_bucket.frontend[0].bucket_regional_domain_name
    origin_id                = "frontend-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend[0].id
  }

  dynamic "origin" {
    for_each = var.api_image != "" ? [1] : []
    content {
      domain_name = aws_lb.api[0].dns_name
      origin_id   = "api-alb"
      custom_origin_config {
        http_port              = 80
        https_port             = 443
        origin_protocol_policy = var.api_certificate_arn != "" ? "https-only" : "http-only"
        origin_ssl_protocols   = ["TLSv1.2"]
      }
    }
  }

  default_root_object = "index.html"

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "frontend-s3"
    viewer_protocol_policy = "redirect-to-https"
    cache_policy_id        = aws_cloudfront_cache_policy.frontend[0].id
    compress               = true
  }

  dynamic "ordered_cache_behavior" {
    for_each = var.api_image != "" ? toset(["api/*", "v1/*", "health"]) : toset([])
    content {
      path_pattern             = ordered_cache_behavior.value
      allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
      cached_methods           = ["GET", "HEAD"]
      target_origin_id         = "api-alb"
      viewer_protocol_policy   = "redirect-to-https"
      cache_policy_id          = aws_cloudfront_cache_policy.api[0].id
      origin_request_policy_id = aws_cloudfront_origin_request_policy.api[0].id
      compress                 = false
    }
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "frontend_bucket" {
  count = var.frontend_enabled ? 1 : 0
  statement {
    sid       = "AllowCloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend[0].arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.app[0].arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  count  = var.frontend_enabled ? 1 : 0
  bucket = aws_s3_bucket.frontend[0].id
  policy = data.aws_iam_policy_document.frontend_bucket[0].json
}

resource "aws_s3_object" "frontend" {
  for_each = var.frontend_enabled && var.frontend_dist_dir != "" ? fileset(var.frontend_dist_dir, "**") : toset([])
  bucket   = aws_s3_bucket.frontend[0].id
  key      = each.value
  source   = "${var.frontend_dist_dir}/${each.value}"
  etag     = filemd5("${var.frontend_dist_dir}/${each.value}")

  content_type  = endswith(each.value, ".html") ? "text/html" : endswith(each.value, ".css") ? "text/css" : endswith(each.value, ".js") ? "application/javascript" : endswith(each.value, ".png") ? "image/png" : "application/octet-stream"
  cache_control = endswith(each.value, ".html") ? "no-cache" : "public,max-age=31536000,immutable"
}
