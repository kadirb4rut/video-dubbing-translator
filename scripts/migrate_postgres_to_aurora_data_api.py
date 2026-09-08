"""Copy application rows from PostgreSQL into an Aurora Data API database.

This is a one-shot migration helper for Aurora Express, where a normal
``pg_restore`` TCP connection is not available.  The source URL is injected
by ECS from the retained legacy database secret.  The target credential is
read at runtime from Secrets Manager and is never printed.

The target must already contain the application schema and must be empty apart
from ``alembic_version``.  The helper aborts before writing if it finds target
rows, then verifies source/target row counts after the copy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from datetime import time as datetime_time
from decimal import Decimal
from typing import Any
from uuid import UUID

import boto3
import psycopg
from botocore.exceptions import ClientError

APP_SCHEMA = "public"
EXCLUDED_TABLES = {"alembic_version"}
BATCH_SIZE = 50


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _source_url() -> str:
    value = os.environ.get("SOURCE_DATABASE_URL", "")
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    return value


def _target_config() -> tuple[Any, str, str, str]:
    secret_arn = os.environ.get("TARGET_AURORA_SECRET_ARN", "")
    cluster_arn = os.environ.get("TARGET_AURORA_CLUSTER_ARN", "")
    database = os.environ.get("TARGET_DATABASE_NAME", "lingowave")
    if not secret_arn or not cluster_arn:
        raise RuntimeError("TARGET_AURORA_SECRET_ARN and TARGET_AURORA_CLUSTER_ARN are required")
    payload = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
    secret = json.loads(payload)
    return boto3.client("rds-data"), cluster_arn, secret_arn, database or secret.get("dbname", "lingowave")


def _source_metadata(connection: psycopg.Connection[Any]) -> tuple[list[str], dict[str, list[str]], dict[str, set[str]]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (APP_SCHEMA,),
        )
        tables = [row[0] for row in cursor.fetchall() if row[0] not in EXCLUDED_TABLES]

        cursor.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
            """,
            (APP_SCHEMA,),
        )
        columns: dict[str, list[str]] = defaultdict(list)
        for table_name, column_name in cursor.fetchall():
            if table_name in tables:
                columns[table_name].append(column_name)

        cursor.execute(
            """
            SELECT table_name, foreign_table_name
            FROM (
                SELECT
                    tc.table_name,
                    ccu.table_name AS foreign_table_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.constraint_schema = tc.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = %s
            ) AS foreign_keys
            """,
            (APP_SCHEMA,),
        )
        dependencies: dict[str, set[str]] = defaultdict(set)
        table_set = set(tables)
        for table_name, foreign_table_name in cursor.fetchall():
            if table_name in table_set and foreign_table_name in table_set and table_name != foreign_table_name:
                dependencies[table_name].add(foreign_table_name)
    return tables, dict(columns), dict(dependencies)


def _ordered_tables(tables: list[str], dependencies: dict[str, set[str]]) -> list[str]:
    pending = {table: set(dependencies.get(table, set())) for table in tables}
    ordered: list[str] = []
    while pending:
        ready = sorted(table for table, deps in pending.items() if not deps)
        if not ready:
            cycle = ", ".join(sorted(pending))
            raise RuntimeError(f"foreign-key cycle prevents safe copy: {cycle}")
        ordered.extend(ready)
        for table in ready:
            pending.pop(table)
        for deps in pending.values():
            deps.difference_update(ready)
    return ordered


def _data_api_value(name: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    if isinstance(value, bool):
        return {"name": name, "value": {"booleanValue": value}}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"name": name, "value": {"longValue": value}}
    if isinstance(value, float):
        return {"name": name, "value": {"doubleValue": value}}
    if isinstance(value, datetime):
        normalized = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
        return {"name": name, "value": {"stringValue": normalized.isoformat(sep=" ")}, "typeHint": "TIMESTAMP"}
    if isinstance(value, date):
        return {"name": name, "value": {"stringValue": value.isoformat()}, "typeHint": "DATE"}
    if isinstance(value, datetime_time):
        normalized = value.replace(tzinfo=None) if value.tzinfo else value
        return {"name": name, "value": {"stringValue": normalized.isoformat()}, "typeHint": "TIME"}
    if isinstance(value, Decimal):
        return {"name": name, "value": {"stringValue": str(value)}, "typeHint": "DECIMAL"}
    if isinstance(value, (dict, list)):
        return {"name": name, "value": {"stringValue": json.dumps(value, ensure_ascii=False)}, "typeHint": "JSON"}
    if isinstance(value, (UUID, bytes, bytearray, memoryview)):
        if isinstance(value, UUID):
            return {"name": name, "value": {"stringValue": str(value)}}
        return {"name": name, "value": {"blobValue": bytes(value)}}
    return {"name": name, "value": {"stringValue": str(value)}}


def _target_statement(client: Any, cluster_arn: str, secret_arn: str, database: str, sql: str) -> list[Any]:
    for attempt in range(12):
        try:
            response = client.execute_statement(
                resourceArn=cluster_arn,
                secretArn=secret_arn,
                database=database,
                sql=sql,
                includeResultMetadata=False,
            )
            return response.get("records", [])
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code not in {"HttpEndpointNotEnabledException", "DatabaseResumingException"} or attempt == 11:
                raise
            time.sleep(min(10, 2 + attempt))
    raise RuntimeError("Aurora Data API statement retry loop exited unexpectedly")


def _target_count(client: Any, cluster_arn: str, secret_arn: str, database: str, table_name: str) -> int:
    records = _target_statement(
        client,
        cluster_arn,
        secret_arn,
        database,
        f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}",
    )
    value = records[0][0]
    return int(value.get("longValue", value.get("stringValue", 0)))


def _copy_table(
    source: psycopg.Connection[Any],
    target: tuple[Any, str, str, str],
    table_name: str,
    columns: list[str],
) -> int:
    client, cluster_arn, secret_arn, database = target
    quoted_table = _quote_identifier(table_name)
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join(f":p{index}" for index in range(len(columns)))
    sql = f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})"
    total = 0
    with source.cursor(name=f"lingowave_migrate_{table_name}") as cursor:
        cursor.execute(f"SELECT {quoted_columns} FROM {quoted_table}")
        while rows := cursor.fetchmany(BATCH_SIZE):
            parameter_sets = [[_data_api_value(f"p{index}", value) for index, value in enumerate(row)] for row in rows]
            client.batch_execute_statement(
                resourceArn=cluster_arn,
                secretArn=secret_arn,
                database=database,
                sql=sql,
                parameterSets=parameter_sets,
            )
            total += len(rows)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-connect-timeout", type=int, default=15)
    args = parser.parse_args()
    source_url = _source_url()
    if not source_url:
        print("SOURCE_DATABASE_URL is required", file=sys.stderr)
        return 2

    started = time.monotonic()
    target = _target_config()
    client, cluster_arn, secret_arn, database = target
    with psycopg.connect(source_url, connect_timeout=args.source_connect_timeout) as source:
        tables, columns, dependencies = _source_metadata(source)
        ordered = _ordered_tables(tables, dependencies)
        print(json.dumps({"phase": "preflight", "tables": len(ordered), "order": ordered}))
        for table_name in ordered:
            count = _target_count(client, cluster_arn, secret_arn, database, table_name)
            if count:
                raise RuntimeError(f"target table is not empty: {table_name} ({count} rows)")

        copied: dict[str, int] = {}
        for table_name in ordered:
            copied[table_name] = _copy_table(source, target, table_name, columns[table_name])
            print(json.dumps({"phase": "copy", "table": table_name, "rows": copied[table_name]}))

        source_counts: dict[str, int] = {}
        with source.cursor() as cursor:
            for table_name in ordered:
                cursor.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}")
                source_counts[table_name] = cursor.fetchone()[0]
        target_counts = {table_name: _target_count(client, cluster_arn, secret_arn, database, table_name) for table_name in ordered}

    mismatches = {
        table: {"source": source_counts[table], "target": target_counts[table]}
        for table in ordered
        if source_counts[table] != target_counts[table]
    }
    result = {
        "status": "ok" if not mismatches else "mismatch",
        "tables": len(ordered),
        "rows": sum(target_counts.values()),
        "mismatches": mismatches,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
