# Serverless migration gates

This checklist is intentionally evidence-driven. A green local test or a
successful Terraform plan does not prove that the production migration is
complete; the live gates below must be recorded after the corresponding AWS
operation.

## Repository gates

| Gate | Evidence | Current status |
| --- | --- | --- |
| API Gateway/Lambda adapter | `backend/tests/test_serverless_handlers.py` invokes a real Mangum HTTP API v2 event | PASS |
| On-demand migrations | The same test exercises `app.migration_handler.handler` and Alembic `head` | PASS |
| PostgreSQL/Aurora readiness | `scripts/verify_aurora_engine.py --region eu-north-1 --engine-version 16.8` | PASS: live orderability verified |
| Serverless production graph | CloudFront routes to API Gateway/Lambda; Aurora Data API is live; only queue-driven workers remain on ECS | PASS: final graph applied and legacy API/RDS/ALB cleanup completed |
| Worker safety | `cpu_worker_max_count=1`, queue scale-in min `0`, SQS DLQ and visibility lease configured | PASS: live CPU target and alarms applied; Aurora-routed task definition active |
| Cost controls | ECR lifecycle, budgets, anomaly detection, log retention, and S3 lifecycle are present in Terraform | PASS: ECR lifecycle policies live; workers/ASG idle |
| CI credential boundary | GitHub OIDC ECR role accepts only `github_actions_branch`; pull requests and other branches are excluded | PASS in IaC and live role |
| Local regression suite | Backend suite, frontend production build, Ruff, Bandit, pip-audit, npm audit, Terraform validation | PASS |

## Live migration gates

The final production evidence is recorded below. Legacy compute/database/load
balancing resources were removed only after validation and an encrypted RDS
snapshot was retained.

1. **PASS** — immutable Lambda image deployed and API Gateway/Lambda health
   returned HTTP 200.
2. **PASS** — all three Google OAuth SSM Standard `SecureString` parameters
   exist; values were never printed.
3. **PASS** — serverless cutover applied and CloudFront now uses only the
   frontend S3 origin plus API Gateway; no legacy ALB/API origin remains.
4. **PASS** — the real migration task copied 25 application tables and 1,090
   rows from legacy RDS to Aurora; source and target counts matched.
5. **PASS** — direct API Gateway and public CloudFront `/health`, Google login,
   and invalid callback routes were verified; CloudFront is deployed with the
   API Gateway origin.
6. **PASS** — the live serverless CPU E2E showed worker `0 → 1 → 0`, a
   completed real-media job, Aurora status updates, and a verified artifact
   download. The prewarmed CPU image ran with VoxCPM2 download disabled.
7. **PASS** — the strict serverless audit passes with CPU/GPU workers and GPU
   ASG at zero, Aurora configured for zero ACU auto-pause, ECR lifecycle active,
   ECR retention cleanup complete, and no NAT gateway.
8. **PASS** — legacy RDS, ALB, API ECS service/task definition, and obsolete
   security-group paths were removed after the rollback snapshot was created.

All live gates now have direct AWS evidence. The GPU quota request remains
open by design; GPU execution is a later performance upgrade and does not block
the CPU production path.
