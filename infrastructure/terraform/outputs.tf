output "media_bucket" { value = aws_s3_bucket.media.bucket }
output "jobs_queue_url" { value = aws_sqs_queue.jobs.url }
output "worker_cluster" { value = aws_ecs_cluster.workers.name }
output "postgres_endpoint" { value = var.enable_rds ? aws_db_instance.postgres[0].address : null }
