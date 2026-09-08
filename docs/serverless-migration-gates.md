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
| Safe Phase A graph | Read-only Terraform plan retains RDS, ECS API, ALB, frontend, and worker definitions; creates Aurora/Lambda/API Gateway without CloudFront cutover | PASS: `30 add, 9 change, 4 task-definition/service replacements` |
| Worker safety | `cpu_worker_max_count=1`, queue scale-in min `0`, SQS DLQ and visibility lease configured | PASS: live CPU target and alarms applied; Aurora-routed task definition active |
| Cost controls | ECR lifecycle, budgets, anomaly detection, log retention, and S3 lifecycle are present in Terraform | PASS: ECR lifecycle policies live; workers/ASG idle |
| CI credential boundary | GitHub OIDC ECR role accepts only `github_actions_branch`; pull requests and other branches are excluded | PASS in IaC; trust policy awaits apply |
| Local regression suite | `81 passed`, frontend production build, Ruff, compile, Terraform validation | PASS |

## Live migration gates

The current Phase A evidence is recorded below. Legacy resources remain
intentionally retained for rollback; this is not the final cleanup phase.

1. **PASS** — immutable Lambda image deployed and API Gateway/Lambda health
   returned HTTP 200.
2. **PASS** — all three Google OAuth SSM Standard `SecureString` parameters
   exist; values were never printed.
3. **PASS** — Phase A applied with `retain_legacy_rds=true`,
   `retain_legacy_api=true`, and the separate CloudFront cutover applied only
   after data/API validation.
4. **PASS** — the real migration task copied 25 application tables and 1,090
   rows from legacy RDS to Aurora; source and target counts matched.
5. **PASS** — direct API Gateway and public CloudFront `/health`, Google login,
   and invalid callback routes were verified; CloudFront is deployed with the
   API Gateway origin.
6. **PASS** — the live serverless CPU E2E showed worker `0 → 1 → 0`, a
   completed real-media job, Aurora status updates, and a verified artifact
   download. The latest evidence is recorded in
   `docs/AWS_MEDIA_PIPELINE_STATUS.md`.
7. **PASS** — the rollback-window idle audit passes with legacy resources
   observed, CPU/GPU workers and GPU ASG at zero, Aurora configured for zero
   ACU auto-pause, ECR lifecycle active, and no NAT gateway. Use
   `--strict-serverless` only after rollback is no longer needed.
8. **OPEN** — after rollback is no longer needed, run a separately reviewed
   cleanup plan with both legacy retain flags set to `false`.

The goal must remain open until each live gate has direct AWS evidence. A plan
file, unit test, or documentation statement is not a substitute for a live
cutover result.
