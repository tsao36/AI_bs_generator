from __future__ import annotations

import argparse
import csv
from pathlib import Path

import psycopg2

from APIs import Sherlock


def _clean(value: object) -> str:
    return str(value or "").strip()


def _connect_db():
    return psycopg2.connect(
        database=Sherlock.PostgresCustomerEngineeringDb.database,
        user=Sherlock.PostgresCustomerEngineeringDb.user,
        password=Sherlock.PostgresCustomerEngineeringDb.password,
        host=Sherlock.PostgresCustomerEngineeringDb.host,
        port=Sherlock.PostgresCustomerEngineeringDb.port,
    )


def export_pairs(out_csv: Path, min_component_count: int = 1) -> tuple[int, int]:
    conn = _connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH base AS (
                    SELECT
                        UPPER(TRIM(COALESCE(jira_id, ''))) AS jira_id,
                        TRIM(ips_title) AS ips_title,
                        TRIM(jira_final_component) AS jira_component
                    FROM ips_jira_bugs
                    WHERE NULLIF(TRIM(COALESCE(ips_title, '')), '') IS NOT NULL
                      AND UPPER(TRIM(COALESCE(ips_title, ''))) <> 'NA'
                      AND NULLIF(TRIM(COALESCE(jira_final_component, '')), '') IS NOT NULL
                      AND UPPER(TRIM(COALESCE(jira_final_component, ''))) <> 'NA'
                ), comp_counts AS (
                    SELECT jira_component, COUNT(*) AS n
                    FROM base
                    GROUP BY jira_component
                )
                SELECT
                    b.jira_id,
                    b.ips_title,
                    b.jira_component,
                    cc.n AS component_frequency
                FROM base b
                JOIN comp_counts cc
                  ON cc.jira_component = b.jira_component
                WHERE cc.n >= %s
                ORDER BY cc.n DESC, b.jira_component, b.jira_id
                """,
                (max(1, int(min_component_count)),),
            )
            rows = cur.fetchall()

    finally:
        conn.close()

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "jira_id",
                "ips_title",
                "jira_component",
                "component_frequency",
                "feature_text",
            ]
        )
        for jira_id, ips_title, jira_component, freq in rows:
            title = _clean(ips_title)
            comp = _clean(jira_component)
            feature_text = f"[COMPONENT={comp}] {title}".strip()
            writer.writerow([_clean(jira_id), title, comp, int(freq or 0), feature_text])

    distinct_components = len({_clean(r[2]) for r in rows})
    return len(rows), distinct_components


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export training pairs using ips_title + jira component (no ips_category input)."
    )
    parser.add_argument(
        "--out-csv",
        default="training_title_component_pairs.csv",
        help="Output CSV path",
    )
    parser.add_argument(
        "--min-component-count",
        type=int,
        default=1,
        help="Keep rows only for components with at least this many samples",
    )
    args = parser.parse_args()

    out_csv = Path(args.out_csv).resolve()
    rows, comps = export_pairs(out_csv, min_component_count=args.min_component_count)

    print(f"[OK] Wrote: {out_csv}")
    print(f"[OK] Rows: {rows}")
    print(f"[OK] Distinct components: {comps}")
    print("[INFO] Input features only: ips_title + jira_component")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
