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
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-jobs-dlq"
  message_retention_seconds = 1209600
}
resource "aws_sqs_queue" "jobs" {
  name                       = "${var.name}-jobs"
  visibility_timeout_seconds = 900
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
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.media.arn}/*" }
  ] })
}
resource "aws_iam_instance_profile" "worker" {
  name = "${var.name}-worker"
  role = aws_iam_role.worker.name
}

resource "aws_launch_template" "gpu_worker" {
  name_prefix   = "${var.name}-gpu-"
  image_id      = data.aws_ami.ecs_gpu.id
  instance_type = var.worker_instance_type
  iam_instance_profile {
    name = aws_iam_instance_profile.worker.name
  }
  vpc_security_group_ids = [var.worker_security_group_id]
  user_data              = base64encode("#!/bin/bash\necho ECS_CLUSTER=${aws_ecs_cluster.workers.name} >> /etc/ecs/ecs.config\n")
}
resource "aws_autoscaling_group" "gpu_worker" {
  name                = "${var.name}-gpu-workers"
  min_size            = 0
  max_size            = 10
  desired_capacity    = 0
  vpc_zone_identifier = var.worker_subnet_ids
  launch_template {
    id      = aws_launch_template.gpu_worker.id
    version = "$Latest"
  }
  tag {
    key                 = "Name"
    value               = "${var.name}-gpu-worker"
    propagate_at_launch = true
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
  network_mode             = "awsvpc"
  cpu                      = "4096"
  memory                   = "15360"
  execution_role_arn       = aws_iam_role.worker.arn
  task_role_arn            = aws_iam_role.worker.arn
  container_definitions    = jsonencode([{ name = "worker", image = var.worker_image, essential = true, command = ["python", "-m", "app.worker"], resourceRequirements = [{ type = "GPU", value = "1" }], environment = [{ name = "SQS_QUEUE_URL", value = aws_sqs_queue.jobs.url }, { name = "S3_BUCKET", value = aws_s3_bucket.media.bucket }, { name = "STORAGE_BACKEND", value = "s3" }] }])
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
  network_configuration {
    subnets          = var.worker_subnet_ids
    security_groups  = [var.worker_security_group_id]
    assign_public_ip = true
  }
  depends_on = [aws_ecs_cluster_capacity_providers.workers]
}
resource "aws_appautoscaling_target" "worker" {
  max_capacity       = 10
  min_capacity       = 0
  resource_id        = "service/${aws_ecs_cluster.workers.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}
resource "aws_appautoscaling_policy" "worker_queue" {
  name               = "${var.name}-queue-depth"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  target_tracking_scaling_policy_configuration {
    target_value       = 1
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    customized_metric_specification {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      statistic   = "Average"
      unit        = "Count"
      dimensions {
        name  = "QueueName"
        value = aws_sqs_queue.jobs.name
      }
    }
  }
}

resource "aws_db_subnet_group" "postgres" {
  count      = var.enable_rds ? 1 : 0
  name       = "${var.name}-postgres"
  subnet_ids = var.database_subnet_ids
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
  vpc_security_group_ids    = [var.database_security_group_id]
  storage_encrypted         = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name}-final"
}
