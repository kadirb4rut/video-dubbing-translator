#!/usr/bin/env python3
"""Copy selected JSON keys from Secrets Manager into SSM SecureStrings.

Secret values are kept in memory and sent directly to SSM.  The command only
prints parameter names and never writes secret material to stdout, Terraform
state, files, or command arguments.

Example:
    python scripts/bootstrap_ssm_from_secret.py \
      --region eu-north-1 \
      --secret-id lingowave/production/google-oauth \
      --mapping GOOGLE_CLIENT_ID=/lingowave/production/google-client-id \
      --mapping GOOGLE_CLIENT_SECRET=/lingowave/production/google-client-secret \
      --mapping GOOGLE_REDIRECT_URI=/lingowave/production/google-redirect-uri
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def _parse_mapping(raw: str) -> tuple[str, str]:
    key, separator, parameter_name = raw.partition("=")
    if not separator or not key or not parameter_name:
        raise ValueError(f"invalid --mapping {raw!r}; expected JSON_KEY=/parameter/name")
    if not key.replace("_", "").isalnum() or not key[0].isalpha():
        raise ValueError(f"invalid JSON key in mapping {raw!r}")
    if not parameter_name.startswith("/"):
        raise ValueError(f"SSM parameter names must start with /: {parameter_name!r}")
    if len(parameter_name) > 1011:
        raise ValueError(f"SSM parameter name is too long: {parameter_name!r}")
    return key, parameter_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="eu-north-1")
    parser.add_argument("--secret-id", required=True)
    parser.add_argument("--mapping", action="append", required=True, metavar="JSON_KEY=/parameter/name")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        mappings = [_parse_mapping(raw) for raw in args.mapping]
        if len({parameter for _, parameter in mappings}) != len(mappings):
            raise ValueError("each SSM parameter name may appear only once")

        secrets = boto3.client("secretsmanager", region_name=args.region)
        ssm = boto3.client("ssm", region_name=args.region)
        response = secrets.get_secret_value(SecretId=args.secret_id)
        raw_secret = response.get("SecretString")
        if raw_secret is None:
            raise ValueError("the selected secret does not contain SecretString JSON")
        document = json.loads(raw_secret)
        if not isinstance(document, dict):
            raise TypeError("the selected secret JSON must be an object")

        for key, parameter_name in mappings:
            value = document.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError(f"required JSON key is missing or empty: {key}")
            if len(value.encode("utf-8")) > 4096:
                raise ValueError(f"SSM Standard value exceeds 4 KiB: {key}")
            ssm.put_parameter(
                Name=parameter_name,
                Value=value,
                Type="SecureString",
                Tier="Standard",
                Overwrite=args.overwrite,
            )
            print(f"stored {key} -> {parameter_name}")
    except (TypeError, ValueError, json.JSONDecodeError, BotoCoreError, ClientError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
