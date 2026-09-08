locals {
  serverless_mode        = var.deployment_mode == "serverless"
  serverless_api_enabled = local.serverless_mode && var.lambda_api_image != ""
  serverless_db_enabled  = local.serverless_mode && var.enable_aurora_serverless
  api_enabled            = var.api_image != "" || local.serverless_api_enabled
  api_origin_domain      = local.serverless_api_enabled ? replace(replace(aws_apigatewayv2_api.serverless[0].api_endpoint, "https://", ""), "/", "") : var.api_image != "" ? aws_lb.api[0].dns_name : ""
  ssm_parameter_arns     = distinct(concat(var.ssm_parameter_arns, [for name in values(var.ssm_parameter_map) : startswith(name, "arn:") ? name : format("arn:aws:ssm:%s:%s:parameter%s", var.aws_region, data.aws_caller_identity.current.account_id, startswith(name, "/") ? name : format("/%s", name))]))
}

resource "terraform_data" "deployment_mode_guard" {
  input = {
    deployment_mode = var.deployment_mode
    api_image       = var.api_image
    lambda_image    = var.lambda_api_image
    legacy_rds      = local.legacy_rds_enabled
    aurora_enabled  = var.enable_aurora_serverless
  }

  lifecycle {
    precondition {
      condition     = var.deployment_mode != "serverless" || var.lambda_api_image != ""
      error_message = "deployment_mode=serverless requires lambda_api_image to be an immutable ECR image URI or digest."
    }
    precondition {
      condition     = var.deployment_mode != "serverless" || var.api_image == "" || var.retain_legacy_api
      error_message = "deployment_mode=serverless may keep the legacy ALB/ECS API only with retain_legacy_api=true during a verified migration window."
    }
    precondition {
      condition     = !var.retain_legacy_api || var.api_image != ""
      error_message = "retain_legacy_api=true requires the existing immutable api_image so rollback remains deployable."
    }
    precondition {
      condition     = var.deployment_mode != "serverless" || !var.enable_rds || var.retain_legacy_rds
      error_message = "deployment_mode=serverless may keep legacy RDS only with retain_legacy_rds=true during a verified migration window."
    }
    precondition {
      condition     = var.deployment_mode != "serverless" || var.enable_aurora_serverless
      error_message = "deployment_mode=serverless requires enable_aurora_serverless=true for the PostgreSQL control plane."
    }
  }
}

resource "aws_iam_role" "serverless_api" {
  count              = local.serverless_api_enabled ? 1 : 0
  name               = "${var.name}-serverless-api"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy_attachment" "serverless_api_logs" {
  count      = local.serverless_api_enabled ? 1 : 0
  role       = aws_iam_role.serverless_api[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "serverless_api" {
  count = local.serverless_api_enabled ? 1 : 0
  role  = aws_iam_role.serverless_api[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:PutObjectTagging", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.media.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        Effect   = "Allow"
        Action   = ["rds-data:BatchExecuteStatement", "rds-data:BeginTransaction", "rds-data:CommitTransaction", "rds-data:ExecuteStatement", "rds-data:RollbackTransaction"]
        Resource = local.serverless_db_enabled ? aws_rds_cluster.aurora[0].arn : "*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = local.serverless_db_enabled ? aws_rds_cluster.aurora[0].master_user_secret[0].secret_arn : "*"
      },
      ], length(local.ssm_parameter_arns) > 0 ? [{
        Effect   = "Allow"
        Action   = ["ssm:GetParameters"]
        Resource = local.ssm_parameter_arns
        }] : [], var.mail_provider == "ses" ? [{
        Effect   = "Allow"
        Action   = ["ses:SendEmail"]
        Resource = var.ses_identity_arn != "" ? var.ses_identity_arn : "*"
    }] : [])
  })
}

resource "aws_rds_cluster" "aurora" {
  count                       = local.serverless_db_enabled ? 1 : 0
  cluster_identifier          = "${var.name}-aurora"
  engine                      = "aurora-postgresql"
  engine_version              = var.aurora_engine_version
  database_name               = var.database_name
  master_username             = var.database_username
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.aurora[0].name
  vpc_security_group_ids      = [local.effective_database_security_group_id]
  storage_encrypted           = true
  backup_retention_period     = 7
  copy_tags_to_snapshot       = true
  enable_http_endpoint        = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.name}-aurora-final"
  deletion_protection         = false

  serverlessv2_scaling_configuration {
    min_capacity             = 0
    max_capacity             = var.aurora_max_acu
    seconds_until_auto_pause = var.aurora_auto_pause_seconds
  }

  lifecycle {
    precondition {
      condition     = local.effective_database_security_group_id != ""
      error_message = "Aurora requires a database security group."
    }
    precondition {
      condition     = length(local.effective_database_subnet_ids) >= 2
      error_message = "Aurora requires at least two database subnets."
    }
  }
}

resource "aws_db_subnet_group" "aurora" {
  count      = local.serverless_db_enabled ? 1 : 0
  name       = "${var.name}-aurora"
  subnet_ids = local.effective_database_subnet_ids
}

resource "aws_rds_cluster_instance" "aurora" {
  count              = local.serverless_db_enabled ? 1 : 0
  identifier         = "${var.name}-aurora-1"
  cluster_identifier = aws_rds_cluster.aurora[0].id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.aurora[0].engine
  engine_version     = aws_rds_cluster.aurora[0].engine_version
}

resource "aws_lambda_function" "serverless_api" {
  count         = local.serverless_api_enabled ? 1 : 0
  function_name = "${var.name}-api"
  role          = aws_iam_role.serverless_api[0].arn
  package_type  = "Image"
  image_uri     = var.lambda_api_image
  timeout       = 30
  memory_size   = 1024
  architectures = ["x86_64"]

  environment {
    variables = {
      DATABASE_URL                   = "postgresql+auroradataapi://:@/${var.database_name}"
      AURORA_CLUSTER_ARN             = local.serverless_db_enabled ? aws_rds_cluster.aurora[0].arn : ""
      AURORA_SECRET_ARN              = local.serverless_db_enabled ? aws_rds_cluster.aurora[0].master_user_secret[0].secret_arn : ""
      DB_POOL_MODE                   = "null"
      MEDIA_INSPECTION_MODE          = "presigned-url"
      STORAGE_BACKEND                = "s3"
      S3_BUCKET                      = aws_s3_bucket.media.bucket
      S3_PRESIGN_ENDPOINT_URL        = "https://s3.${var.aws_region}.amazonaws.com"
      SQS_QUEUE_URL                  = aws_sqs_queue.jobs.url
      SQS_VISIBILITY_TIMEOUT_SECONDS = tostring(var.sqs_visibility_timeout_seconds)
      AWS_REGION                     = var.aws_region
      FRONTEND_ORIGIN                = var.frontend_origin
      COOKIE_SECURE                  = "true"
      MAIL_PROVIDER                  = var.mail_provider
      MAIL_FROM                      = var.mail_from
      ALLOW_UNMEASURED_PRICING       = tostring(var.allow_unmeasured_pricing)
      SSM_PARAMETER_MAP              = jsonencode(var.ssm_parameter_map)
    }
  }

  depends_on = [aws_iam_role_policy_attachment.serverless_api_logs]
}

resource "aws_lambda_function" "serverless_migration" {
  count         = local.serverless_api_enabled && local.serverless_db_enabled ? 1 : 0
  function_name = "${var.name}-migration"
  role          = aws_iam_role.serverless_api[0].arn
  package_type  = "Image"
  image_uri     = var.lambda_api_image
  timeout       = 900
  memory_size   = 1024
  architectures = ["x86_64"]

  image_config {
    command = ["app.migration_handler.handler"]
  }

  environment {
    variables = {
      DATABASE_URL          = "postgresql+auroradataapi://:@/${var.database_name}"
      AURORA_CLUSTER_ARN    = aws_rds_cluster.aurora[0].arn
      AURORA_SECRET_ARN     = aws_rds_cluster.aurora[0].master_user_secret[0].secret_arn
      DB_POOL_MODE          = "null"
      MEDIA_INSPECTION_MODE = "presigned-url"
      AWS_REGION            = var.aws_region
      SSM_PARAMETER_MAP     = jsonencode(var.ssm_parameter_map)
    }
  }

  depends_on = [aws_iam_role_policy_attachment.serverless_api_logs]
}

resource "aws_apigatewayv2_api" "serverless" {
  count         = local.serverless_api_enabled ? 1 : 0
  name          = "${var.name}-http"
  protocol_type = "HTTP"

  cors_configuration {
    allow_credentials = true
    allow_headers     = ["*"]
    allow_methods     = ["*"]
    allow_origins     = [var.frontend_origin]
    expose_headers    = ["location"]
  }
}

resource "aws_apigatewayv2_integration" "serverless" {
  count                  = local.serverless_api_enabled ? 1 : 0
  api_id                 = aws_apigatewayv2_api.serverless[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.serverless_api[0].invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "serverless" {
  count     = local.serverless_api_enabled ? 1 : 0
  api_id    = aws_apigatewayv2_api.serverless[0].id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.serverless[0].id}"
}

resource "aws_apigatewayv2_stage" "serverless" {
  count       = local.serverless_api_enabled ? 1 : 0
  api_id      = aws_apigatewayv2_api.serverless[0].id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "serverless_api_gateway" {
  count         = local.serverless_api_enabled ? 1 : 0
  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.serverless_api[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.serverless[0].execution_arn}/*/*"
}
