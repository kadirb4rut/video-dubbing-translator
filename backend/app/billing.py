from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    monthly_credits: int
    max_concurrent_jobs: int
    max_voice_profiles: int


PLANS = {
    "free": Plan("free", "Free", 30, 1, 1),
    "creator": Plan("creator", "Creator", 500, 2, 5),
    "pro": Plan("pro", "Pro", 1600, 4, 20),
    "studio": Plan("studio", "Studio", 5000, 8, 100),
}


class BillingProvider:
    """Future payment boundary. No payment processor is enabled in this milestone."""

    name = "disabled"

    def reconcile_verified_event(self, payload: bytes, signature: str) -> None:
        raise NotImplementedError("Payment-provider integration is intentionally disabled")


def plan(key: str) -> Plan:
    return PLANS.get(key, PLANS["free"])
