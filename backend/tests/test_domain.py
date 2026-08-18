import pytest

from app.domain import CostProfile, JobState, estimate_credits, should_recredit, transition


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
