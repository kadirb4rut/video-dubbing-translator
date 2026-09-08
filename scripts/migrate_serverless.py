"""Run the checked-in Alembic head through the on-demand Lambda migration function."""

from __future__ import annotations

import json
import os
import sys

import boto3


def main() -> int:
    function_name = os.environ.get("SERVERLESS_MIGRATION_FUNCTION")
    region = os.environ.get("AWS_REGION", "eu-north-1")
    if not function_name:
        print("SERVERLESS_MIGRATION_FUNCTION is required", file=sys.stderr)
        return 2
    response = boto3.client("lambda", region_name=region).invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"action": "upgrade", "revision": "head"}).encode(),
    )
    payload = response["Payload"].read().decode()
    if response.get("FunctionError"):
        print(payload, file=sys.stderr)
        return 1
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
