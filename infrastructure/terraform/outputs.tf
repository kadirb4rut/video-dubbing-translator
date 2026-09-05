output "media_bucket_name" {
  value = aws_s3_bucket.media.bucket
}

output "jobs_queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "jobs_dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "worker_cluster_name" {
  value = aws_ecs_cluster.workers.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "cpu_worker_service_name" {
  value = var.cpu_worker_image != "" ? aws_ecs_service.cpu_worker[0].name : null
}

output "vpc_id" {
  value = local.effective_vpc_id
}

output "worker_subnet_ids" {
  value = local.effective_worker_subnet_ids
}

output "worker_security_group_id" {
  value = local.effective_worker_security_group_id
}

output "api_url" {
  value = var.api_image != "" ? (var.api_certificate_arn != "" ? "https://${aws_lb.api[0].dns_name}" : "https://${aws_cloudfront_distribution.app[0].domain_name}") : null
}

output "frontend_url" {
  value = var.frontend_enabled ? "https://${aws_cloudfront_distribution.app[0].domain_name}" : null
}

output "api_cluster_name" {
  value = var.api_image != "" ? aws_ecs_cluster.api[0].name : null
}

output "api_service_name" {
  value = var.api_image != "" ? aws_ecs_service.api[0].name : null
}

output "github_actions_ecr_role_arn" {
  value = aws_iam_role.github_actions_ecr.arn
}
