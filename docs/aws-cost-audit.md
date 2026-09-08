# AWS cost audit

Snapshot date: 2026-09-08, account `520646547849`, region `eu-north-1`.

## Before migration

The supplied `costs.csv` contains three billable days totaling `$9.8579798828`,
which is a simple 30-day run-rate of approximately `$98.58`, not a full-month
forecast. The largest run-rate contributors were ECS `$44.28`, ECR `$15.75`,
ALB `$15.09`, RDS `$13.07`, and VPC `$9.79`. Those five categories represented
99.39% of the sample.

The live account still matches the legacy shape at this snapshot:

- ECS API service: desired/running `1/1`.
- ECS GPU and CPU worker services: desired/running `0/0`.
- GPU Auto Scaling Group: desired `0`, min `0`, max `10`.
- Application Load Balancer: active.
- Provisioned PostgreSQL: `db.t4g.micro`, available.
- Aurora Express/Serverless v2 Data API and API Gateway: live after Phase A;
  CloudFront now routes API traffic to API Gateway.
- NAT gateways and VPC endpoints: none found.

The live CPU worker task definition is `4096 CPU / 16384 MiB`, and recent ECS
service events include placement failures caused by the account's concurrent
vCPU quota when more than one task was requested. The serverless target keeps a
separate `cpu_worker_max_count` default of `1` until a quota increase and a
measured concurrency need justify raising it; this limits both surprise spend
and avoidable failed placements.

ECR inventory is materially larger than the cost CSV alone reveals: 68 API
image manifests and 84 worker manifests, approximately 269 GiB of logical
image sizes. Phase A now has lifecycle policies live on both repositories. The
Terraform policy keeps explicitly tagged `release-*` images out of the
catch-all cleanup rule, keeps only the newest five other manifests per
repository, and expires untagged leftovers after one day. Recheck actual ECR
storage after the next lifecycle run before treating the `$5` idle-baseline
target as credible.

## Target architecture

| Area | Legacy | Target |
| --- | --- | --- |
| API | ECS/Fargate + ALB | API Gateway HTTP API + Lambda |
| Database | Provisioned RDS PostgreSQL | Aurora PostgreSQL Serverless v2, 0 ACU auto-pause |
| Workers | ECS services | SQS-driven ECS/Fargate Spot CPU or ECS/EC2 GPU fleet, desired zero when idle |
| Uploads | API multipart path available | Presigned S3 upload/complete path for media and voice |
| Secrets | ECS Secrets Manager maps | SSM Standard for ordinary Lambda configuration; Aurora-managed secret retained for Data API |
| Controls | Ad hoc | Budget, anomaly detection, log retention, S3/ECR lifecycle policies |

The first migration plan intentionally retains the old RDS instance through
`retain_legacy_rds=true`. Data is copied and the new API is validated before a
separate cleanup plan is allowed to remove the legacy database. On the current
AWS Free Plan, Aurora was created with the official Express configuration and
is referenced by Terraform with `aurora_provisioning_mode="express-existing"`;
the external cluster and application secret are not recreated or destroyed by
Terraform.

## Expected idle cost

API and worker compute, ALB, and Aurora capacity are intended to be zero or
usage-based while idle. Remaining idle-generating categories are ECR storage,
Aurora storage/backups, the Aurora-managed Secrets Manager secret, CloudWatch
logs, S3 objects, CloudFront requests, and any enabled budget/anomaly or domain
costs. The practical idle target is approximately `$5/month` excluding durable
storage, backups, domain costs, and user-driven processing; it is conditional on
ECR pruning, log volume, free-tier eligibility, and the actual Aurora storage
footprint. A complete idle-month Cost Explorer audit is required after cutover.

No Savings Plan, Reserved Instance, NAT Gateway, or GPU commitment is part of
the proposed architecture.
