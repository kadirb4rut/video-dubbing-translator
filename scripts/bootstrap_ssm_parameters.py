#!/usr/bin/env python3
"""Create SSM Parameter Store Standard SecureStrings from environment variables.

The command deliberately accepts only parameter names and environment-variable
names. Secret values are read at runtime, sent directly to SSM, and never
printed or written to Terraform state, files, or command arguments.

Example:
    AWS_PROFILE=lingowave python scripts/bootstrap_ssm_parameters.py \
      --region eu-north-1 \
      --parameter GOOGLE_CLIENT_ID=/lingowave/production/google-client-id \
      --parameter GOOGLE_CLIENT_SECRET=/lingowave/production/google-client-secret \
      --parameter DATABASE_URL=/lingowave/production/database-url \
      --overwrite
"""

from __future__ import annotations

import argparse
import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def _parse_mapping(raw: str) -> tuple[str, str]:
    """Parse ``ENVIRONMENT_VARIABLE=SSM_PARAMETER_NAME`` without exposing values."""

    environment_name, separator, parameter_name = raw.partition("=")
    if not separator or not environment_name or not parameter_name:
        raise ValueError(f"invalid --parameter mapping {raw!r}; expected ENV_VAR=/parameter/name")
    if not environment_name.replace("_", "").isalnum() or not environment_name[0].isalpha():
        raise ValueError(f"invalid environment variable name in mapping {raw!r}")
    if not parameter_name.startswith("/"):
        raise ValueError(f"SSM parameter names must start with /: {parameter_name!r}")
    if len(parameter_name) > 1011:
        raise ValueError(f"SSM parameter name is too long: {parameter_name!r}")
    return environment_name, parameter_name


def _put_parameters(client, mappings: list[tuple[str, str]], *, overwrite: bool) -> None:
    for environment_name, parameter_name in mappings:
        value = os.environ.get(environment_name)
        if value is None:
            raise ValueError(f"required environment variable is not set: {environment_name}")
        if not value:
            raise ValueError(f"required environment variable is empty: {environment_name}")
        if len(value.encode("utf-8")) > 4096:
            raise ValueError(f"SSM Standard value exceeds 4 KiB: {environment_name}")
        client.put_parameter(
            Name=parameter_name,
            Value=value,
            Type="SecureString",
            Tier="Standard",
            Overwrite=overwrite,
        )
        print(f"stored {environment_name} -> {parameter_name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-north-1"))
    parser.add_argument(
        "--parameter",
        action="append",
        required=True,
        metavar="ENV_VAR=/parameter/name",
        help="Map an existing environment variable to an SSM Standard parameter.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing values. Without this flag, an existing parameter fails safely.",
    )
    args = parser.parse_args()

    try:
        mappings = [_parse_mapping(raw) for raw in args.parameter]
        if len({parameter_name for _, parameter_name in mappings}) != len(mappings):
            raise ValueError("each SSM parameter name may appear only once")
        _put_parameters(boto3.client("ssm", region_name=args.region), mappings, overwrite=args.overwrite)
    except (ValueError, BotoCoreError, ClientError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
