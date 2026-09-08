# ECR retention audit

Date: 2026-09-08  
Account: `520646547849`  
Region: `eu-north-1`

This audit records the deliberate cleanup of superseded container manifests
after the serverless API cutover and the real prewarmed CPU E2E validation.
It does not delete any compute, database, queue, storage, or GPU-quota
resource. The GPU worker image is retained as architecture/rollback evidence
while its ASG and service remain at zero.

## Before cleanup

| Repository | Manifests | Tagged | Untagged | Logical image size |
| --- | ---: | ---: | ---: | ---: |
| `lingowave-api` | 89 | 71 | 18 | 20.89 GiB |
| `lingowave-worker` | 18 | 15 | 3 | 59.43 GiB |

## Retained digests

The following immutable digests are tagged with `keep-*` aliases before any
other manifest is removed. The aliases make the retention decision visible to
the live ECR lifecycle policy and provide an emergency rollback reference.

### `lingowave-api`

| Keep tag | Digest | Reason |
| --- | --- | --- |
| `keep-production-lambda` | `sha256:3e5161015099b1246017a1cdc1636d0434043d0db45bcdb4c107dc579500ff63` | Deployed serverless API and migration Lambda image |
| `keep-rollback-lambda` | `sha256:c9b98896f01d88ba2d9970c1d692e30eaa6a8dbc0d4f7ccc7c94fe57eb5247fb` | Immediately previous known-good Lambda image |
| `keep-production-api` | `sha256:8d70a7686069c53296b23c8436d59252c5b75e87218044ae5c1f18756b3a7520` | Latest normal API image for future ECS rollback/reuse |
| `keep-rollback-legacy-api` | `sha256:8a4fe642d13a485e428bbe13437085c376674ab57f977b9f294e37694f285148` | Legacy API emergency rollback image |

### `lingowave-worker`

| Keep tag | Digest | Reason |
| --- | --- | --- |
| `keep-production-cpu` | `sha256:9baea47e7e93c3e761e909948e41bebeac1e1365f62391521e8b18891bdfea16` | Deployed prewarmed CPU worker; VoxCPM2 cache embedded |
| `keep-rollback-cpu` | `sha256:407a6ff4a33228da1e85239a1e9d672f5faf88d19c35384e6b12de63ea820d63` | Previous real-E2E-validated CPU worker |
| `keep-production-gpu` | `sha256:fd430e014b522a47d95910d66cb225b46a554f5e116cc86e6c121663573edbc0` | Latest GPU architecture image; execution remains disabled at zero |
| `keep-rollback-gpu` | `sha256:573d6e577f6d3ce1369897562f8ed1472f20a2720d3b228bb13486538e59266b` | Previous GPU architecture rollback image |

All other manifests, including the failed oversized prewarm candidate and
superseded tagged/untagged layers, are cleanup targets. No secret material is
present in image tags, manifests, or this audit.

## After cleanup

The final counts and logical sizes are appended below after the immutable keep
tags are applied and the cleanup batch completes.

| Repository | Manifests | Tagged | Untagged | Logical image size |
| --- | ---: | ---: | ---: | ---: |
| `lingowave-api` | 4 | 4 | 0 | 1.04 GiB |
| `lingowave-worker` | 4 | 4 | 0 | 15.90 GiB |

The cleanup removed 88 API image records and 15 worker image records as
reported by ECR. The logical inventory fell by approximately 19.85 GiB for
the API repository and 43.53 GiB for the worker repository. ECR billing uses
deduplicated layer storage rather than this logical-manifest sum, so the exact
invoice reduction will appear after the normal billing delay.

The live ECR lifecycle policy remains enabled: untagged manifests expire after
one day, `keep-*` aliases are retained, and other tagged history is bounded.
