import pytest
from app.config import settings
from app.domain import (
    CostProfile,
    JobState,
    estimate_credits,
    should_recredit,
    transition,
)
from app.models import Job, now
from app.services import estimate_for_duration, serialize_job
from fastapi import HTTPException


def test_estimate_credits_uses_measured_profile_and_optional_lip_sync():
    profiles = {
        "dubbing": CostProfile("worker-a", "g5.xlarge", 40, 12, 1.20),
        "lip_sync": CostProfile("worker-b", "g5.2xlarge", 70, 6, 2.40),
    }
    assert estimate_credits(10, "dubbing", profiles) == 1
    assert estimate_credits(10, "dubbing", profiles, lip_sync=True) == 7


def test_jobs_follow_explicit_state_machine():
    assert transition(JobState.QUEUED, JobState.PROVISIONING) == JobState.PROVISIONING
    with pytest.raises(ValueError):
        transition(JobState.COMPLETED, JobState.QUEUED)


def test_infrastructure_failures_release_reserved_credits():
    assert should_recredit(infrastructure_failure=True)
    assert not should_recredit(infrastructure_failure=False)
    assert not should_recredit(infrastructure_failure=True, user_cancelled=True)


def test_lip_sync_is_rejected_until_a_real_provider_is_enabled():
    with pytest.raises(HTTPException) as error:
        estimate_for_duration(60, "dubbing", lip_sync=True)
    assert error.value.status_code == 503


def test_unmeasured_pricing_fails_closed_for_production_database(monkeypatch):
    previous_database_url = settings.database_url
    object.__setattr__(settings, "database_url", "postgresql+psycopg://lingowave@db/lingowave")
    monkeypatch.delenv("ALLOW_UNMEASURED_PRICING", raising=False)
    try:
        with pytest.raises(HTTPException) as error:
            estimate_for_duration(60, "transcription")
        assert error.value.status_code == 503
    finally:
        object.__setattr__(settings, "database_url", previous_database_url)


def test_user_job_serialization_does_not_expose_internal_worker_errors():
    timestamp = now()
    job = Job(user_id="user", operation="noise", idempotency_key="job", state="failed", estimate_credits=1, reserved_credits=1, error_code="WORKER_FAILURE", error_message="Traceback: /private/model-cache/secret", created_at=timestamp, updated_at=timestamp)
    assert serialize_job(job)["error_message"] == "The media worker could not complete this job. Please retry."
    assert serialize_job(job, include_internal_error=True)["error_message"].startswith("Traceback:")
