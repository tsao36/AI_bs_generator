"""Create semantic views in Postgres using the repo SQL file."""
from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict

import psycopg2
from psycopg2 import errors

# align with Wireless_bug_dashboard.py DB config
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "APIs"))
import Sherlock  # type: ignore


def _get_db_params() -> Dict[str, Any]:
    return {
        "database": Sherlock.PostgresCustomerEngineeringDb.database,
        "user": Sherlock.PostgresCustomerEngineeringDb.user,
        "password": Sherlock.PostgresCustomerEngineeringDb.password,
        "host": Sherlock.PostgresCustomerEngineeringDb.host,
        "port": Sherlock.PostgresCustomerEngineeringDb.port,
    }


def _column_exists(conn: Any, schema: str, table: str, column: str) -> bool:
    query = """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
          AND column_name = %s
        LIMIT 1;
    """
    with conn.cursor() as cur:
        cur.execute(query, (schema, table, column))
        return cur.fetchone() is not None


def _build_view_sql(conn: Any, *, schema: str, table: str, view: str) -> str:
    schema = schema or "public"
    schema = schema.lower()
    view = view or "vw_issues"
    table = table or "ips_jira_bugs"

    def _valid(name: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_]+$", name):
            raise SystemExit(f"Invalid identifier: {name}")
        return name

    schema_q = _valid(schema)
    table_q = _valid(table)
    view_q = _valid(view)

    def col_or_null(col: str, alias: str | None = None, cast_date: bool = False) -> str:
        alias = alias or col
        if _column_exists(conn, schema_q, table_q, col):
            return f"{col} AS {alias}" if alias != col else col
        return f"NULL::date AS {alias}" if cast_date else f"NULL::text AS {alias}"

    platform_candidates = [c for c in ["ips_platform", "jira_platform", "hsd_platform"] if _column_exists(conn, schema_q, table_q, c)]
    platform_expr = (
        "COALESCE(" + ", ".join(platform_candidates) + ", 'NA') AS platform"
        if platform_candidates
        else "'NA' AS platform"
    )

    created_expr = None
    if _column_exists(conn, schema_q, table_q, "bug_created_date"):
        created_expr = "bug_created_date"
    elif _column_exists(conn, schema_q, table_q, "ips_created_date"):
        created_expr = "ips_created_date AS bug_created_date"
    else:
        created_expr = "NULL::date AS bug_created_date"

    cfe_team_candidates = [
        c
        for c in ["CFE_Team", "engineer", "reporter"]
        if _column_exists(conn, schema_q, table_q, c)
    ]
    if cfe_team_candidates:
        cfe_team_expr = (
            "COALESCE("
            + ", ".join(f"NULLIF(TRIM(COALESCE({c}::text, '')), 'NA')" for c in cfe_team_candidates)
            + ", 'NA') AS cfe_team"
        )
    else:
        cfe_team_expr = "'NA' AS cfe_team"

    select_parts = [
        col_or_null("ips_case_number"),
        col_or_null("ips_title"),
        col_or_null("ips_status"),
        col_or_null("ips_sub_status"),
        col_or_null("ips_created_date", cast_date=True),
        col_or_null("ips_last_modified_date", cast_date=True),
        col_or_null("ips_last_modified_days"),
        col_or_null("ips_open_days"),
        col_or_null("reporter"),
        col_or_null("jira_id"),
        col_or_null("jira_title"),
        col_or_null("jira_status"),
        col_or_null("bug_project"),
        platform_expr,
        col_or_null("bug_category_custom"),
        col_or_null("bug_criticality_custom"),
        col_or_null("bug_status_custom"),
        cfe_team_expr,
        col_or_null("bug_origin"),
        col_or_null("bug_created_year"),
        col_or_null("jira_found_by"),
        col_or_null("customer_custom"),
        col_or_null("bug_closed_date", cast_date=True),
        created_expr,
        col_or_null("is_ips_promoted_to_jira"),
        col_or_null("ips_jira_promo_status"),
        col_or_null("jira_final_component"),
        col_or_null("jira_state_reason"),
    ]

    select_clause = ",\n    ".join(select_parts)
    return (
        f"CREATE OR REPLACE VIEW {schema_q}.{view_q} AS\n"
        f"SELECT\n    {select_clause}\n"
        f"FROM {schema_q}.{table_q};"
    )


def main() -> int:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    sql_path = os.path.join(base_dir, "schema", "semantic_views.sql")
    sql_from_file = None
    if os.path.exists(sql_path):
        with open(sql_path, "r", encoding="utf-8") as fh:
            sql_from_file = fh.read()

    params = _get_db_params()
    missing = [k for k, v in params.items() if not v]
    if missing:
        raise SystemExit(f"Missing DB params in Sherlock config: {missing}")

    source_table = os.getenv("DB_TABLE_SRC", "ips_jira_bugs")
    source_schema = os.getenv("DB_SCHEMA", "public")
    view_name = os.getenv("DB_VIEW_NAME", "vw_issues")

    with psycopg2.connect(**params) as conn:
        try:
            with conn.cursor() as cur:
                try:
                    if sql_from_file:
                        cur.execute(sql_from_file)
                    else:
                        raise errors.UndefinedColumn("semantic_views.sql not available; building dynamic view")
                except errors.UndefinedColumn:
                    conn.rollback()
                    dynamic_sql = _build_view_sql(conn, schema=source_schema, table=source_table, view=view_name)
                    with conn.cursor() as cur2:
                        cur2.execute(f"DROP VIEW IF EXISTS {source_schema}.{view_name}")
                        cur2.execute(dynamic_sql)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    print("Semantic view created/updated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
