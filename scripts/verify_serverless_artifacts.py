#!/usr/bin/env python3
"""Verify immutable ECR images before a serverless Terraform apply."""

from __future__ import annotations

import argparse
import re
import sys

import boto3

IMAGE_RE = re.compile(r"^(?P<registry>[^/]+)/(?P<repository>[^@]+)@(?P<digest>sha256:[0-9a-f]{64})$")


def _verify_image(client, image: str, *, region: str, label: str, required_tag_prefix: str | None = None) -> None:
    match = IMAGE_RE.fullmatch(image)
    if not match:
        raise ValueError(f"{label} must be an immutable ECR image URI with a sha256 digest")
    expected_suffix = f".dkr.ecr.{region}.amazonaws.com"
    if not match.group("registry").endswith(expected_suffix):
        raise ValueError(f"{label} must point to an ECR registry in {region}")
    response = client.describe_images(repositoryName=match.group("repository"), imageIds=[{"imageDigest": match.group("digest")}])
    if not response.get("imageDetails"):
        raise ValueError(f"{label} digest was not found in ECR: {match.group('repository')}@{match.group('digest')}")
    detail = response["imageDetails"][0]
    tags = detail.get("imageTags", [])
    if required_tag_prefix and not any(tag.startswith(required_tag_prefix) for tag in tags):
        raise ValueError(f"{label} digest exists but has no {required_tag_prefix}* publication tag; publish the Lambda image workflow first")
    print(f"{label}=present repository={match.group('repository')} digest={detail['imageDigest']} tags={tags} pushed={detail.get('imagePushedAt')}")


def _verify_parameters(client, names: list[str]) -> None:
    missing: list[str] = []
    insecure: list[str] = []
    for offset in range(0, len(names), 10):
        response = client.get_parameters(Names=names[offset : offset + 10], WithDecryption=False)
        missing.extend(response.get("InvalidParameters", []))
        insecure.extend(
            parameter["Name"]
            for parameter in response.get("Parameters", [])
            if parameter.get("Type") != "SecureString"
        )
    if missing:
        raise ValueError(f"SSM parameters were not found: {', '.join(missing)}")
    if insecure:
        raise ValueError(f"SSM parameters must be SecureString: {', '.join(insecure)}")
    print(f"ssm_parameters=present count={len(names)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="eu-north-1")
    parser.add_argument("--lambda-api-image", required=True)
    parser.add_argument("--cpu-worker-image")
    parser.add_argument("--gpu-worker-image")
    parser.add_argument("--ssm-parameter", action="append", default=[])
    args = parser.parse_args()
    client = boto3.client("ecr", region_name=args.region)
    try:
        _verify_image(client, args.lambda_api_image, region=args.region, label="lambda_api_image", required_tag_prefix="lambda-")
        if args.cpu_worker_image:
            _verify_image(client, args.cpu_worker_image, region=args.region, label="cpu_worker_image")
        if args.gpu_worker_image:
            _verify_image(client, args.gpu_worker_image, region=args.region, label="gpu_worker_image")
        if args.ssm_parameter:
            _verify_parameters(boto3.client("ssm", region_name=args.region), args.ssm_parameter)
    except (ValueError, client.exceptions.RepositoryNotFoundException, client.exceptions.ImageNotFoundException) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("serverless_artifacts=ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
