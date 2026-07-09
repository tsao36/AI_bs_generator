from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List, Set, Tuple

import psycopg2

from APIs import Sherlock


def _clean(value: object) -> str:
    return str(value or "").strip()


def _norm(value: object) -> str:
    return _clean(value).lower()


def _connect_db():
    return psycopg2.connect(
        database=Sherlock.PostgresCustomerEngineeringDb.database,
        user=Sherlock.PostgresCustomerEngineeringDb.user,
        password=Sherlock.PostgresCustomerEngineeringDb.password,
        host=Sherlock.PostgresCustomerEngineeringDb.host,
        port=Sherlock.PostgresCustomerEngineeringDb.port,
    )


def _load_component_map(path: Path) -> Dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Mapping CSV not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        if "component" not in headers or "suggested_category" not in headers:
            raise RuntimeError(
                "Mapping CSV must contain columns: component, suggested_category"
            )

        out: Dict[str, str] = {}
        for row in reader:
            comp = _clean(row.get("component"))
            cat = _clean(row.get("suggested_category"))
            if not comp or not cat:
                continue
            out[_norm(comp)] = cat
    if not out:
        raise RuntimeError("No usable mapping rows found in mapping CSV")
    return out


def _load_jira_ids_from_export(path: Path) -> Set[str]:
    if not path.exists():
        raise FileNotFoundError(f"Jira CSV not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = [f for f in (reader.fieldnames or [])]

        issue_col = None
        for f in fieldnames:
            if _norm(f) == "issue key":
                issue_col = f
                break
        if issue_col is None:
            raise RuntimeError("Could not find 'Issue key' column in jira export CSV")

        ids: Set[str] = set()
        for row in reader:
            jira_id = _clean(row.get(issue_col)).upper()
            if jira_id:
                ids.add(jira_id)
    if not ids:
        raise RuntimeError("No Jira IDs loaded from jira export CSV")
    return ids


def _fetch_db_candidates(jira_ids: Set[str]) -> List[Tuple[str, str, str]]:
    conn = _connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    UPPER(TRIM(COALESCE(jira_id, ''))) AS jira_id,
                    TRIM(COALESCE(ips_title, '')) AS ips_title,
                    TRIM(COALESCE(jira_final_component, '')) AS jira_component
                FROM ips_jira_bugs
                WHERE UPPER(TRIM(COALESCE(jira_id, ''))) = ANY(%s)
                  AND NULLIF(TRIM(COALESCE(ips_title, '')), '') IS NOT NULL
                  AND UPPER(TRIM(COALESCE(ips_title, ''))) <> 'NA'
                  AND NULLIF(TRIM(COALESCE(jira_final_component, '')), '') IS NOT NULL
                  AND UPPER(TRIM(COALESCE(jira_final_component, ''))) <> 'NA'
                """,
                (list(jira_ids),),
            )
            return cur.fetchall()
    finally:
        conn.close()


def generate_dataset(mapping_csv: Path, jira_csv: Path, out_csv: Path) -> Dict[str, int]:
    component_map = _load_component_map(mapping_csv)
    jira_ids = _load_jira_ids_from_export(jira_csv)
    candidates = _fetch_db_candidates(jira_ids)

    seen: Set[Tuple[str, str]] = set()
    selected_rows: List[Dict[str, str]] = []

    for jira_id, ips_title, jira_component in candidates:
        norm_comp = _norm(jira_component)
        if norm_comp not in component_map:
            continue

        key = (jira_id, norm_comp)
        if key in seen:
            continue
        seen.add(key)

        selected_rows.append(
            {
                "jira_id": jira_id,
                "ips_title": ips_title,
                "jira_component": jira_component,
                "human_category": component_map[norm_comp],
                "label_source": "component_to_category_100pct",
                "pseudo_confidence": "1.00",
                "sample_weight": "0.90",
                "feature_text": f"[COMPONENT={jira_component}] {ips_title}".strip(),
            }
        )

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "jira_id",
                "ips_title",
                "jira_component",
                "human_category",
                "label_source",
                "pseudo_confidence",
                "sample_weight",
                "feature_text",
            ],
        )
        writer.writeheader()
        writer.writerows(selected_rows)

    return {
        "jira_ids_in_export": len(jira_ids),
        "db_candidate_rows": len(candidates),
        "mapped_training_rows": len(selected_rows),
        "mapped_components": len({_norm(r['jira_component']) for r in selected_rows}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate high-confidence training rows from component->category map + jira export + DB"
    )
    parser.add_argument("--mapping-csv", default="Component_to_category_mapping.csv")
    parser.add_argument("--jira-csv", default="jira_2021_2026_may.csv")
    parser.add_argument("--out-csv", default="high_confidence_training_from_component_map.csv")
    args = parser.parse_args()

    summary = generate_dataset(
        mapping_csv=Path(args.mapping_csv).resolve(),
        jira_csv=Path(args.jira_csv).resolve(),
        out_csv=Path(args.out_csv).resolve(),
    )

    print(f"[OK] wrote: {Path(args.out_csv).resolve()}")
    print(f"[INFO] jira_ids_in_export={summary['jira_ids_in_export']}")
    print(f"[INFO] db_candidate_rows={summary['db_candidate_rows']}")
    print(f"[INFO] mapped_training_rows={summary['mapped_training_rows']}")
    print(f"[INFO] mapped_components={summary['mapped_components']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
