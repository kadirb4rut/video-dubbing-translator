# LingoWave infrastructure direction

The first production deployment is intentionally queue-first and scale-to-zero:

```text
React/Vite web → FastAPI API → PostgreSQL (ledger + jobs)
                       ├── S3 private objects (signed upload/download URLs)
                       └── SQS → GPU worker capacity (ECS/EC2 GPU, desired count 0 when idle)
```

The worker image is split from the API image. Queue depth and oldest-message age drive the worker autoscaling policy. When the queue is empty, the desired GPU capacity returns to zero. ECS tasks mount `/var/lib/lingowave/model-cache` at `/home/lingowave/.cache`, so model downloads are reused across task replacements while a GPU host remains alive; scale-to-zero intentionally discards that host-local cache, and cold-start time must remain visible in benchmarks.

Staging and production use separate Terraform variable files and separate S3
state keys under `terraform/environments/`. Initialize one explicitly with
`terraform init -reconfigure -backend-config=environments/backend-<environment>.hcl`
and apply with the matching `-var-file`. The production backend key preserves
the existing live state location; staging uses `lingowave/staging/terraform.tfstate`.

Required production controls before enabling public traffic:

- PostgreSQL-backed credit ledger with reserve/finalize/release transactions.
- If `enable_rds=true`, provide `database_password` through a secret-backed Terraform variable; there is no committed password default.
- Set `create_network=true` to let Terraform create a small two-AZ VPC, public worker subnets, isolated database subnets, and least-privilege worker/database security groups. Leave it false only when the deployment supplies existing `vpc_id`, subnet IDs, and security-group IDs.
- Pass `worker_secrets` as a map of ECS environment names to Secrets Manager ARNs (at minimum `DATABASE_URL`; add `TRANSLATION_API_KEY` or other provider secrets as needed). ECS injects these at task start; no secret value belongs in Terraform variables, images, or git.
- The production default `translation_provider = "aws-translate"` uses Amazon Translate through the worker task role; Terraform grants only `translate:TranslateText` in that mode. Set `configured-api` when using an external translation endpoint and inject its key through `worker_secrets`.
- S3 object validation, lifecycle retention rules, and signed URLs.
- SQS visibility timeout, bounded retries, idempotency keys, and dead-letter queue.
- For SES mail, set `mail_provider = "ses"`, use a verified `mail_from`, and pass its `ses_identity_arn`; Terraform scopes `ses:SendEmail` to that identity.
- `sqs_visibility_timeout_seconds` defaults to 3600 seconds and should cover the measured worst-case dubbing job; it is configurable up to AWS's 12-hour maximum.
- CloudWatch metrics for queue age, successful minutes, startup time, and actual cost per minute.
- Separate model images for transcription/separation, voice, and optional lip sync.
- No permanent GPU service; Spot capacity is optional only after interruption recovery is proven.

The exact AWS service choice (ECS GPU capacity providers vs. managed EC2 queue workers vs. SageMaker async) should be selected from measured `$ / successfully processed minute`, not the lowest hourly instance price. This repository includes the domain/config layer to store those measurements without embedding guesses in application logic.

The Terraform implementation uses an ECS EC2 GPU capacity provider backed by an ASG with desired/minimum capacity zero. SQS CloudWatch alarms use ECS service step scaling to raise the worker task count when messages are visible and reduce it after a sustained empty period; the capacity provider then supplies GPU instances and can return the ASG to zero when tasks drain. AWS cautions against using `ApproximateNumberOfMessagesVisible` as a target-tracking metric because message count is not proportional to instance count, so the queue alarms intentionally use step scaling. Verify the step sizes and cooldowns with measured processing time before production launch.

Set `api_image` to the published API image, pass `api_certificate_arn` for an ACM certificate, and pass `api_secrets = { DATABASE_URL = "..." }` to provision the Fargate API service behind an HTTPS ALB. HTTP redirects to HTTPS and the API uses secure cookies. If `create_network=true`, Terraform creates the API/load-balancer security groups and reuses the generated public subnets; with an existing VPC, provide `api_subnet_ids`, `api_security_group_id`, and `load_balancer_security_group_id` configured for ports 443→8000. Inject provider credentials through `api_secrets` or `worker_secrets`; never copy secret values into output files or images.

For a first launch without a custom domain, set `frontend_enabled=true` and leave `api_certificate_arn` empty. Terraform then creates one private S3 frontend bucket and a CloudFront distribution with the default AWS HTTPS certificate; `/api/*`, `/v1/*`, and `/health` route to the HTTP ALB origin. Build the frontend with `VITE_API_URL=''`, pass the absolute `frontend_dist_dir`, and set `frontend_origin` to the resulting `frontend_url` on the upload apply. The `api_url` and `frontend_url` outputs are the same CloudFront URL. Add a custom ACM certificate later when a domain is available.

After apply, use the Terraform outputs for `api_url`, `S3_BUCKET`, `SQS_QUEUE_URL`, `AWS_REGION`, and the generated network IDs. The API service is intentionally steady-state; only the GPU worker service scales to zero.
