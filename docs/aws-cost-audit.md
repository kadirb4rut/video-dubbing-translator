# AWS cost audit

Snapshot date: 2026-09-08, account `520646547849`, region `eu-north-1`.

## Before migration

The supplied `costs.csv` contains three billable days totaling `$9.8579798828`,
which is a simple 30-day run-rate of approximately `$98.58`, not a full-month
forecast. The largest run-rate contributors were ECS `$44.28`, ECR `$15.75`,
ALB `$15.09`, RDS `$13.07`, and VPC `$9.79`. Those five categories represented
99.39% of the sample.

The final live account no longer has the always-on legacy shape:

- API: CloudFront → API Gateway HTTP API → Lambda; no API ECS service.
- CPU worker: ECS/Fargate Spot autoscaling, final desired/running/pending `0/0/0`.
- GPU worker: ECS/EC2 architecture retained, ECS service and ASG desired `0`.
- Application Load Balancer: removed.
- Provisioned PostgreSQL RDS: removed after a named encrypted snapshot.
- Aurora Serverless v2 Data API: live with observed auto-pause at `0.0` ACU;
  a cold Data API query resumed the cluster successfully.
- NAT gateways, EIPs, and VPC endpoints: none found.

The live CPU worker task definition is `4096 CPU / 16384 MiB`, and recent ECS
service events include placement failures caused by the account's concurrent
vCPU quota when more than one task was requested. The serverless target keeps a
separate `cpu_worker_max_count` default of `1` until a quota increase and a
measured concurrency need justify raising it; this limits both surprise spend
and avoidable failed placements.

ECR inventory was pruned after deployment validation. The final logical
inventory is four API manifests (`1.04 GiB`) and four worker manifests
(`15.90 GiB`), with only production/rollback keep tags and no untagged
manifests. The exact billed layer storage will be reflected after billing
aggregation; the retention evidence is in `docs/ecr-retention-audit.md`.

## Target architecture

| Area | Legacy | Target |
| --- | --- | --- |
| API | ECS/Fargate + ALB | API Gateway HTTP API + Lambda |
| Database | Provisioned RDS PostgreSQL | Aurora PostgreSQL Serverless v2, 0 ACU auto-pause |
| Workers | ECS services | SQS-driven ECS/Fargate Spot CPU or ECS/EC2 GPU fleet, desired zero when idle |
| Uploads | API multipart path available | Presigned S3 upload/complete path for media and voice |
| Secrets | ECS Secrets Manager maps | SSM Standard for ordinary Lambda configuration; Aurora-managed secret retained for Data API |
| Controls | Ad hoc | Budget, anomaly detection, log retention, S3/ECR lifecycle policies |

The legacy RDS instance was retained during validation, then deleted only
after an encrypted manual snapshot named
`lingowave-pre-serverless-cleanup-20260908` reached `available`. Aurora was
created with the official Express configuration and is referenced by Terraform
with `aurora_provisioning_mode="express-existing"`; the external cluster and
application secret are not recreated or destroyed by Terraform.

## Expected idle cost

API and worker compute, ALB, and Aurora capacity are zero or usage-based while
idle. Remaining idle-generating categories are ECR storage, Aurora
storage/backups, the named RDS snapshot, the Aurora-managed Secrets Manager
secret, CloudWatch logs, S3 objects, CloudFront requests, and any enabled
budget/anomaly or domain costs. The live monthly budget guardrail is `$25`; the
measured CPU E2E compute component was `$0.006046` for 13.2 seconds of input
(`$0.027482` per input minute). A complete idle-month Cost Explorer audit is
still required after billing aggregation.

No Savings Plan, Reserved Instance, NAT Gateway, or GPU commitment is part of
the proposed architecture.
