# LingoWave infrastructure direction

The first production deployment is intentionally queue-first and scale-to-zero:

```text
React/Vite web → FastAPI API → PostgreSQL (ledger + jobs)
                       ├── S3 private objects (signed upload/download URLs)
                       └── SQS → GPU worker capacity (ECS/EC2 GPU, desired count 0 when idle)
```

The worker image is split from the API image. Queue depth and oldest-message age drive the worker autoscaling policy. When the queue is empty, the desired GPU capacity returns to zero; model caches should live in image layers or a measured shared cache so cold-start economics are visible in benchmarks.

Required production controls before enabling public traffic:

- PostgreSQL-backed credit ledger with reserve/finalize/release transactions.
- S3 object validation, lifecycle retention rules, and signed URLs.
- SQS visibility timeout, bounded retries, idempotency keys, and dead-letter queue.
- CloudWatch metrics for queue age, successful minutes, startup time, and actual cost per minute.
- Separate model images for transcription/separation, voice, and optional lip sync.
- No permanent GPU service; Spot capacity is optional only after interruption recovery is proven.

The exact AWS service choice (ECS GPU capacity providers vs. managed EC2 queue workers vs. SageMaker async) should be selected from measured `$ / successfully processed minute`, not the lowest hourly instance price. This repository includes the domain/config layer to store those measurements without embedding guesses in application logic.
