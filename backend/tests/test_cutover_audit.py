from __future__ import annotations

from scripts.verify_serverless_cutover import _ecs_service, _ecs_service_absent, _ssm_secure_strings


class FakeEcsClient:
    class exceptions:
        ClusterNotFoundException = type("ClusterNotFoundException", (Exception,), {})

    def describe_services(self, **kwargs):
        raise self.exceptions.ClusterNotFoundException()


class FakeSsmClient:
    def __init__(self):
        self.calls: list[list[str]] = []

    def get_parameters(self, *, Names, WithDecryption):
        self.calls.append(list(Names))
        return {
            "Parameters": [{"Name": name, "Type": "SecureString"} for name in Names],
            "InvalidParameters": [],
        }


def test_cutover_audit_treats_missing_ecs_cluster_as_absent():
    assert _ecs_service(FakeEcsClient(), "removed-api-cluster", "removed-api") is None


def test_cutover_audit_treats_inactive_ecs_service_tombstone_as_absent():
    assert _ecs_service_absent({"status": "INACTIVE"}) is True


def test_cutover_audit_batches_ssm_parameters_and_requires_secure_strings():
    client = FakeSsmClient()
    names = [f"/lingowave/production/key-{index}" for index in range(11)]

    valid, detail = _ssm_secure_strings(client, names)

    assert valid is True
    assert detail == {"count": 11, "invalid": []}
    assert [len(call) for call in client.calls] == [10, 1]
