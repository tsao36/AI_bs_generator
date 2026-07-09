from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import psycopg2
from psycopg2.extras import execute_values

from APIs import Sherlock


@dataclass
class CsvRecord:
    jira_id: str
    component: str


def _clean(value: object) -> str:
    return str(value or "").strip()


def _norm_header(value: str) -> str:
    return _clean(value).lower()


def _load_csv_records(csv_path: Path, jira_col: str, component_col: str) -> dict[str, str]:
    encodings: Iterable[str] = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
    last_error: Exception | None = None

    for enc in encodings:
        try:
            with csv_path.open("r", encoding=enc, newline="") as handle:
                reader = csv.reader(handle)
                headers = next(reader, None)
                if not headers:
                    return {}

                jira_idx = None
                comp_indices: list[int] = []
                for idx, header in enumerate(headers):
                    h = _norm_header(header)
                    if h == _norm_header(jira_col):
                        jira_idx = idx
                    if h == _norm_header(component_col):
                        comp_indices.append(idx)

                if jira_idx is None:
                    raise RuntimeError(f"CSV missing jira column: {jira_col}")
                if not comp_indices:
                    raise RuntimeError(f"CSV missing component column: {component_col}")

                by_jira: dict[str, str] = {}
                for row in reader:
                    if jira_idx >= len(row):
                        continue
                    jira_id = _clean(row[jira_idx]).upper()
                    if not jira_id:
                        continue

                    component = ""
                    for comp_idx in comp_indices:
                        if comp_idx < len(row):
                            candidate = _clean(row[comp_idx])
                            if candidate:
                                component = candidate
                                break
                    if not component:
                        continue

                    by_jira[jira_id] = component
                return by_jira
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

    if last_error:
        raise last_error
    return {}


def _connect_db():
    return psycopg2.connect(
        database=Sherlock.PostgresCustomerEngineeringDb.database,
        user=Sherlock.PostgresCustomerEngineeringDb.user,
        password=Sherlock.PostgresCustomerEngineeringDb.password,
        host=Sherlock.PostgresCustomerEngineeringDb.host,
        port=Sherlock.PostgresCustomerEngineeringDb.port,
    )


def _pick_target_component_column(cur) -> str:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'ips_jira_bugs'
        """
    )
    cols = {row[0] for row in cur.fetchall()}

    for candidate in ("component", "jira_final_component", "jira_initial_component"):
        if candidate in cols:
            return candidate

    raise RuntimeError(
        "No usable component column found on ips_jira_bugs. Tried: component, jira_final_component, jira_initial_component"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Update ips_jira_bugs component column from Jira CSV by jira_id.")
    parser.add_argument("--csv", default="jira_2021_2026_may.csv", help="Path to Jira export CSV")
    parser.add_argument("--jira-col", default="Issue key", help="CSV column containing Jira ID")
    parser.add_argument("--component-col", default="Component/s", help="CSV column containing component")
    parser.add_argument("--table", default="ips_jira_bugs", help="Target DB table")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only")
    args = parser.parse_args()

    table = _clean(args.table)
    if table != "ips_jira_bugs":
        raise RuntimeError("This script is currently restricted to ips_jira_bugs for safety.")

    csv_path = Path(args.csv).resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    csv_map = _load_csv_records(csv_path, args.jira_col, args.component_col)
    if not csv_map:
        print("[INFO] No jira_id/component rows found in CSV. Nothing to update.")
        return 0

    conn = _connect_db()
    try:
        with conn:
            with conn.cursor() as cur:
                target_component_col = _pick_target_component_column(cur)
                print(f"[INFO] Target column: {target_component_col}")
                print(f"[INFO] CSV jira_id with component: {len(csv_map)}")

                jira_ids = list(csv_map.keys())
                cur.execute(
                    """
                    SELECT UPPER(TRIM(COALESCE(jira_id, '')))
                    FROM ips_jira_bugs
                    WHERE UPPER(TRIM(COALESCE(jira_id, ''))) = ANY(%s)
                    """,
                    (jira_ids,),
                )
                matched_ids = {row[0] for row in cur.fetchall() if row and row[0]}

                missing_ids = [j for j in jira_ids if j not in matched_ids]
                updates = [(csv_map[j], j) for j in jira_ids if j in matched_ids]

                if args.dry_run:
                    print(f"[DRY RUN] Would update rows: {len(updates)}")
                else:
                    cur.execute("SET lock_timeout TO '5s'")
                    cur.execute("SET statement_timeout TO '120s'")
                    cur.execute(
                        """
                        CREATE TEMP TABLE tmp_jira_component_update (
                            jira_id TEXT PRIMARY KEY,
                            component TEXT
                        ) ON COMMIT DROP
                        """
                    )
                    execute_values(
                        cur,
                        "INSERT INTO tmp_jira_component_update (jira_id, component) VALUES %s ON CONFLICT (jira_id) DO UPDATE SET component = EXCLUDED.component",
                        [(jira_id, component) for component, jira_id in updates],
                    )
                    cur.execute(
                        f"""
                        UPDATE ips_jira_bugs AS t
                        SET {target_component_col} = s.component
                        FROM tmp_jira_component_update AS s
                        WHERE UPPER(TRIM(COALESCE(t.jira_id, ''))) = s.jira_id
                        """
                    )
                    print(f"[OK] Updated rows: {cur.rowcount}")

                print(f"[INFO] Unmatched jira_id count: {len(missing_ids)}")
                if missing_ids:
                    sample = ", ".join(missing_ids[:10])
                    print(f"[INFO] Unmatched sample (first 10): {sample}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
