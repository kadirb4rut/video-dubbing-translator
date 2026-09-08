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
| Safe Phase A graph | Read-only Terraform plan retains RDS, ECS API, ALB, and frontend; creates Aurora/Lambda/API Gateway | PASS: `30 add, 10 change, 8 destroy` |
| Worker safety | `cpu_worker_max_count=1`, queue scale-in min `0`, SQS DLQ and visibility lease configured | PASS in IaC; live target awaits apply |
| Cost controls | ECR lifecycle, budgets, anomaly detection, log retention, and S3 lifecycle are present in Terraform | PASS in IaC; ECR policy not yet live |
| CI credential boundary | GitHub OIDC ECR role accepts only `github_actions_branch`; pull requests and other branches are excluded | PASS in IaC; trust policy awaits apply |
| Local regression suite | `79 passed`, frontend production build, Ruff, compile, Terraform validation | PASS |

## Live migration gates

These remain open because completing them changes or exercises the AWS
production environment:

1. Publish and verify an immutable Lambda image with a `lambda-*` ECR tag.
2. Create all mapped SSM Standard `SecureString` parameters and run the
   fail-closed artifact preflight.
3. Apply Phase A with `retain_legacy_rds=true` and `retain_legacy_api=true`.
4. Run the PostgreSQL dump/restore from a VPC-reachable task and validate row
   counts, constraints, migrations, authentication, uploads, jobs, and
   downloads.
5. Run the manual `AWS Serverless CPU Scale-to-Zero E2E` workflow. Evidence
   must show worker `0 → 1+ → 0`, a completed job, and a verified artifact
   download.
6. Perform a full idle audit: no legacy API task, no ALB, Aurora paused at
   zero ACU, workers/ASG at zero, ECR lifecycle active, and no unexpected NAT,
   public-IP, or endpoint cost. Record the JSON output of
   `scripts/verify_serverless_cutover.py --strict-serverless --require-idle`.
7. Only after rollback is no longer needed, run a separately reviewed cleanup
   plan with both legacy retain flags set to `false`.

The goal must remain open until each live gate has direct AWS evidence. A plan
file, unit test, or documentation statement is not a substitute for a live
cutover result.
