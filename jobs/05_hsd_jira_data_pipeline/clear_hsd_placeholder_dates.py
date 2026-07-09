"""Clear placeholder HSD dates (future sentinel) in ips_jira_bugs.

Usage:
    python clear_hsd_placeholder_dates.py --table ips_jira_bugs --cutoff 2027-01-01

Defaults assume the same Postgres credentials as the dashboard scripts (.env keys DB_NAME/DB_USER/DB_PASS/DB_HOST/DB_PORT).
"""

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, date

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env() -> None:
    if load_dotenv is None:
        return
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


def _conn_params() -> dict:
    return {
        "database": (os.getenv("DB_NAME") or "").strip(),
        "user": (os.getenv("DB_USER") or "").strip(),
        "password": (os.getenv("DB_PASS") or "").strip(),
        "host": (os.getenv("DB_HOST") or "").strip(),
        "port": (os.getenv("DB_PORT") or "5433").strip(),
    }


def _validate_params(params: dict) -> None:
    missing = [k for k, v in params.items() if not v]
    if missing:
        raise SystemExit(f"Missing DB params in environment: {', '.join(missing)}")


def _validate_table_name(name: str) -> None:
    if not re.match(r"^[a-zA-Z0-9_.]+$", name):
        raise SystemExit(f"Unsafe table name: {name}")


def _split_table(name: str) -> tuple[str, str]:
    if "." in name:
        schema, tbl = name.rsplit(".", 1)
    else:
        schema, tbl = "public", name
    return schema, tbl


def _existing_date_cols(conn, schema: str, table: str) -> list[str]:
    sql = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
          AND column_name IN ('hsd_submitted_date', 'hsd_closed_date');
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (schema, table))
        return [row["column_name"] for row in cur.fetchall()]


def clear_placeholder_dates(table: str, cutoff: datetime) -> int:
    params = _conn_params()
    _validate_params(params)
    _validate_table_name(table)

    schema, tbl = _split_table(table)

    with psycopg2.connect(**params) as conn:
        cols = _existing_date_cols(conn, schema, tbl)
        if not cols:
            raise SystemExit(f"No target date columns found on {table}; expected hsd_submitted_date or hsd_closed_date.")

        set_parts = [f"{col} = NULL" for col in cols]
        where_parts = [f"{col} >= %s" for col in cols]
        sql = f"UPDATE {schema}.{tbl} SET {', '.join(set_parts)} WHERE {' OR '.join(where_parts)};"

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, tuple([cutoff] * len(where_parts)))
            return cur.rowcount or 0


def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(description="Clear placeholder HSD dates in Postgres table.")
    parser.add_argument("--table", default="ips_jira_bugs", help="Target table name (default: ips_jira_bugs)")
    parser.add_argument(
        "--cutoff",
        default=None,
        help="Cutoff date (YYYY-MM-DD). Rows with dates >= cutoff will be nulled. Default is Jan 1 of next year.",
    )
    args = parser.parse_args()

    if args.cutoff:
        cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d")
    else:
        cutoff = datetime(year=date.today().year + 1, month=1, day=1)

    updated = clear_placeholder_dates(args.table, cutoff)
    print(f"Cleared {updated} row(s) in {args.table} where date >= {cutoff.date()}.")


if __name__ == "__main__":
    main()
