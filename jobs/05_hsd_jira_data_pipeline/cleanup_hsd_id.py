"""Cleanup HSD columns for a specific HSD ID in ips_jira_bugs.

Usage:
  python cleanup_hsd_id.py --hsd-id 22020576427
  python cleanup_hsd_id.py --hsd-id 22020576427 --table ips_jira_bugs
"""
from __future__ import annotations

import argparse
import logging
import sys

import psycopg2

from APIs import Sherlock

LOG = logging.getLogger("cleanup_hsd_id")


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    LOG.addHandler(handler)


def _connect():
    return psycopg2.connect(
        database=Sherlock.PostgresCustomerEngineeringDb.database,
        user=Sherlock.PostgresCustomerEngineeringDb.user,
        password=Sherlock.PostgresCustomerEngineeringDb.password,
        host=Sherlock.PostgresCustomerEngineeringDb.host,
        port=Sherlock.PostgresCustomerEngineeringDb.port,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear HSD columns for a specific HSD ID.")
    parser.add_argument("--hsd-id", required=True, help="HSD ID to clear (exact match)")
    parser.add_argument("--table", default="ips_jira_bugs", help="Target table (default: ips_jira_bugs)")
    args = parser.parse_args()

    _setup_logging()

    update_sql = f'''
        UPDATE "{args.table}"
        SET
            hsd_id = NULL,
            hsd_promoted_id = NULL,
            hsd_status_reason = NULL,
            hsd_customer_detail = NULL,
            hsd_owner = NULL,
            hsd_title = NULL,
            hsd_submitted_date = NULL,
            hsd_updated_date = NULL,
            hsd_platform = NULL
        WHERE hsd_id = %s
    '''

    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(update_sql, (args.hsd_id,))
                LOG.info("Rows updated: %s", cursor.rowcount)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
