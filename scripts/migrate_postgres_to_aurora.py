#!/usr/bin/env python3
"""Copy PostgreSQL data from the retained legacy RDS source into Aurora.

Run this from a temporary host/task that can reach both private PostgreSQL
endpoints. Connection URLs are read from environment variables so passwords do
not appear in the command line or in Terraform variables. The default mode is
non-destructive and is intended for an empty Aurora cluster; ``--clean`` must
be explicitly selected before replacing existing target tables.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


def _connection_env(raw_url: str) -> dict[str, str]:
    parsed = urlparse(raw_url.replace("postgresql+psycopg://", "postgresql://", 1))
    if parsed.scheme != "postgresql" or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("Database URLs must be normal PostgreSQL URLs with a host and database name")
    env = {
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGUSER": unquote(parsed.username or ""),
        "PGPASSWORD": unquote(parsed.password or ""),
        "PGDATABASE": parsed.path.lstrip("/"),
    }
    query = parse_qs(parsed.query)
    if query.get("sslmode"):
        env["PGSSLMODE"] = query["sslmode"][0]
    return env


def _run(command: list[str], *, env: dict[str, str]) -> None:
    print("running=" + " ".join(command[:2]))
    subprocess.run(command, check=True, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump-file", type=Path, default=Path("/tmp/lingowave-rds-migration.dump"))
    parser.add_argument("--clean", action="store_true", help="Drop target objects before restore; use only after an explicit target backup.")
    args = parser.parse_args()

    source_url = os.getenv("SOURCE_DATABASE_URL", "")
    target_url = os.getenv("TARGET_DATABASE_URL", "")
    if not source_url or not target_url:
        print("SOURCE_DATABASE_URL and TARGET_DATABASE_URL are required", file=sys.stderr)
        return 2
    try:
        source_env = _connection_env(source_url)
        target_env = _connection_env(target_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    args.dump_file.parent.mkdir(parents=True, exist_ok=True)
    dump_command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        str(args.dump_file),
    ]
    _run(dump_command, env={**os.environ, **source_env})

    restore_command = ["pg_restore", "--no-owner", "--no-acl", "--exit-on-error"]
    if args.clean:
        restore_command.extend(["--clean", "--if-exists"])
    restore_command.extend(["--dbname", target_env["PGDATABASE"], str(args.dump_file)])
    _run(restore_command, env={**os.environ, **target_env})
    print(f"migration_complete dump={args.dump_file} clean={args.clean}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
