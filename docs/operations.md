# Operations runbook

## Job lifecycle

The API creates an immutable job specification, reserves credits, and sends an SQS message. A worker claims `queued`, records every stage in `job_events`, uploads an output, and finalizes the reservation only after output creation. Provider or infrastructure failures set an explicit error code and release the reservation; user cancellation is accounted as consumed work. Jobs can be retried only through a deliberate operator action; the worker never silently retries a failed model call in-process.

## Production checks

- Set PostgreSQL, S3, SQS, `COOKIE_SECURE=true`, and a non-local `FRONTEND_ORIGIN`.
- Use a private S3 bucket and signed downloads; never put media in a public bucket.
- Set SQS visibility timeout longer than the measured worst-case job, with a DLQ and bounded redelivery.
- Keep API and worker images separate. GPU capacity starts at zero and scales from queue depth in `infrastructure/terraform`.
- Run `scripts/benchmarks/benchmark_media.py` against representative media and replace the development-only values in `config/cost_profiles.json` only with measured results.
- Do not enable public voice cloning until consent, deletion, abuse reporting, and the model/checkpoint license review are complete.

## Incident handling

For stuck jobs, inspect `/api/jobs/{id}` and the `job_events` sequence before changing state. If the worker died after reservation and before completion, re-drive the SQS message or run the operator reconciliation that releases the reservation; do not edit ledger rows manually. Rotate session secrets at the edge and revoke affected sessions if account access is suspected.
