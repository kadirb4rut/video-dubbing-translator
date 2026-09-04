from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import GpuCostProfile, Job, JobEvent, User, UsageRecord, now
from app.queueing import JobMessage, SQSQueue
from app.worker import JobWorker


def test_usage_telemetry_uses_measured_gpu_profile_and_accumulates_retries():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="telemetry@example.com", password_hash="hash")
        db.add(user)
        db.flush()
        job = Job(user_id=user.id, operation="transcription", idempotency_key="telemetry-job", estimate_credits=1, reserved_credits=1, retry_count=2)
        db.add(job)
        db.flush()
        db.add(GpuCostProfile(provider="aws", gpu_type="g4dn.xlarge", region=settings.s3_region, pricing_mode="on_demand", hourly_price_usd=3.60, startup_seconds=10, model_load_seconds=20, processed_minutes_per_hour=60, measured=True))
        db.commit()

        worker = JobWorker()
        worker.gpu_type = "g4dn.xlarge"
        worker._record_usage(db, job, 60, 60, time.monotonic() - 10, input_bytes=100, output_bytes=200)
        db.commit()
        worker._record_usage(db, job, 60, 60, time.monotonic() - 5, input_bytes=100, output_bytes=300)
        db.commit()

        record = db.query(UsageRecord).one()
        assert record.model_version == "whisper"
        assert record.retry_count == 1
        assert record.output_duration_seconds == 60
        assert record.output_bytes == 300
        assert record.wall_clock_seconds >= 14
        assert record.estimated_cost_usd is not None
        assert record.actual_cost_usd is not None
        assert record.actual_cost_usd > 0


def test_worker_requeues_stale_job_from_any_active_stage():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="stale@example.com", password_hash="hash")
        db.add(user)
        db.flush()
        job = Job(user_id=user.id, operation="transcription", state="transcribing", idempotency_key="stale-job", estimate_credits=1, reserved_credits=1, retry_count=1, updated_at=now() - timedelta(hours=1))
        db.add(job)
        db.commit()

        worker = JobWorker()
        worker._recover_stale(db)
        assert job.state == "queued"
        event = db.query(JobEvent).filter_by(job_id=job.id).order_by(JobEvent.created_at.desc()).first()
        assert event is not None
        assert event.message == "Recovered stale worker lease"


def test_worker_acknowledges_queue_message_after_processing(monkeypatch):
    events = []

    class FakeQueue:
        def receive(self, timeout=0.25):
            return JobMessage("job-1", "transcription", "receipt-1")

        def delete(self, message):
            events.append("delete")

    worker = JobWorker()
    worker.queue = FakeQueue()
    monkeypatch.setattr(worker, "_heartbeat", lambda db: None)
    monkeypatch.setattr(worker, "_recover_stale", lambda db: None)
    monkeypatch.setattr(worker, "_claim", lambda db, message: events.append("claim") or Job(id="job-1"))
    monkeypatch.setattr(worker, "process", lambda job_id: events.append("process"))
    monkeypatch.setattr(worker, "_ack_if_settled", lambda message, job_id: events.append("ack"))

    assert worker.run_once() is True
    assert events == ["claim", "process", "ack"]


def test_sqs_visibility_can_be_extended_for_a_long_running_job():
    calls = []

    class FakeClient:
        def change_message_visibility(self, **kwargs):
            calls.append(kwargs)

    queue = object.__new__(SQSQueue)
    queue.client = FakeClient()
    queue.url = "https://sqs.example/jobs"
    queue.change_visibility(JobMessage("job-1", "dubbing", "receipt-1"), 3600)

    assert calls == [{"QueueUrl": "https://sqs.example/jobs", "ReceiptHandle": "receipt-1", "VisibilityTimeout": 3600}]
