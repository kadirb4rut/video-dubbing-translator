"""Verify that an Aurora PostgreSQL engine version is orderable in a region.

Aurora's zero-ACU auto-pause support is engine-version dependent. AWS documents
PostgreSQL 16.3 and later as eligible, but the selected minor version must also
be available for ``db.serverless`` in the target region. Run this before the
first serverless Terraform plan or after changing ``aurora_engine_version``.
"""

from __future__ import annotations

import argparse
import sys

import boto3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="eu-north-1")
    parser.add_argument("--engine-version", default="16.8")
    args = parser.parse_args()

    major, minor, *_ = (int(part) for part in args.engine_version.split("."))
    if (major, minor) < (16, 3):
        print("Aurora PostgreSQL zero-ACU auto-pause requires version 16.3 or newer.", file=sys.stderr)
        return 2

    client = boto3.client("rds", region_name=args.region)
    versions = client.describe_db_engine_versions(
        Engine="aurora-postgresql",
        EngineVersion=args.engine_version,
        IncludeAll=True,
    ).get("DBEngineVersions", [])
    if not versions:
        print(f"{args.engine_version} is not available for aurora-postgresql in {args.region}.", file=sys.stderr)
        return 1

    orderable = client.describe_orderable_db_instance_options(
        Engine="aurora-postgresql",
        EngineVersion=args.engine_version,
        DBInstanceClass="db.serverless",
        MaxRecords=100,
    ).get("OrderableDBInstanceOptions", [])
    if not orderable:
        print(f"{args.engine_version} is not orderable as db.serverless in {args.region}.", file=sys.stderr)
        return 1

    print(f"Aurora PostgreSQL {args.engine_version} is available as db.serverless in {args.region}; zero-ACU prerequisites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
