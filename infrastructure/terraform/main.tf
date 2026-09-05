data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_ami" "ecs_gpu" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["amzn2-ami-ecs-gpu-hvm-*-x86_64-ebs"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_ecr_repository" "worker" {
  name = "${var.name}-worker"
}

data "aws_ecr_repository" "api" {
  name = "${var.name}-api"
}

resource "aws_s3_bucket" "media" {
  bucket        = "${var.name}-${data.aws_caller_identity.current.account_id}-media"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_cors_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  cors_rule {
    allowed_methods = ["GET", "HEAD", "PUT"]
    allowed_origins = [var.frontend_origin]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 900
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    id     = "expire-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
  rule {
    id     = "expire-generated-outputs"
    status = "Enabled"
    filter {
      tag {
        key   = "lingowave-category"
        value = "outputs"
      }
    }
    expiration {
      days = var.media_retention_days
    }
  }
  rule {
    id     = "expire-source-media"
    status = "Enabled"
    filter {
      tag {
        key   = "lingowave-category"
        value = "media"
      }
    }
    expiration {
      days = var.media_retention_days
    }
  }
  rule {
    id     = "expire-voice-references"
    status = "Enabled"
    filter {
      tag {
        key   = "lingowave-category"
        value = "voices"
      }
    }
    expiration {
      days = var.media_retention_days
    }
  }
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-jobs-dlq"
  message_retention_seconds = 1209600
}
resource "aws_sqs_queue" "jobs" {
  name                       = "${var.name}-jobs"
  visibility_timeout_seconds = var.sqs_visibility_timeout_seconds
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dlq.arn, maxReceiveCount = 3 })
}

resource "aws_ecs_cluster" "workers" {
  name = "${var.name}-workers"
}
resource "aws_iam_role" "worker" {
  name               = "${var.name}-worker"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "worker" {
  role = aws_iam_role.worker.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"], Resource = aws_sqs_queue.jobs.arn },
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.dlq.arn },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:PutObjectTagging", "s3:DeleteObject"], Resource = "${aws_s3_bucket.media.arn}/*" }
  ] })
}
resource "aws_iam_instance_profile" "worker" {
  name = "${var.name}-worker"
  role = aws_iam_role.worker.name
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${var.name}-ecs-execution"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  count = length(concat(values(var.worker_secrets), values(var.api_secrets))) > 0 ? 1 : 0
  role  = aws_iam_role.ecs_task_execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow"
    Action   = ["secretsmanager:GetSecretValue"]
    Resource = distinct(concat(values(var.worker_secrets), values(var.api_secrets)))
  }] })
}
resource "aws_iam_role" "ecs_task_worker" {
  name               = "${var.name}-ecs-task"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "ecs_task_worker" {
  role = aws_iam_role.ecs_task_worker.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility", "sqs:SendMessage"], Resource = aws_sqs_queue.jobs.arn },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:PutObjectTagging", "s3:DeleteObject"], Resource = "${aws_s3_bucket.media.arn}/*" }
  ], var.translation_provider == "aws-translate" ? [{ Effect = "Allow", Action = ["translate:TranslateText"], Resource = "*" }] : []) })
}

resource "aws_iam_role" "ecs_task_api" {
  count              = var.api_image != "" ? 1 : 0
  name               = "${var.name}-ecs-api-task"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy" "ecs_task_api" {
  count = var.api_image != "" ? 1 : 0
  role  = aws_iam_role.ecs_task_api[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:PutObjectTagging", "s3:DeleteObject"], Resource = "${aws_s3_bucket.media.arn}/*" },
    { Effect = "Allow", Action = ["sqs:SendMessage", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.jobs.arn }
  ], var.mail_provider == "ses" ? [{ Effect = "Allow", Action = ["ses:SendEmail"], Resource = var.ses_identity_arn != "" ? var.ses_identity_arn : "*" }] : []) })
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_actions_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository}:ref:refs/heads/*",
        "repo:${var.github_repository}:pull_request",
      ]
    }
  }
}

resource "aws_iam_role" "github_actions_ecr" {
  name               = "${var.name}-github-actions-ecr"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume_role.json
}

resource "aws_iam_role_policy" "github_actions_ecr" {
  role = aws_iam_role.github_actions_ecr.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:DescribeRepositories"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeImages",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = [
          data.aws_ecr_repository.worker.arn,
          data.aws_ecr_repository.api.arn,
        ]
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name}/worker"
  retention_in_days = 30
}

resource "aws_launch_template" "gpu_worker" {
  name_prefix   = "${var.name}-gpu-"
  image_id      = data.aws_ami.ecs_gpu.id
  instance_type = var.worker_instance_type
  iam_instance_profile {
    name = aws_iam_instance_profile.worker.name
  }
  vpc_security_group_ids = [local.effective_worker_security_group_id]
  user_data = base64encode(<<-EOT
    #!/bin/bash
    set -eu
    echo ECS_CLUSTER=${aws_ecs_cluster.workers.name} >> /etc/ecs/ecs.config
    # Reuse model downloads across ECS task replacements while this GPU host lives.
    install -d -m 1777 /var/lib/lingowave/model-cache
  EOT
  )
}
resource "aws_autoscaling_group" "gpu_worker" {
  name                = "${var.name}-gpu-workers"
  min_size            = 0
  max_size            = 10
  desired_capacity    = 0
  vpc_zone_identifier = local.effective_worker_subnet_ids
  launch_template {
    id      = aws_launch_template.gpu_worker.id
    version = "$Latest"
  }
  tag {
    key                 = "Name"
    value               = "${var.name}-gpu-worker"
    propagate_at_launch = true
  }
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = false
  }
  lifecycle {
    precondition {
      condition     = length(local.effective_worker_subnet_ids) > 0
      error_message = "Provide worker_subnet_ids when create_network is false."
    }
    precondition {
      condition     = local.effective_worker_security_group_id != ""
      error_message = "Provide worker_security_group_id when create_network is false."
    }
  }
}
resource "aws_ecs_capacity_provider" "gpu" {
  name = "${var.name}-gpu"
  auto_scaling_group_provider {
    auto_scaling_group_arn = aws_autoscaling_group.gpu_worker.arn
    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 2
    }
    managed_termination_protection = "DISABLED"
  }
}
resource "aws_ecs_cluster_capacity_providers" "workers" {
  cluster_name       = aws_ecs_cluster.workers.name
  capacity_providers = [aws_ecs_capacity_provider.gpu.name]
}
resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["EC2"]
  # GPU workers run on ECS/EC2 hosts. Host networking lets the task use the
  # host's public-subnet egress without an unsupported public IP on an EC2
  # task ENI. The worker exposes no inbound ports; the host security group
  # remains egress-only.
  network_mode       = "host"
  cpu                = "4096"
  memory             = "15360"
  execution_role_arn = aws_iam_role.ecs_task_execution.arn
  task_role_arn      = aws_iam_role.ecs_task_worker.arn
  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.worker_image
    essential = true
    command   = ["python", "-m", "app.worker"]
    resourceRequirements = [{
      type  = "GPU"
      value = "1"
    }]
    environment = [
      { name = "SQS_QUEUE_URL", value = aws_sqs_queue.jobs.url },
      { name = "SQS_VISIBILITY_TIMEOUT_SECONDS", value = tostring(var.sqs_visibility_timeout_seconds) },
      { name = "S3_BUCKET", value = aws_s3_bucket.media.bucket },
      { name = "STORAGE_BACKEND", value = "s3" },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "TRANSLATION_PROVIDER", value = var.translation_provider },
      { name = "TRANSLATION_MODEL", value = var.translation_model },
      { name = "TRANSLATION_MODEL_REVISION", value = var.translation_model_revision },
      { name = "WORKER_TYPE", value = "aws-gpu" },
      { name = "GPU_TYPE", value = var.worker_instance_type },
      { name = "GPU_HOURLY_PRICE_USD", value = tostring(var.worker_hourly_price_usd) },
      { name = "XDG_CACHE_HOME", value = "/home/lingowave/.cache" },
    ]
    secrets = [for name, value_from in var.worker_secrets : { name = name, valueFrom = value_from }]
    mountPoints = [{
      sourceVolume  = "model-cache"
      containerPath = "/home/lingowave/.cache"
      readOnly      = false
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])
  volume {
    name      = "model-cache"
    host_path = "/var/lib/lingowave/model-cache"
  }
}
resource "aws_ecs_service" "worker" {
  name            = "${var.name}-worker"
  cluster         = aws_ecs_cluster.workers.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.gpu.name
    weight            = 1
  }
  depends_on = [aws_ecs_cluster_capacity_providers.workers]
}

resource "aws_ecs_task_definition" "cpu_worker" {
  count                    = var.cpu_worker_image != "" ? 1 : 0
  family                   = "${var.name}-cpu-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cpu_worker_cpu)
  memory                   = tostring(var.cpu_worker_memory)
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task_worker.arn
  container_definitions = jsonencode([{
    name      = "cpu-worker"
    image     = var.cpu_worker_image
    essential = true
    command   = ["sh", "-c", "mkdir -p /tmp/lingowave-home /tmp/lingowave-cache && exec python -m app.worker"]
    environment = [
      { name = "SQS_QUEUE_URL", value = aws_sqs_queue.jobs.url },
      { name = "SQS_VISIBILITY_TIMEOUT_SECONDS", value = tostring(var.sqs_visibility_timeout_seconds) },
      { name = "S3_BUCKET", value = aws_s3_bucket.media.bucket },
      { name = "STORAGE_BACKEND", value = "s3" },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "TRANSLATION_PROVIDER", value = var.translation_provider },
      { name = "TRANSLATION_MODEL", value = var.translation_model },
      { name = "TRANSLATION_MODEL_REVISION", value = var.translation_model_revision },
      { name = "WORKER_TYPE", value = "aws-cpu" },
      { name = "CPU_TYPE", value = "fargate-${var.cpu_worker_cpu}-${var.cpu_worker_memory}" },
      { name = "COMPUTE_HOURLY_PRICE_USD", value = tostring(var.cpu_worker_hourly_price_usd) },
      { name = "CHATTERBOX_DEVICE", value = "cpu" },
      { name = "HOME", value = "/tmp/lingowave-home" },
      { name = "XDG_CACHE_HOME", value = "/tmp/lingowave-cache" },
      { name = "TORCH_HOME", value = "/tmp/lingowave-cache/torch" },
      { name = "HF_HOME", value = "/tmp/lingowave-cache/huggingface" },
    ]
    secrets = [for name, value_from in var.worker_secrets : { name = name, valueFrom = value_from }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "cpu-worker"
      }
    }
  }])
}

resource "aws_ecs_service" "cpu_worker" {
  count           = var.cpu_worker_image != "" ? 1 : 0
  name            = "${var.name}-cpu-worker"
  cluster         = aws_ecs_cluster.workers.id
  task_definition = aws_ecs_task_definition.cpu_worker[0].arn
  desired_count   = var.cpu_worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.effective_worker_subnet_ids
    security_groups  = [local.effective_worker_security_group_id]
    assign_public_ip = true
  }
}

resource "aws_cloudwatch_log_group" "api" {
  count             = var.api_image != "" ? 1 : 0
  name              = "/ecs/${var.name}/api"
  retention_in_days = 30
}

resource "aws_ecs_cluster" "api" {
  count = var.api_image != "" ? 1 : 0
  name  = "${var.name}-api"
}

resource "aws_lb" "api" {
  count              = var.api_image != "" ? 1 : 0
  name               = substr("${var.name}-api", 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [local.effective_load_balancer_security_group_id]
  subnets            = local.effective_api_subnet_ids
}

resource "aws_lb_target_group" "api" {
  count       = var.api_image != "" ? 1 : 0
  name        = substr("${var.name}-api", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = local.effective_vpc_id

  health_check {
    enabled             = true
    path                = "/health"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "api" {
  count             = var.api_image != "" ? 1 : 0
  load_balancer_arn = aws_lb.api[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = var.api_certificate_arn != "" ? "redirect" : "forward"
    dynamic "redirect" {
      for_each = var.api_certificate_arn != "" ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
    dynamic "forward" {
      for_each = var.api_certificate_arn == "" ? [1] : []
      content {
        target_group {
          arn = aws_lb_target_group.api[0].arn
        }
      }
    }
  }
}

resource "aws_lb_listener" "api_tls" {
  count             = var.api_image != "" && var.api_certificate_arn != "" ? 1 : 0
  load_balancer_arn = aws_lb.api[0].arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.api_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[0].arn
  }
}

resource "aws_ecs_task_definition" "api" {
  count                    = var.api_image != "" ? 1 : 0
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task_api[0].arn
  container_definitions    = jsonencode([{ name = "api", image = var.api_image, essential = true, command = ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"], portMappings = [{ containerPort = 8000, hostPort = 8000, protocol = "tcp" }], environment = [{ name = "S3_BUCKET", value = aws_s3_bucket.media.bucket }, { name = "STORAGE_BACKEND", value = "s3" }, { name = "AWS_REGION", value = var.aws_region }, { name = "S3_PRESIGN_ENDPOINT_URL", value = "https://s3.${var.aws_region}.amazonaws.com" }, { name = "SQS_QUEUE_URL", value = aws_sqs_queue.jobs.url }, { name = "FRONTEND_ORIGIN", value = var.frontend_origin }, { name = "COOKIE_SECURE", value = "true" }, { name = "SQS_VISIBILITY_TIMEOUT_SECONDS", value = tostring(var.sqs_visibility_timeout_seconds) }, { name = "MAIL_PROVIDER", value = var.mail_provider }, { name = "MAIL_FROM", value = var.mail_from }, { name = "ALLOW_UNMEASURED_PRICING", value = tostring(var.allow_unmeasured_pricing) }], secrets = [for name, value_from in var.api_secrets : { name = name, valueFrom = value_from }], logConfiguration = { logDriver = "awslogs", options = { "awslogs-group" = aws_cloudwatch_log_group.api[0].name, "awslogs-region" = var.aws_region, "awslogs-stream-prefix" = "api" } } }])

  lifecycle {
    precondition {
      condition     = contains(keys(var.api_secrets), "DATABASE_URL")
      error_message = "api_secrets must include DATABASE_URL when api_image is set."
    }
    precondition {
      condition     = var.api_certificate_arn != "" || var.frontend_enabled
      error_message = "Provide api_certificate_arn or enable the shared CloudFront HTTPS frontend distribution when api_image is set."
    }
    precondition {
      condition     = length(local.effective_api_subnet_ids) >= 2
      error_message = "The API service and load balancer require at least two API subnets."
    }
    precondition {
      condition     = local.effective_api_security_group_id != "" && local.effective_load_balancer_security_group_id != ""
      error_message = "API and load balancer security groups must be supplied or created by Terraform."
    }
    precondition {
      condition     = var.mail_provider != "ses" || var.ses_identity_arn != ""
      error_message = "ses_identity_arn is required when mail_provider is ses."
    }
  }
}

resource "aws_ecs_service" "api" {
  count           = var.api_image != "" ? 1 : 0
  name            = "${var.name}-api"
  cluster         = aws_ecs_cluster.api[0].id
  task_definition = aws_ecs_task_definition.api[0].arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.effective_api_subnet_ids
    security_groups  = [local.effective_api_security_group_id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api[0].arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.api, aws_lb_listener.api_tls]
}
resource "aws_appautoscaling_target" "worker" {
  max_capacity       = 10
  min_capacity       = 0
  resource_id        = "service/${aws_ecs_cluster.workers.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}
resource "aws_appautoscaling_policy" "worker_scale_out" {
  name               = "${var.name}-queue-scale-out"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 4
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 4
      scaling_adjustment          = 2
    }
  }
}

resource "aws_appautoscaling_policy" "worker_scale_in" {
  name               = "${var.name}-queue-scale-in"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 300
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = -10
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_queue_nonempty" {
  alarm_name          = "${var.name}-queue-nonempty"
  alarm_description   = "Scale ECS worker tasks out when jobs are visible in SQS."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.worker_scale_out.arn]
  dimensions = {
    QueueName = aws_sqs_queue.jobs.name
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_queue_empty" {
  alarm_name          = "${var.name}-queue-empty"
  alarm_description   = "Scale ECS worker tasks in after visible and in-flight queue messages have been absent."
  evaluation_periods  = 15
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.worker_scale_in.arn]

  metric_query {
    id          = "active"
    expression  = "visible + inflight"
    label       = "Visible and in-flight SQS messages"
    return_data = true
  }

  metric_query {
    id          = "visible"
    return_data = false

    metric {
      metric_name = "ApproximateNumberOfMessagesVisible"
      namespace   = "AWS/SQS"
      period      = 60
      stat        = "Maximum"
      dimensions = {
        QueueName = aws_sqs_queue.jobs.name
      }
    }
  }

  metric_query {
    id          = "inflight"
    return_data = false

    metric {
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      namespace   = "AWS/SQS"
      period      = 60
      stat        = "Maximum"
      dimensions = {
        QueueName = aws_sqs_queue.jobs.name
      }
    }
  }
}

resource "aws_db_subnet_group" "postgres" {
  count      = var.enable_rds ? 1 : 0
  name       = "${var.name}-postgres"
  subnet_ids = local.effective_database_subnet_ids
  lifecycle {
    precondition {
      condition     = length(local.effective_database_subnet_ids) > 0
      error_message = "Provide database_subnet_ids when RDS is enabled and create_network is false."
    }
  }
}
resource "aws_db_instance" "postgres" {
  count                     = var.enable_rds ? 1 : 0
  identifier                = var.name
  engine                    = "postgres"
  engine_version            = "16"
  instance_class            = "db.t4g.micro"
  allocated_storage         = 30
  db_name                   = var.database_name
  username                  = var.database_username
  password                  = var.database_password
  db_subnet_group_name      = aws_db_subnet_group.postgres[0].name
  vpc_security_group_ids    = [local.effective_database_security_group_id]
  storage_encrypted         = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name}-final"
  lifecycle {
    # The generated password is stored in Secrets Manager and should not be
    # rotated implicitly by routine Terraform plans.
    ignore_changes = [password]
    precondition {
      condition     = local.effective_database_security_group_id != ""
      error_message = "Provide database_security_group_id when RDS is enabled and create_network is false."
    }
  }
}
