from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

from Wireless_bug_dashboard import DbConnector, normalize_ips_owner_reporter


def parse_dt(text: str) -> datetime | None:
    value = (text or "").strip()
    if not value:
        return None
    for fmt in (
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def jira_from_backend(value: str) -> str:
    match = re.search(r"([A-Za-z]+-\d+)", (value or "").strip())
    return match.group(1).upper() if match else ""


def clean(value: Any) -> str:
    return str(value or "").strip().replace("\r", " ").replace("\n", " ")


def build_records(
    csv_path: str,
    start_year: int,
    end_year: int,
) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]], int]:
    by_jira: dict[str, dict[str, Any]] = {}
    by_case: dict[int, dict[str, Any]] = {}
    missing_backend_rows = 0

    with open(csv_path, "r", encoding="cp1252", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        header_lookup = {h.strip().lower(): h for h in headers if h}

        def pick_header(*candidates: str) -> str:
            for candidate in candidates:
                key = candidate.strip().lower()
                if key in header_lookup:
                    return header_lookup[key]
            return ""

        opened_col = pick_header("Date/Time Opened", "date/time opened", "Date Time Opened", "opened")
        closed_col = pick_header("Closed Date", "closed date", "Date/Time Closed", "date/time closed", "closed")
        owner_col = "Case Owner" if "Case Owner" in headers else ""
        product_col = ""
        for candidate in ("Product", "Products", "Platform", "Platforms", "Product Family"):
            if candidate in headers:
                product_col = candidate
                break
        env_details_col = ""
        for candidate in ("Environment Detail Answer", "Environment Details", "Environment Detail", "Env Details"):
            if candidate in headers:
                env_details_col = candidate
                break
        print("owner column:", owner_col or "(not found)")
        print("opened date column:", opened_col or "(not found)")
        print("closed date column:", closed_col or "(not found)")
        print("product column:", product_col or "(not found)")
        print("env details column:", env_details_col or "(not found)")

        def should_replace(prev: dict[str, Any] | None, curr: dict[str, Any]) -> bool:
            if prev is None:
                return True
            prev_has_closed = prev.get("ips_closed_date") is not None
            curr_has_closed = curr.get("ips_closed_date") is not None
            if curr_has_closed and not prev_has_closed:
                return True
            if prev_has_closed and not curr_has_closed:
                return False
            return curr["ips_created_date"] >= prev["ips_created_date"]

        for row in reader:
            opened = parse_dt(row.get(opened_col, "")) if opened_col else None
            closed = parse_dt(row.get(closed_col, "")) if closed_col else None
            if not opened or opened.year < start_year or opened.year > end_year:
                continue

            jira_id = jira_from_backend(row.get("Backend ID", ""))
            case_text = clean(row.get("Case Number", ""))
            if not case_text:
                continue

            try:
                case_number = int(case_text)
            except ValueError:
                continue

            if not jira_id:
                missing_backend_rows += 1

            record = {
                "ips_case_number": case_number,
                "ips_title": clean(row.get("Subject", "")),
                "ips_created_date": opened,
                "ips_closed_date": closed,
                "ips_category": clean(row.get("Case Subcategory", "")),
                "ips_oem": clean(row.get("Account Name", "")),
                "ips_owner_name": clean(row.get(owner_col, "")) if owner_col else "",
                "ips_product": clean(row.get(product_col, "")) if product_col else "",
                "ips_env_details": clean(row.get(env_details_col, "")) if env_details_col else "",
                "ips_jira_id": jira_id or "NA",
                "jira_id": jira_id,
                "reporter": normalize_ips_owner_reporter(clean(row.get(owner_col, "")) if owner_col else ""),
            }

            prev_case = by_case.get(case_number)
            if should_replace(prev_case, record):
                by_case[case_number] = record

            if jira_id:
                prev = by_jira.get(jira_id)
                if should_replace(prev, record):
                    by_jira[jira_id] = record

    return by_jira, by_case, missing_backend_rows


def _get_table_columns(cur: Any, table_name: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        """,
        (table_name,),
    )
    return {row[0] for row in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill IPS columns from IPS_data_exported.csv")
    parser.add_argument("--csv-path", default="IPS_data_exported.csv")
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int, default=2022)
    parser.add_argument("--owner-refresh-empty", action="store_true", help="Also fill ips_owner_name when DB owner is empty/NA")
    parser.add_argument("--dates-only", action="store_true", help="Only sync ips_created_date / ips_closed_date by ips_case_number")
    args = parser.parse_args()

    jira_records, case_records, missing_backend_rows = build_records(args.csv_path, args.start_year, args.end_year)
    print("csv rows by case:", len(case_records))
    print("csv jira candidates:", len(jira_records))
    print("csv rows with empty backend_id:", missing_backend_rows)
    if not case_records:
        return 0

    db = DbConnector()
    cur = db._DbConnector__cursor
    conn = db._DbConnector__connection

    if args.dates_only:
        # In date-only mode, keep each statement isolated and fail fast on lock waits.
        conn.rollback()
        cur.execute("SET lock_timeout TO '200ms'")
        cur.execute("SET statement_timeout TO '120s'")

    table_columns = _get_table_columns(cur, "ips_jira_bugs")
    if "ips_closed_date" not in table_columns:
        cur.execute("ALTER TABLE ips_jira_bugs ADD COLUMN IF NOT EXISTS ips_closed_date TIMESTAMP")
        conn.commit()
        table_columns = _get_table_columns(cur, "ips_jira_bugs")
    if "ips_product" not in table_columns:
        cur.execute("ALTER TABLE ips_jira_bugs ADD COLUMN IF NOT EXISTS ips_product TEXT")
        conn.commit()
        table_columns = _get_table_columns(cur, "ips_jira_bugs")
    if "ips_env_details" not in table_columns:
        cur.execute("ALTER TABLE ips_jira_bugs ADD COLUMN IF NOT EXISTS ips_env_details TEXT")
        conn.commit()
        table_columns = _get_table_columns(cur, "ips_jira_bugs")

    if args.dates_only:
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS tmp_ips_case_dates (
                ips_case_number BIGINT PRIMARY KEY,
                ips_created_date TIMESTAMP,
                ips_closed_date TIMESTAMP
            ) ON COMMIT DROP
            """
        )
        cur.execute("TRUNCATE tmp_ips_case_dates")

        temp_rows = [
            (
                int(case_number),
                rec.get("ips_created_date"),
                rec.get("ips_closed_date"),
            )
            for case_number, rec in case_records.items()
        ]

        execute_values(
            cur,
            """
            INSERT INTO tmp_ips_case_dates (ips_case_number, ips_created_date, ips_closed_date)
            VALUES %s
            ON CONFLICT (ips_case_number)
            DO UPDATE SET
                ips_created_date = EXCLUDED.ips_created_date,
                ips_closed_date = COALESCE(EXCLUDED.ips_closed_date, tmp_ips_case_dates.ips_closed_date)
            """,
            temp_rows,
            page_size=1000,
        )

        cur.execute(
            """
            UPDATE ips_jira_bugs AS t
            SET ips_created_date = s.ips_created_date,
                ips_closed_date = COALESCE(s.ips_closed_date, t.ips_closed_date)
            FROM tmp_ips_case_dates AS s
            WHERE t.ips_case_number = s.ips_case_number
              AND (
                  t.ips_created_date IS DISTINCT FROM s.ips_created_date
                  OR (s.ips_closed_date IS NOT NULL AND t.ips_closed_date IS DISTINCT FROM s.ips_closed_date)
              )
            """
        )
        case_date_synced = cur.rowcount

        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS tmp_ips_jira_dates (
                jira_id TEXT PRIMARY KEY,
                ips_created_date TIMESTAMP,
                ips_closed_date TIMESTAMP
            ) ON COMMIT DROP
            """
        )
        cur.execute("TRUNCATE tmp_ips_jira_dates")

        jira_temp_rows = [
            (
                jira_id,
                rec.get("ips_created_date"),
                rec.get("ips_closed_date"),
            )
            for jira_id, rec in jira_records.items()
            if jira_id
        ]

        if jira_temp_rows:
            execute_values(
                cur,
                """
                INSERT INTO tmp_ips_jira_dates (jira_id, ips_created_date, ips_closed_date)
                VALUES %s
                ON CONFLICT (jira_id)
                DO UPDATE SET
                    ips_created_date = EXCLUDED.ips_created_date,
                    ips_closed_date = COALESCE(EXCLUDED.ips_closed_date, tmp_ips_jira_dates.ips_closed_date)
                """,
                jira_temp_rows,
                page_size=1000,
            )

            cur.execute(
                """
                UPDATE ips_jira_bugs AS t
                SET ips_created_date = s.ips_created_date,
                    ips_closed_date = COALESCE(s.ips_closed_date, t.ips_closed_date)
                FROM tmp_ips_jira_dates AS s
                WHERE (
                        UPPER(TRIM(COALESCE(t.jira_id, ''))) = s.jira_id
                        OR UPPER(TRIM(COALESCE(t.ips_jira_id, ''))) = s.jira_id
                    )
                  AND (
                      t.ips_created_date IS DISTINCT FROM s.ips_created_date
                      OR (s.ips_closed_date IS NOT NULL AND t.ips_closed_date IS DISTINCT FROM s.ips_closed_date)
                  )
                """
            )
            case_date_synced += cur.rowcount

        conn.commit()
        print("updated rows:", 0)
        print("case date synced rows:", case_date_synced)
        print("case date lock-skipped rows:", 0)
        print("dates-only mode: skipped non-date backfill steps")
        return 0

    jira_ids = sorted(jira_records.keys())

    targets: list[str] = []
    if jira_ids:
        cur.execute(
            """
            SELECT UPPER(jira_id) AS jira_id
            FROM ips_jira_bugs
            WHERE UPPER(jira_id) = ANY(%s)
              AND jira_created_date >= %s::date
              AND jira_created_date < %s::date
              AND COALESCE(ips_case_number, 0) <= 0
            """,
            (jira_ids, f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
        )
        targets = [row[0] for row in cur.fetchall()]
    print("eligible to backfill:", len(targets))

    updated = 0
    if not args.dates_only:
        for jira_id in targets:
            rec = jira_records[jira_id]
            cur.execute(
                """
                UPDATE ips_jira_bugs
                SET ips_case_number = %s,
                    ips_title = %s,
                    ips_created_date = %s,
                    ips_closed_date = %s,
                    ips_category = %s,
                    ips_oem = %s,
                    ips_owner_name = %s,
                    ips_jira_id = %s,
                    is_ips_promoted_to_jira = TRUE
                WHERE UPPER(jira_id) = %s
                  AND jira_created_date >= %s::date
                  AND jira_created_date < %s::date
                  AND COALESCE(ips_case_number, 0) <= 0
                """,
                (
                    rec["ips_case_number"],
                    rec["ips_title"] or "NA",
                    rec["ips_created_date"],
                    rec.get("ips_closed_date"),
                    rec["ips_category"] or "NA",
                    rec["ips_oem"] or "NA",
                    rec["ips_owner_name"] or "NA",
                    rec["ips_jira_id"],
                    jira_id,
                    f"{args.start_year}-01-01",
                    f"{args.end_year + 1}-01-01",
                ),
            )
            updated += cur.rowcount

    case_date_synced = 0
    case_date_lock_skipped = 0
    case_sync_batch = 0
    for case_number, rec in case_records.items():
        try:
            cur.execute(
                """
                UPDATE ips_jira_bugs
                SET ips_created_date = %s,
                    ips_closed_date = COALESCE(%s, ips_closed_date)
                WHERE ips_case_number = %s
                  AND (
                      ips_created_date IS DISTINCT FROM %s
                      OR (%s IS NOT NULL AND ips_closed_date IS DISTINCT FROM %s)
                  )
                """,
                (
                    rec.get("ips_created_date"),
                    rec.get("ips_closed_date"),
                    case_number,
                    rec.get("ips_created_date"),
                    rec.get("ips_closed_date"),
                    rec.get("ips_closed_date"),
                ),
            )
            case_date_synced += cur.rowcount
            if args.dates_only:
                case_sync_batch += 1
                if case_sync_batch >= 200:
                    conn.commit()
                    case_sync_batch = 0
        except (psycopg2.errors.DeadlockDetected, psycopg2.errors.LockNotAvailable):
            conn.rollback()
            if args.dates_only:
                cur.execute("SET lock_timeout TO '200ms'")
                cur.execute("SET statement_timeout TO '120s'")
            case_date_lock_skipped += 1

    if args.dates_only:
        conn.commit()
        print("updated rows:", updated)
        print("case date synced rows:", case_date_synced)
        print("case date lock-skipped rows:", case_date_lock_skipped)
        print("dates-only mode: skipped non-date backfill steps")
        return 0

    owner_refreshed = 0
    if args.owner_refresh_empty:
        owner_candidates = [j for j, rec in jira_records.items() if rec.get("ips_owner_name")]
        if owner_candidates:
            cur.execute(
                """
                SELECT UPPER(jira_id)
                FROM ips_jira_bugs
                WHERE UPPER(jira_id) = ANY(%s)
                  AND jira_created_date >= %s::date
                  AND jira_created_date < %s::date
                  AND (
                      ips_owner_name IS NULL
                      OR TRIM(ips_owner_name) = ''
                      OR UPPER(TRIM(ips_owner_name)) = 'NA'
                  )
                """,
                (owner_candidates, f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
            )
            owner_targets = [row[0] for row in cur.fetchall()]

            for jira_id in owner_targets:
                rec = jira_records[jira_id]
                cur.execute(
                    """
                    UPDATE ips_jira_bugs
                    SET ips_owner_name = %s
                    WHERE UPPER(jira_id) = %s
                      AND jira_created_date >= %s::date
                      AND jira_created_date < %s::date
                      AND (
                          ips_owner_name IS NULL
                          OR TRIM(ips_owner_name) = ''
                          OR UPPER(TRIM(ips_owner_name)) = 'NA'
                      )
                    """,
                    (
                        rec["ips_owner_name"] or "NA",
                        jira_id,
                        f"{args.start_year}-01-01",
                        f"{args.end_year + 1}-01-01",
                    ),
                )
                owner_refreshed += cur.rowcount

    cur.execute(
        """
        SELECT COALESCE(ips_case_number, 0) AS ips_case_number,
               UPPER(TRIM(COALESCE(jira_id, ''))) AS jira_id
        FROM ips_jira_bugs
        """
    )
    existing_case_numbers: set[int] = set()
    existing_jira_ids: set[str] = set()
    for row in cur.fetchall():
        try:
            case_num = int(row[0] or 0)
        except (TypeError, ValueError):
            case_num = 0
        if case_num > 0:
            existing_case_numbers.add(case_num)
        jira_id = (row[1] or "").strip()
        if jira_id:
            existing_jira_ids.add(jira_id)

    insert_column_order = [
        "ips_case_number",
        "ips_title",
        "ips_created_date",
        "ips_closed_date",
        "ips_category",
        "ips_oem",
        "ips_owner_name",
        "ips_product",
        "ips_env_details",
        "ips_jira_id",
        "jira_id",
        "ips_status",
        "ips_sub_status",
        "ips_priority",
        "ips_closure_status",
        "ips_jira_promo_status",
        "ips_reporter_account_name",
        "reporter",
        "customer",
        "bug_project",
        "is_ips_promoted_to_jira",
    ]
    insert_columns = [col for col in insert_column_order if col in table_columns]

    insert_rows: list[tuple[Any, ...]] = []
    skipped_existing_case = 0
    skipped_existing_jira = 0
    for case_number, rec in case_records.items():
        jira_id = (rec.get("jira_id") or "").upper()
        if case_number in existing_case_numbers:
            skipped_existing_case += 1
            continue
        if jira_id and jira_id in existing_jira_ids:
            skipped_existing_jira += 1
            continue

        row_map: dict[str, Any] = {
            "ips_case_number": case_number,
            "ips_title": rec.get("ips_title") or "NA",
            "ips_created_date": rec.get("ips_created_date"),
            "ips_closed_date": rec.get("ips_closed_date"),
            "ips_category": rec.get("ips_category") or "NA",
            "ips_oem": rec.get("ips_oem") or "NA",
            "ips_owner_name": rec.get("ips_owner_name") or "NA",
            "ips_product": rec.get("ips_product") or "NA",
            "ips_env_details": rec.get("ips_env_details") or "NA",
            "ips_jira_id": jira_id or "NA",
            "jira_id": jira_id or "NA",
            "ips_status": "NA",
            "ips_sub_status": "NA",
            "ips_priority": "NA",
            "ips_closure_status": "NA",
            "ips_jira_promo_status": "NA",
            "ips_reporter_account_name": rec.get("ips_oem") or "NA",
            "reporter": rec.get("reporter") or "NA",
            "customer": rec.get("ips_oem") or "NA",
            "bug_project": "NA",
            "is_ips_promoted_to_jira": bool(jira_id),
        }
        insert_rows.append(tuple(row_map[col] for col in insert_columns))

    inserted = 0
    if insert_rows and insert_columns:
        placeholders = ", ".join(["%s"] * len(insert_columns))
        columns_sql = ", ".join(insert_columns)
        cur.executemany(
            f"INSERT INTO ips_jira_bugs ({columns_sql}) VALUES ({placeholders})",
            insert_rows,
        )
        inserted = len(insert_rows)

    reporter_filled = 0
    reporter_msft_normalized = 0
    ips_product_filled = 0
    ips_env_details_filled = 0

    if "ips_product" in table_columns:
        for case_number, rec in case_records.items():
            product_value = clean(rec.get("ips_product", ""))
            if not product_value or product_value.upper() == "NA":
                continue
            cur.execute(
                """
                UPDATE ips_jira_bugs
                SET ips_product = %s
                WHERE ips_case_number = %s
                  AND (
                      ips_product IS NULL
                      OR TRIM(ips_product) = ''
                      OR UPPER(TRIM(ips_product)) = 'NA'
                  )
                """,
                (product_value, case_number),
            )
            ips_product_filled += cur.rowcount

    if "ips_env_details" in table_columns:
        for case_number, rec in case_records.items():
            env_details_value = clean(rec.get("ips_env_details", ""))
            if not env_details_value or env_details_value.upper() == "NA":
                continue
            cur.execute(
                """
                UPDATE ips_jira_bugs
                SET ips_env_details = %s
                WHERE ips_case_number = %s
                  AND (
                      ips_env_details IS NULL
                      OR TRIM(ips_env_details) = ''
                      OR UPPER(TRIM(ips_env_details)) = 'NA'
                  )
                """,
                (env_details_value, case_number),
            )
            ips_env_details_filled += cur.rowcount

    if "reporter" in table_columns and "ips_owner_name" in table_columns:
        cur.execute(
            """
            SELECT ips_case_number, ips_owner_name
            FROM ips_jira_bugs
            WHERE ips_created_date >= %s::date
              AND ips_created_date < %s::date
              AND (
                  reporter IS NULL
                  OR TRIM(reporter) = ''
                  OR UPPER(TRIM(reporter)) = 'NA'
              )
              AND ips_owner_name IS NOT NULL
              AND TRIM(ips_owner_name) <> ''
              AND UPPER(TRIM(ips_owner_name)) <> 'NA'
            """,
            (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
        )
        reporter_targets = cur.fetchall()

        for case_number, owner_name in reporter_targets:
            reporter_name = normalize_ips_owner_reporter(owner_name)
            if not reporter_name or reporter_name.upper() == "NA":
                continue

            cur.execute(
                """
                UPDATE ips_jira_bugs
                SET reporter = %s
                WHERE ips_case_number = %s
                  AND (
                      reporter IS NULL
                      OR TRIM(reporter) = ''
                      OR UPPER(TRIM(reporter)) = 'NA'
                  )
                """,
                (reporter_name, case_number),
            )
            reporter_filled += cur.rowcount

        # MSFT-tagged IPS should be reported as MICROSOFT.
        cur.execute(
            """
            UPDATE ips_jira_bugs
            SET reporter = 'MICROSOFT'
            WHERE ips_created_date >= %s::date
                AND ips_created_date < %s::date
                AND LOWER(COALESCE(ips_title, '')) LIKE '%%msft%%'
                AND UPPER(TRIM(COALESCE(reporter, 'NA'))) <> 'MICROSOFT'
            """,
            (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
        )
        reporter_msft_normalized += cur.rowcount

    customer_filled = 0
    lenovo_normalized = 0
    fujitsu_normalized = 0
    huawei_normalized = 0
    surface_normalized = 0
    intel_nuc_normalized = 0
    ips_only_platform_inferred = 0
    ips_product_platform_inferred = 0
    ips_env_details_platform_inferred = 0
    ips_product_direct_platform_inferred = 0
    ips_only_title_brand_normalized = 0
    ips_only_other_oem_fallback = 0
    customer_alias_normalized = 0
    if "customer" in table_columns:
        cur.execute(
            """
            UPDATE ips_jira_bugs
            SET customer = UPPER(TRIM(ips_oem))
            WHERE ips_created_date >= %s::date
              AND ips_created_date < %s::date
              AND (
                  customer IS NULL
                  OR TRIM(customer) = ''
                  OR UPPER(TRIM(customer)) = 'NA'
              )
              AND ips_oem IS NOT NULL
              AND TRIM(ips_oem) <> ''
              AND UPPER(TRIM(ips_oem)) <> 'NA'
            """,
            (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
        )
        customer_filled += cur.rowcount

        if "ips_reporter_account_name" in table_columns:
            cur.execute(
                """
                UPDATE ips_jira_bugs
                SET customer = UPPER(TRIM(ips_reporter_account_name))
                WHERE ips_created_date >= %s::date
                  AND ips_created_date < %s::date
                  AND (
                      customer IS NULL
                      OR TRIM(customer) = ''
                      OR UPPER(TRIM(customer)) = 'NA'
                  )
                  AND ips_reporter_account_name IS NOT NULL
                  AND TRIM(ips_reporter_account_name) <> ''
                  AND UPPER(TRIM(ips_reporter_account_name)) <> 'NA'
                """,
                (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
            )
            customer_filled += cur.rowcount

        # Canonicalize known customer aliases.
        cur.execute(
            """
            UPDATE ips_jira_bugs
            SET customer = CASE LOWER(TRIM(COALESCE(customer, '')))
                WHEN 'micro-star international co., ltd.' THEN 'MSI'
                WHEN 'asustek computer incorporation' THEN 'ASUS'
                WHEN 'dell u s a corporation' THEN 'DELL'
                WHEN 'samsung electronics co., ltd.' THEN 'SAMSUNG'
                WHEN 'honor device co., ltd.' THEN 'HONOR'
                WHEN 'lg electronics inc.' THEN 'LG'
                WHEN 'panasonic connect' THEN 'PANASONIC'
                WHEN 'hp inc' THEN 'HP'
                WHEN 'xiaomi corporation' THEN 'XIAOMI'
                WHEN 'huawei ? allowed activities' THEN 'HUAWEI'
                WHEN 'giga-byte technology co., ltd.' THEN 'GIGABYTE'
                WHEN 'clevo co.' THEN 'CLEVO'
                WHEN 'asrock inc.' THEN 'ASROCK'
                WHEN 'vaio corporation' THEN 'VAIO'
                WHEN 'nec personal computers,ltd.' THEN 'NEC'
                WHEN 'shenzhen transsion holdings' THEN 'TRANSSION'
                WHEN 'elitegroup computer systems co. ltd' THEN 'ECS'
                WHEN 'acer incorporated' THEN 'ACER'
                WHEN 'shanghai sixunited intelligent technology' THEN 'SIXUNITED'
                WHEN 'guangdong oppo mobile telecommunicationscorp. ltd.' THEN 'OPPO'
                WHEN 'nexstgo company limited' THEN 'Nexstgo'
                WHEN 'account for deactivated contacts' THEN 'Other - OEM'
                WHEN 'edimax technology co., ltd' THEN 'Edimax'
                WHEN 'edimax technology co., ltd.' THEN 'Edimax'
                WHEN 'jaguar land rover - end customer' THEN 'JAGUAR LAND ROVER'
                WHEN 'shenzhen bitland information technologyco., ltd.' THEN 'Bitland'
                WHEN 'shenzhen emdoor electronic technology co., ltd.' THEN 'Emdoor'
                WHEN 'shanghai wingtech electronics technologyco., ltd.' THEN 'Wingtech'
                WHEN 'foxconn technology co., ltd.' THEN 'Foxconn'
                WHEN 'allion test labs, inc.' THEN 'Allion'
                WHEN 'shenzhen ip3 centry intelligent technolo' THEN 'IPS3 Tech'
                ELSE UPPER(TRIM(COALESCE(customer, '')))
            END
            WHERE ips_created_date >= %s::date
              AND ips_created_date < %s::date
              AND LOWER(TRIM(COALESCE(customer, ''))) IN (
                  'micro-star international co., ltd.',
                  'asustek computer incorporation',
                  'dell u s a corporation',
                  'samsung electronics co., ltd.',
                  'honor device co., ltd.',
                  'lg electronics inc.',
                  'panasonic connect',
                  'hp inc',
                  'xiaomi corporation',
                  'huawei ? allowed activities',
                  'giga-byte technology co., ltd.',
                  'clevo co.',
                  'asrock inc.',
                  'vaio corporation',
                                    'nec personal computers,ltd.',
                                    'shenzhen transsion holdings',
                                    'elitegroup computer systems co. ltd',
                                                                        'acer incorporated',
                                                                        'shanghai sixunited intelligent technology',
                                                                        'guangdong oppo mobile telecommunicationscorp. ltd.',
                                                                        'nexstgo company limited',
                                                                        'account for deactivated contacts',
                                                                        'edimax technology co., ltd',
                                                                        'edimax technology co., ltd.',
                                                                        'jaguar land rover - end customer',
                                                                        'shenzhen bitland information technologyco., ltd.',
                                                                        'shenzhen emdoor electronic technology co., ltd.',
                                                                        'shanghai wingtech electronics technologyco., ltd.',
                                                                        'foxconn technology co., ltd.',
                                                                        'allion test labs, inc.',
                                                                        'shenzhen ip3 centry intelligent technolo'
              )
            """,
            (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
        )
        customer_alias_normalized += cur.rowcount

        # Canonicalize Lenovo customer aliases and title-based hint.
        cur.execute(
            """
            UPDATE ips_jira_bugs
            SET customer = 'LENOVO'
            WHERE ips_created_date >= %s::date
              AND ips_created_date < %s::date
              AND (
                    LOWER(TRIM(COALESCE(customer, ''))) IN (
                        'lenovo (beijing) limited',
                        'lenovo compal future center'
                    )
                                        OR LOWER(COALESCE(ips_title, '')) LIKE '%%lenovo%%'
                    OR LOWER(COALESCE(ips_title, '')) LIKE '%%yoga%%'
                  )
              AND UPPER(TRIM(COALESCE(customer, 'NA'))) <> 'LENOVO'
            """,
            (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
        )
        lenovo_normalized += cur.rowcount

        # FCCL-related IPS should map to Fujitsu.
        cur.execute(
            """
            UPDATE ips_jira_bugs
            SET customer = 'FUJITSU'
            WHERE ips_created_date >= %s::date
              AND ips_created_date < %s::date
              AND (
                    LOWER(TRIM(COALESCE(customer, ''))) = 'fujitsu client computing limited (lenovo/fccl)'
                    OR LOWER(TRIM(COALESCE(ips_oem, ''))) LIKE '%%fccl%%'
                    OR LOWER(TRIM(COALESCE(ips_reporter_account_name, ''))) LIKE '%%fccl%%'
                    OR LOWER(COALESCE(ips_title, '')) LIKE '%%fccl%%'
                  )
              AND UPPER(TRIM(COALESCE(customer, 'NA'))) <> 'FUJITSU'
            """,
            (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
        )
        fujitsu_normalized += cur.rowcount

    if "ips_platform" in table_columns:
        # Infer missing IPS-only platform from title tokens.
        platform_rules = [
            ("Raptor Lake", r"(^|[^A-Za-z0-9])(RPL|RAPTOR\s*LAKE)([^A-Za-z0-9]|$)"),
            ("Arrow Lake - Hx", r"(^|[^A-Za-z0-9])(ARL[-_\s]?HX|ARROW\s*LAKE[-_\s]?HX)([^A-Za-z0-9]|$)"),
            ("Arrow Lake -H", r"(^|[^A-Za-z0-9])(ARL[-_\s]?H|ARROW\s*LAKE[-_\s]?H)([^A-Za-z0-9]|$)"),
            ("Arrow Lake -S", r"(^|[^A-Za-z0-9])(ARL[-_\s]?S|ARROW\s*LAKE[-_\s]?S)([^A-Za-z0-9]|$)"),
            ("Arrow Lake", r"(^|[^A-Za-z0-9])(ARL|ARX|ARROW\s*LAKE)([^A-Za-z0-9]|$)"),
            ("Alder Lake", r"(^|[^A-Za-z0-9])(ADL|ALDER\s*LAKE)([^A-Za-z0-9]|$)"),
            ("Meteor Lake", r"(^|[^A-Za-z0-9])(MTL|METEOR\s*LAKE)([^A-Za-z0-9]|$)"),
            ("Tiger Lake", r"(^|[^A-Za-z0-9])(TGL|TIGER\s*LAKE)([^A-Za-z0-9]|$)"),
            ("Coffee Lake", r"(^|[^A-Za-z0-9])(CFL|COFFEE\s*LAKE)([^A-Za-z0-9]|$)"),
            ("Comet Lake", r"(^|[^A-Za-z0-9])(CML|COMET\s*LAKE)([^A-Za-z0-9]|$)"),
            ("Lunar Lake", r"(^|[^A-Za-z0-9])(LNL|LUNAR(?:\s*LAKE)?)([^A-Za-z0-9]|$)"),
            ("Panther Lake", r"(^|[^A-Za-z0-9])(PTL|PANTHER\s*LAKE)([^A-Za-z0-9]|$)"),
            ("Nova Lake - S", r"(^|[^A-Za-z0-9])(NVL[-_\s]?S|NOVA\s*LAKE[-_\s]?S)([^A-Za-z0-9]|$)"),
            ("Nova Lake", r"(^|[^A-Za-z0-9])(NVL|NOVA\s*LAKE)([^A-Za-z0-9]|$)"),
            ("Wildcat Lake", r"(^|[^A-Za-z0-9])(WCL|WILDCAT\s*LAKE)([^A-Za-z0-9]|$)"),
        ]

        for platform_name, regex_pattern in platform_rules:
            cur.execute(
                """
                UPDATE ips_jira_bugs
                SET ips_platform = %s
                WHERE COALESCE(ips_case_number, 0) > 0
                  AND (
                      jira_id IS NULL
                      OR TRIM(jira_id) = ''
                      OR UPPER(TRIM(jira_id)) = 'NA'
                      OR NOT (TRIM(jira_id) ~* '^[A-Za-z][A-Za-z0-9_]*-[0-9]+$')
                  )
                  AND (
                      ips_jira_id IS NULL
                      OR TRIM(ips_jira_id) = ''
                      OR UPPER(TRIM(ips_jira_id)) = 'NA'
                  )
                  AND (
                      ips_platform IS NULL
                      OR TRIM(ips_platform) = ''
                      OR UPPER(TRIM(ips_platform)) IN ('NA', 'N/A', 'UNKNOWN', 'OTHERS')
                  )
                  AND COALESCE(ips_title, '') ~* %s
                """,
                (platform_name, regex_pattern),
            )
            ips_only_platform_inferred += cur.rowcount

        if "ips_product" in table_columns:
            for platform_name, regex_pattern in platform_rules:
                cur.execute(
                    """
                    UPDATE ips_jira_bugs
                    SET ips_platform = %s
                    WHERE COALESCE(ips_case_number, 0) > 0
                      AND (
                          jira_id IS NULL
                          OR TRIM(jira_id) = ''
                          OR UPPER(TRIM(jira_id)) = 'NA'
                          OR NOT (TRIM(jira_id) ~* '^[A-Za-z][A-Za-z0-9_]*-[0-9]+$')
                      )
                      AND (
                          ips_jira_id IS NULL
                          OR TRIM(ips_jira_id) = ''
                          OR UPPER(TRIM(ips_jira_id)) = 'NA'
                      )
                      AND (
                          ips_platform IS NULL
                          OR TRIM(ips_platform) = ''
                          OR UPPER(TRIM(ips_platform)) IN ('NA', 'N/A', 'UNKNOWN', 'OTHERS')
                      )
                      AND COALESCE(ips_product, '') ~* %s
                    """,
                    (platform_name, regex_pattern),
                )
                ips_product_platform_inferred += cur.rowcount

        if "ips_env_details" in table_columns:
            for platform_name, regex_pattern in platform_rules:
                cur.execute(
                    """
                    UPDATE ips_jira_bugs
                    SET ips_platform = %s
                    WHERE COALESCE(ips_case_number, 0) > 0
                      AND (
                          jira_id IS NULL
                          OR TRIM(jira_id) = ''
                          OR UPPER(TRIM(jira_id)) = 'NA'
                          OR NOT (TRIM(jira_id) ~* '^[A-Za-z][A-Za-z0-9_]*-[0-9]+$')
                      )
                      AND (
                          ips_jira_id IS NULL
                          OR TRIM(ips_jira_id) = ''
                          OR UPPER(TRIM(ips_jira_id)) = 'NA'
                      )
                      AND (
                          ips_platform IS NULL
                          OR TRIM(ips_platform) = ''
                          OR UPPER(TRIM(ips_platform)) IN ('NA', 'N/A', 'UNKNOWN', 'OTHERS')
                      )
                      AND COALESCE(ips_env_details, '') ~* %s
                    """,
                    (platform_name, regex_pattern),
                )
                ips_env_details_platform_inferred += cur.rowcount

        # Final fallback: if ips_product itself is a canonical platform name, copy it
        # directly to ips_platform. This catches entries (e.g. "Arrow Lake") whose
        # titles contain no recognisable abbreviation but whose product field already
        # carries the exact platform string.
        if "ips_product" in table_columns:
            known_platforms = [
                p for p, _ in platform_rules
            ]
            cur.execute(
                """
                UPDATE ips_jira_bugs
                SET ips_platform = TRIM(ips_product)
                WHERE COALESCE(ips_case_number, 0) > 0
                  AND (
                      jira_id IS NULL
                      OR TRIM(jira_id) = ''
                      OR UPPER(TRIM(jira_id)) = 'NA'
                      OR NOT (TRIM(jira_id) ~* '^[A-Za-z][A-Za-z0-9_]*-[0-9]+$')
                  )
                  AND (
                      ips_jira_id IS NULL
                      OR TRIM(ips_jira_id) = ''
                      OR UPPER(TRIM(ips_jira_id)) = 'NA'
                  )
                  AND (
                      ips_platform IS NULL
                      OR TRIM(ips_platform) = ''
                      OR UPPER(TRIM(ips_platform)) IN ('NA', 'N/A', 'UNKNOWN', 'OTHERS')
                  )
                  AND ips_product IS NOT NULL
                  AND TRIM(ips_product) <> ''
                  AND UPPER(TRIM(ips_product)) NOT IN ('NA', 'N/A', 'UNKNOWN', 'OTHERS')
                  AND LOWER(TRIM(ips_product)) = ANY(%s)
                """,
                ([p.lower() for p in known_platforms],),
            )
            ips_product_direct_platform_inferred += cur.rowcount

        # Title hint: HUAWEI-tagged IPS should map to Huawei.
        cur.execute(
            """
            UPDATE ips_jira_bugs
            SET customer = 'HUAWEI'
            WHERE ips_created_date >= %s::date
                AND ips_created_date < %s::date
                AND LOWER(COALESCE(ips_title, '')) LIKE '%%huawei%%'
                AND UPPER(TRIM(COALESCE(customer, 'NA'))) <> 'HUAWEI'
            """,
            (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
        )
        huawei_normalized += cur.rowcount

        # Title hint: Surface-tagged IPS/JIRA should map to MICROSOFT.
        title_surface_conditions = ["LOWER(COALESCE(ips_title, '')) LIKE '%%surface%%'"]
        if "jira_title" in table_columns:
            title_surface_conditions.append("LOWER(COALESCE(jira_title, '')) LIKE '%%surface%%'")

        cur.execute(
            f"""
            UPDATE ips_jira_bugs
            SET customer = 'MICROSOFT'
            WHERE (
                    (ips_created_date >= %s::date AND ips_created_date < %s::date)
                    OR (jira_created_date >= %s::date AND jira_created_date < %s::date)
                )
                AND ({' OR '.join(title_surface_conditions)})
                AND UPPER(TRIM(COALESCE(customer, 'NA'))) <> 'MICROSOFT'
            """,
            (
                f"{args.start_year}-01-01",
                f"{args.end_year + 1}-01-01",
                f"{args.start_year}-01-01",
                f"{args.end_year + 1}-01-01",
            ),
        )
        surface_normalized += cur.rowcount

        # Title hint: Intel NUC-tagged IPS should map to INTEL NUC.
        cur.execute(
            """
            UPDATE ips_jira_bugs
            SET customer = 'INTEL NUC'
            WHERE ips_created_date >= %s::date
                AND ips_created_date < %s::date
                AND (
                    COALESCE(ips_title, '') ~* '(^|[^A-Za-z0-9])INTEL[[:space:]]+NUC([^A-Za-z0-9]|$)'
                    OR COALESCE(ips_title, '') ~* '(^|[^A-Za-z0-9])NUC([^A-Za-z0-9]|$)'
                )
                AND UPPER(TRIM(COALESCE(customer, 'NA'))) <> 'INTEL NUC'
            """,
            (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
        )
        intel_nuc_normalized += cur.rowcount

        # IPS-only entries: title brand tags imply canonical customer.
        ips_only_brand_rules = [
                    ("MICROSOFT", r"(^|[^A-Za-z0-9])MSFT([^A-Za-z0-9]|$)"),
                    ("MICROSOFT", r"(^|[^A-Za-z0-9])MICROSOFT([^A-Za-z0-9]|$)"),
                    ("MADIGI", r"(^|[^A-Za-z0-9])MADIG(I)?([^A-Za-z0-9]|$)"),
                    ("BYD", r"(^|[^A-Za-z0-9])BYD([^A-Za-z0-9]|$)"),
                    ("MECHREVO", r"(^|[^A-Za-z0-9])MECHREVO([^A-Za-z0-9]|$)"),
                    ("EPSON", r"(^|[^A-Za-z0-9])EPSON([^A-Za-z0-9]|$)"),
                    ("HAIER", r"(^|[^A-Za-z0-9])HAIER([^A-Za-z0-9]|$)"),
                    ("INTEL", r"(^|[^A-Za-z0-9])INTEL([^A-Za-z0-9]|$)"),
                    ("WEIBU", r"(^|[^A-Za-z0-9])WEIBU([^A-Za-z0-9]|$)"),
                    ("AISTONE", r"(^|[^A-Za-z0-9])AISTONE([^A-Za-z0-9]|$)|(^|[^A-Za-z0-9])ALSTONE([^A-Za-z0-9]|$)"),
                    ("EMDOOR", r"(^|[^A-Za-z0-9])EMDOOR([^A-Za-z0-9]|$)"),
                    ("SIXUNITED", r"(^|[^A-Za-z0-9])SIXUNITED([^A-Za-z0-9]|$)"),
                    ("UNIWILL", r"(^|[^A-Za-z0-9])UNIWILL([^A-Za-z0-9]|$)"),
                    ("HUAWEI", r"(^|[^A-Za-z0-9])HUAWEI([^A-Za-z0-9]|$)"),
                    ("HONOR", r"(^|[^A-Za-z0-9])HONOR([^A-Za-z0-9]|$)"),
                    ("HONOR", r"(^|[^A-Za-z0-9])HONORI([^A-Za-z0-9]|$)"),
                    ("SAMSUNG", r"(^|[^A-Za-z0-9])SAMSUNG([^A-Za-z0-9]|$)"),
                    ("XIAOMI", r"(^|[^A-Za-z0-9])XIAOMI([^A-Za-z0-9]|$)"),
                    ("REALME", r"(^|[^A-Za-z0-9])REALME([^A-Za-z0-9]|$)"),
                    ("OPPO", r"(^|[^A-Za-z0-9])OPPO([^A-Za-z0-9]|$)"),
                    ("LENOVO", r"(^|[^A-Za-z0-9])LENOVO([^A-Za-z0-9]|$)"),
                    ("LENOVO", r"(^|[^A-Za-z0-9])YOGA([^A-Za-z0-9]|$)"),
                    ("LENOVO", r"(^|[^A-Za-z0-9])THINKPAD([^A-Za-z0-9]|$)"),
                    ("LENOVO", r"(^|[^A-Za-z0-9])IDEAPAD([^A-Za-z0-9]|$)"),
                    ("VAIO", r"(^|[^A-Za-z0-9])VAIO([^A-Za-z0-9]|$)"),
                    ("NEC", r"(^|[^A-Za-z0-9])NEC([^A-Za-z0-9]|$)"),
                    ("MSI", r"(^|[^A-Za-z0-9])MSI([^A-Za-z0-9]|$)"),
                    ("UNIS", r"(^|[^A-Za-z0-9])UNIS([^A-Za-z0-9]|$)"),
                    ("CLEVO", r"(^|[^A-Za-z0-9])CLEVO([^A-Za-z0-9]|$)"),
                    ("ASROCK", r"(^|[^A-Za-z0-9])ASROCK([^A-Za-z0-9]|$)"),
                    ("RAZER", r"(^|[^A-Za-z0-9])RAZER([^A-Za-z0-9]|$)"),
                    ("GIGABYTE", r"(^|[^A-Za-z0-9])GIGABYTE([^A-Za-z0-9]|$)"),
                    ("ACER", r"(^|[^A-Za-z0-9])ACER([^A-Za-z0-9]|$)"),
                    ("ASUS", r"(^|[^A-Za-z0-9])ASUS([^A-Za-z0-9]|$)"),
                    ("HP", r"(^|[^A-Za-z0-9])HP([^A-Za-z0-9]|$)"),
                    ("DELL", r"(^|[^A-Za-z0-9])DELL([^A-Za-z0-9]|$)"),
                    ("LG", r"(^|[^A-Za-z0-9])LG([^A-Za-z0-9]|$)"),
                    ("PANASONIC", r"(^|[^A-Za-z0-9])PANASONIC([^A-Za-z0-9]|$)"),
                    ("TOSHIBA", r"(^|[^A-Za-z0-9])TOSHIBA([^A-Za-z0-9]|$)"),
                    ("FUJITSU", r"(^|[^A-Za-z0-9])FUJITSU([^A-Za-z0-9]|$)"),
                    ("WIKO", r"(^|[^A-Za-z0-9])WIKO([^A-Za-z0-9]|$)"),
        ]

        for target_customer, regex_pattern in ips_only_brand_rules:
            cur.execute(
                """
                UPDATE ips_jira_bugs
                SET customer = %s
                WHERE COALESCE(ips_case_number, 0) > 0
                  AND (
                      jira_id IS NULL
                      OR TRIM(jira_id) = ''
                      OR UPPER(TRIM(jira_id)) = 'NA'
                      OR NOT (TRIM(jira_id) ~* '^[A-Za-z][A-Za-z0-9_]*-[0-9]+$')
                  )
                  AND COALESCE(ips_title, '') ~* %s
                  AND UPPER(TRIM(COALESCE(customer, 'NA'))) <> %s
                """,
                (target_customer, regex_pattern, target_customer),
            )
            ips_only_title_brand_normalized += cur.rowcount

        # Final fallback for IPS-only rows: keep non-major OEM bucket explicit.
        cur.execute(
            """
            UPDATE ips_jira_bugs
            SET customer = 'Other - OEM'
            WHERE COALESCE(ips_case_number, 0) > 0
              AND (
                  jira_id IS NULL
                  OR TRIM(jira_id) = ''
                  OR UPPER(TRIM(jira_id)) = 'NA'
                  OR NOT (TRIM(jira_id) ~* '^[A-Za-z][A-Za-z0-9_]*-[0-9]+$')
              )
              AND (
                  ips_jira_id IS NULL
                  OR TRIM(ips_jira_id) = ''
                  OR UPPER(TRIM(ips_jira_id)) = 'NA'
              )
              AND (
                  customer IS NULL
                  OR TRIM(customer) = ''
                  OR UPPER(TRIM(customer)) = 'NA'
              )
            """
        )
        ips_only_other_oem_fallback += cur.rowcount

        # Ensure FCCL override still wins after title-based normalization.
        cur.execute(
            """
            UPDATE ips_jira_bugs
            SET customer = 'FUJITSU'
            WHERE COALESCE(ips_case_number, 0) > 0
              AND (
                  jira_id IS NULL
                  OR TRIM(jira_id) = ''
                  OR UPPER(TRIM(jira_id)) = 'NA'
                  OR NOT (TRIM(jira_id) ~* '^[A-Za-z][A-Za-z0-9_]*-[0-9]+$')
              )
              AND (
                    LOWER(TRIM(COALESCE(customer, ''))) = 'fujitsu client computing limited (lenovo/fccl)'
                    OR LOWER(TRIM(COALESCE(ips_oem, ''))) LIKE '%%fccl%%'
                    OR LOWER(TRIM(COALESCE(ips_reporter_account_name, ''))) LIKE '%%fccl%%'
                    OR LOWER(COALESCE(ips_title, '')) LIKE '%%fccl%%'
                  )
              AND UPPER(TRIM(COALESCE(customer, 'NA'))) <> 'FUJITSU'
            """
        )
        fujitsu_normalized += cur.rowcount

    conn.commit()
    print("updated rows:", updated)
    print("case date synced rows:", case_date_synced)
    print("owner refreshed rows:", owner_refreshed)
    print("inserted missing rows:", inserted)
    print("ips_product filled rows:", ips_product_filled)
    print("ips_env_details filled rows:", ips_env_details_filled)
    print("reporter filled rows:", reporter_filled)
    print("reporter msft normalized rows:", reporter_msft_normalized)
    print("customer filled rows:", customer_filled)
    print("customer alias normalized rows:", customer_alias_normalized)
    print("lenovo normalized rows:", lenovo_normalized)
    print("fujitsu normalized rows:", fujitsu_normalized)
    print("huawei normalized rows:", huawei_normalized)
    print("surface normalized rows:", surface_normalized)
    print("intel nuc normalized rows:", intel_nuc_normalized)
    print("ips-only platform inferred rows:", ips_only_platform_inferred)
    print("ips-product platform inferred rows:", ips_product_platform_inferred)
    print("ips-product direct platform inferred rows:", ips_product_direct_platform_inferred)
    print("ips-env-details platform inferred rows:", ips_env_details_platform_inferred)
    print("ips-only title brand normalized rows:", ips_only_title_brand_normalized)
    print("ips-only other-oem fallback rows:", ips_only_other_oem_fallback)
    print("skipped existing by ips_case_number:", skipped_existing_case)
    print("skipped existing by jira_id:", skipped_existing_jira)

    cur.execute(
        """
        SELECT EXTRACT(YEAR FROM jira_created_date)::int AS yr,
               COUNT(*) AS total,
               SUM(CASE WHEN ips_case_number > 0 THEN 1 ELSE 0 END) AS with_ips,
               SUM(CASE WHEN ips_owner_name IS NOT NULL AND TRIM(ips_owner_name) <> '' AND UPPER(TRIM(ips_owner_name)) <> 'NA' THEN 1 ELSE 0 END) AS with_owner
        FROM ips_jira_bugs
        WHERE jira_created_date >= %s::date
          AND jira_created_date < %s::date
        GROUP BY 1 ORDER BY 1
        """,
        (f"{args.start_year}-01-01", f"{args.end_year + 1}-01-01"),
    )
    print("yearly:", cur.fetchall())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())