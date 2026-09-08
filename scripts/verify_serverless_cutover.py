#!/usr/bin/env python3
"""Read-only post-apply audit for the serverless migration and idle baseline."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any

import boto3


def _check(results: list[dict[str, Any]], name: str, passed: bool, detail: Any) -> None:
    results.append({"name": name, "status": "pass" if passed else "fail", "detail": detail})


def _health(api_url: str) -> tuple[bool, Any]:
    try:
        with urllib.request.urlopen(f"{api_url.rstrip('/')}/health", timeout=30) as response:
            body = json.loads(response.read())
            return response.status == 200 and body.get("status") == "ok", {"http": response.status, "body": body}
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return False, str(exc)


def _find_api_gateway(client, name: str) -> dict[str, Any] | None:
    for page in client.get_paginator("get_apis").paginate():
        for api in page.get("Items", []):
            if api.get("Name") == f"{name}-http":
                return api
    return None


def _find_load_balancer(client, name: str) -> dict[str, Any] | None:
    for page in client.get_paginator("describe_load_balancers").paginate():
        for load_balancer in page.get("LoadBalancers", []):
            if load_balancer.get("LoadBalancerName") == f"{name}-api":
                return load_balancer
    return None


def _find_lambda(client, name: str) -> dict[str, Any] | None:
    for page in client.get_paginator("list_functions").paginate():
        for function in page.get("Functions", []):
            if function.get("FunctionName") == f"{name}-api":
                return function
    return None


def _find_cluster(client, name: str) -> dict[str, Any] | None:
    for page in client.get_paginator("describe_db_clusters").paginate():
        for cluster in page.get("DBClusters", []):
            if cluster.get("DBClusterIdentifier") == f"{name}-aurora":
                return cluster
    return None


def _find_db_instance(client, identifier: str) -> dict[str, Any] | None:
    for page in client.get_paginator("describe_db_instances").paginate():
        for instance in page.get("DBInstances", []):
            if instance.get("DBInstanceIdentifier") == identifier:
                return instance
    return None


def _ecs_service(client, cluster: str, service: str) -> dict[str, Any] | None:
    try:
        response = client.describe_services(cluster=cluster, services=[service])
    except client.exceptions.ClusterNotFoundException:
        return None
    return (response.get("services") or [None])[0]


def _ecs_service_absent(service: dict[str, Any] | None) -> bool:
    """Treat ECS's retained INACTIVE tombstone as removed infrastructure."""

    return not service or service.get("status") == "INACTIVE"


def _ecr_policy(client, repository: str) -> bool:
    try:
        client.get_lifecycle_policy(repositoryName=repository)
        return True
    except (client.exceptions.LifecyclePolicyNotFoundException, client.exceptions.RepositoryNotFoundException):
        return False


def _ssm_secure_strings(client, names: list[str]) -> tuple[bool, dict[str, Any]]:
    found: list[dict[str, Any]] = []
    invalid: list[str] = []
    for offset in range(0, len(names), 10):
        response = client.get_parameters(Names=names[offset : offset + 10], WithDecryption=False)
        found.extend(response.get("Parameters", []))
        invalid.extend(response.get("InvalidParameters", []))
    valid = not invalid and len(found) == len(names) and all(item.get("Type") == "SecureString" for item in found)
    return valid, {"count": len(found), "invalid": invalid}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="eu-north-1")
    parser.add_argument("--name", default="lingowave")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--ecs-cluster", default="lingowave-workers")
    parser.add_argument("--api-cluster", default="lingowave-api")
    parser.add_argument("--api-service", default="lingowave-api")
    parser.add_argument("--cpu-service", default="lingowave-cpu-worker")
    parser.add_argument("--gpu-service", default="lingowave-worker")
    parser.add_argument("--gpu-asg", default="lingowave-gpu-workers")
    parser.add_argument("--db-identifier", default="lingowave")
    parser.add_argument("--strict-serverless", action="store_true", help="Fail if legacy API/RDS/ALB resources remain.")
    parser.add_argument("--require-idle", action="store_true", help="Fail unless CPU/GPU workers and GPU ASG are at zero.")
    parser.add_argument("--ssm-parameter", action="append", default=[])
    args = parser.parse_args()

    results: list[dict[str, Any]] = []
    health_ok, health_detail = _health(args.api_url)
    _check(results, "api_health", health_ok, health_detail)

    clients = {
        "lambda": boto3.client("lambda", region_name=args.region),
        "apigatewayv2": boto3.client("apigatewayv2", region_name=args.region),
        "rds": boto3.client("rds", region_name=args.region),
        "elb": boto3.client("elbv2", region_name=args.region),
        "ecs": boto3.client("ecs", region_name=args.region),
        "autoscaling": boto3.client("autoscaling", region_name=args.region),
        "ecr": boto3.client("ecr", region_name=args.region),
        "ec2": boto3.client("ec2", region_name=args.region),
        "ssm": boto3.client("ssm", region_name=args.region),
    }

    function = _find_lambda(clients["lambda"], args.name)
    function_config = None
    if function:
        try:
            function_config = clients["lambda"].get_function_configuration(FunctionName=f"{args.name}-api")
        except clients["lambda"].exceptions.ResourceNotFoundException:
            function_config = None
    lambda_ok = bool(
        function
        and function.get("PackageType") == "Image"
        and function_config
        and function_config.get("State") == "Active"
        and function_config.get("LastUpdateStatus") == "Successful"
    )
    _check(results, "lambda_api", lambda_ok, function_config or function or "missing")

    api_gateway = _find_api_gateway(clients["apigatewayv2"], args.name)
    _check(results, "api_gateway_http", bool(api_gateway and api_gateway.get("ProtocolType") == "HTTP"), api_gateway or "missing")

    aurora = _find_cluster(clients["rds"], args.name)
    aurora_ok = bool(
        aurora
        and aurora.get("Engine") == "aurora-postgresql"
        and (aurora.get("ServerlessV2ScalingConfiguration") or {}).get("MinCapacity") == 0
    )
    _check(results, "aurora_zero_acu_config", aurora_ok, aurora or "missing")

    legacy_db = _find_db_instance(clients["rds"], args.db_identifier)
    _check(results, "legacy_rds_absent" if args.strict_serverless else "legacy_rds_observed", not legacy_db if args.strict_serverless else True, legacy_db or "absent")

    load_balancer = _find_load_balancer(clients["elb"], args.name)
    _check(results, "legacy_alb_absent" if args.strict_serverless else "legacy_alb_observed", not load_balancer if args.strict_serverless else True, load_balancer or "absent")

    legacy_api = _ecs_service(clients["ecs"], args.api_cluster, args.api_service)
    _check(
        results,
        "legacy_ecs_api_absent" if args.strict_serverless else "legacy_ecs_api_observed",
        _ecs_service_absent(legacy_api) if args.strict_serverless else True,
        "absent" if _ecs_service_absent(legacy_api) else legacy_api,
    )

    cpu_service = _ecs_service(clients["ecs"], args.ecs_cluster, args.cpu_service)
    gpu_service = _ecs_service(clients["ecs"], args.ecs_cluster, args.gpu_service)
    cpu_idle = not cpu_service or (cpu_service.get("desiredCount", 0) == 0 and cpu_service.get("runningCount", 0) == 0)
    gpu_idle = not gpu_service or (gpu_service.get("desiredCount", 0) == 0 and gpu_service.get("runningCount", 0) == 0)
    _check(results, "cpu_worker_idle", cpu_idle if args.require_idle else True, cpu_service or "absent")
    _check(results, "gpu_worker_idle", gpu_idle if args.require_idle else True, gpu_service or "absent")

    asg_response = clients["autoscaling"].describe_auto_scaling_groups(AutoScalingGroupNames=[args.gpu_asg])
    asg = (asg_response.get("AutoScalingGroups") or [None])[0]
    asg_idle = not asg or (asg.get("DesiredCapacity", 0) == 0 and asg.get("InServiceInstances", 0) == 0)
    _check(results, "gpu_asg_idle", asg_idle if args.require_idle else True, asg or "absent")

    api_ecr_policy = _ecr_policy(clients["ecr"], f"{args.name}-api")
    worker_ecr_policy = _ecr_policy(clients["ecr"], f"{args.name}-worker")
    _check(results, "api_ecr_lifecycle", api_ecr_policy, "policy present" if api_ecr_policy else "missing")
    _check(results, "worker_ecr_lifecycle", worker_ecr_policy, "policy present" if worker_ecr_policy else "missing")

    nat = clients["ec2"].describe_nat_gateways(Filters=[{"Name": "state", "Values": ["available", "pending"]}]).get("NatGateways", [])
    _check(results, "nat_gateways_absent", not nat, nat)

    if args.ssm_parameter:
        ssm_ok, ssm_detail = _ssm_secure_strings(clients["ssm"], args.ssm_parameter)
        _check(results, "ssm_secure_strings", ssm_ok, ssm_detail)

    print(json.dumps({"ready": all(item["status"] == "pass" for item in results), "checks": results}, indent=2, default=str))
    return 0 if all(item["status"] == "pass" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
