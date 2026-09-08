"""Schema helpers compatible with PostgreSQL and the Aurora Data API."""

from collections.abc import Mapping

import sqlalchemy as sa


def table_columns(bind, table_name: str) -> dict[str, Mapping[str, object]]:
    """Return column metadata without PostgreSQL catalog CHAR fields."""

    if bind.dialect.name == "sqlite":
        safe_table_name = table_name.replace('"', '""')
        rows = bind.execute(sa.text(f'PRAGMA table_info("{safe_table_name}")'))
        return {
            row[1]: {
                "name": row[1],
                "nullable": row[3] == 0,
            }
            for row in rows
        }

    rows = bind.execute(
        sa.text(
            """
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return {
        row.column_name: {
            "name": row.column_name,
            "nullable": row.is_nullable == "YES",
        }
        for row in rows
    }


def table_names(bind) -> set[str]:
    """Return application-visible table names from information_schema."""

    if bind.dialect.name == "sqlite":
        rows = bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type = 'table'"))
        return {row[0] for row in rows}

    rows = bind.execute(
        sa.text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = current_schema()
            """
        )
    )
    return {row.table_name for row in rows}


def index_names(bind, table_name: str) -> set[str]:
    """Return index names without PostgreSQL catalog CHAR fields."""

    if bind.dialect.name == "sqlite":
        safe_table_name = table_name.replace('"', '""')
        rows = bind.execute(sa.text(f'PRAGMA index_list("{safe_table_name}")'))
        return {row[1] for row in rows}

    rows = bind.execute(
        sa.text(
            """
            SELECT indexname
            FROM pg_catalog.pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return {row.indexname for row in rows}
