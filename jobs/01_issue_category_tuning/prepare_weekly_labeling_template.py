from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Tuple

from Wireless_bug_dashboard import DbConnector  # type: ignore
from issue_category_model import classify_issue_title, load_category_model
from offload_reporter_issues import _created_date_expr, _get_table_columns, _has, _title_expr


def _find_latest_weekly_dir(tuning_root: str) -> str:
    candidates = [p for p in glob.glob(os.path.join(tuning_root, "weekly_*")) if os.path.isdir(p)]
    if not candidates:
        raise FileNotFoundError(f"No weekly output folder found under: {tuning_root}")
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _find_latest_low_conf_csv(tuning_root: str, exclude_dir: str = "") -> str:
    """Find the most recent low_confidence_candidates.csv from completed tuning runs.

    Excludes `exclude_dir` (the current week's dir being built) so we pick the
    previous week's results rather than an empty/partial new folder.
    """
    pattern = os.path.join(tuning_root, "weekly_*", "low_confidence_candidates.csv")
    candidates = [
        p for p in glob.glob(pattern)
        if os.path.isfile(p) and os.path.abspath(os.path.dirname(p)) != os.path.abspath(exclude_dir)
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _find_latest_targeted_supplement() -> str:
    pattern = "targeted_labeling_supplement_*.csv"
    candidates = [p for p in glob.glob(pattern) if os.path.isfile(p)]
    if not candidates:
        return ""
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _load_low_confidence_candidates(csv_path: str, top_n: int) -> List[Dict[str, str]]:
    """Load the lowest-confidence rows from a low_confidence_candidates.csv.

    Returns at most `top_n` rows sorted ascending by confidence (lowest first).
    """
    if not csv_path or not os.path.isfile(csv_path):
        return []
    rows: List[Dict[str, str]] = []
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                title = str(row.get("ips_title") or "").strip()
                existing_label = str(row.get("human_category") or "").strip()
                if not title or not existing_label:
                    continue
                try:
                    conf = float(row.get("confidence") or "1.0")
                except ValueError:
                    conf = 1.0
                rows.append(
                    {
                        "created_date": "",
                        "technology": str(row.get("technology") or "").strip(),
                        "ips_title": title,
                        "predicted_category_existing": str(row.get("predicted_category_existing") or "").strip(),
                        "predicted_category_model": str(row.get("predicted_category_model") or "").strip(),
                        "confidence": f"{conf:.4f}",
                        "human_category": existing_label,
                        "label_notes": f"RE-VERIFY (model conf={conf:.2f})",
                        "ips_id": str(row.get("ips_id") or "").strip(),
                    }
                )
    except Exception as exc:
        print(f"[WARN] Could not read low-confidence CSV {csv_path}: {exc}")
        return []
    rows.sort(key=lambda r: float(r["confidence"]))
    return rows[:top_n]


def _load_targeted_supplement(csv_path: str, top_n: int = 0) -> List[Dict[str, str]]:
    """Load targeted supplement rows (created by prepare_targeted_labeling_supplement.py).

    If top_n <= 0, all rows are returned.
    """
    if not csv_path or not os.path.isfile(csv_path):
        return []

    rows: List[Dict[str, str]] = []
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                title = str(row.get("ips_title") or "").strip()
                if not title:
                    continue
                rows.append(
                    {
                        "created_date": str(row.get("created_date") or "").strip(),
                        "technology": str(row.get("technology") or "").strip(),
                        "ips_title": title,
                        "predicted_category_existing": str(row.get("predicted_category_existing") or "").strip(),
                        "predicted_category_model": str(row.get("predicted_category_model") or "").strip(),
                        "confidence": str(row.get("confidence") or "").strip(),
                        "human_category": str(row.get("human_category") or "").strip(),
                        "label_notes": str(row.get("label_notes") or "").strip(),
                        "ips_id": str(row.get("ips_id") or "").strip(),
                    }
                )
    except Exception as exc:
        print(f"[WARN] Could not read targeted supplement CSV {csv_path}: {exc}")
        return []

    if top_n and top_n > 0:
        return rows[:top_n]
    return rows


def _dedupe_issue_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Deduplicate rows by (ips_id, title) with a title fallback."""
    deduped: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for row in rows:
        ips_id = str(row.get("ips_id") or "").strip().lower()
        title = str(row.get("ips_title") or "").strip().lower()
        key = (ips_id, title)
        if not ips_id:
            key = ("", title)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _parse_reference_date(value: str) -> date:
    text = str(value or "").strip()
    if not text:
        return datetime.now().date()
    return datetime.strptime(text, "%Y-%m-%d").date()


def _last_week_range(reference_date: date) -> Tuple[date, date]:
    # Previous full week: Monday 00:00 to next Monday 00:00.
    current_week_start = reference_date - timedelta(days=reference_date.weekday())
    last_week_start = current_week_start - timedelta(days=7)
    last_week_end = current_week_start
    return last_week_start, last_week_end


def _technology_expr(columns: set[str]) -> str:
    if _has(columns, "technology"):
        return "technology"
    if _has(columns, "bug_project"):
        return (
            "CASE "
            "WHEN LOWER(TRIM(COALESCE(bug_project::text, ''))) = 'wifi' THEN 'WiFi' "
            "WHEN LOWER(TRIM(COALESCE(bug_project::text, ''))) = 'bt' THEN 'BT' "
            "WHEN LOWER(TRIM(COALESCE(bug_project::text, ''))) = 'cie' THEN 'Software' "
            "WHEN LOWER(TRIM(COALESCE(bug_project::text, ''))) = 'wot' THEN 'Tools' "
            "ELSE NULL END"
        )
    return "NULL"


def _existing_pred_expr(columns: set[str]) -> str:
    for name in ["predicted_category", "issue_category", "human_category"]:
        if _has(columns, name):
            return f"NULLIF(NULLIF(TRIM({name}::text), ''), 'NA')"
    return "NULL"


def _description_expr(columns: set[str]) -> str:
    for name in ["ips_description", "jira_description", "description"]:
        if _has(columns, name):
            return f"NULLIF(NULLIF(TRIM({name}::text), ''), 'NA')"
    return "NULL"


def _extract_new_issues_from_last_week(
    *,
    table: str,
    week_start: date,
    week_end: date,
) -> List[Dict[str, str]]:
    db = DbConnector()
    columns = _get_table_columns(db, table)
    created_expr = _created_date_expr(columns)
    if created_expr == "NULL":
        raise RuntimeError(f"No created-date columns available in table: {table}")

    title_expr = _title_expr(columns)
    description_expr = _description_expr(columns)
    technology_expr = _technology_expr(columns)
    predicted_expr = _existing_pred_expr(columns)

    ips_case_expr = "NULLIF(NULLIF(TRIM(ips_case_number::text), ''), 'NA')" if _has(columns, "ips_case_number") else "NULL"

    query = f"""
        WITH src AS (
            SELECT
                {created_expr} AS created_date,
                {title_expr} AS ips_title,
                {description_expr} AS ips_description,
                {technology_expr} AS technology,
                {predicted_expr} AS predicted_category_existing,
                {ips_case_expr} AS ips_id
            FROM {table}
        )
        SELECT
            created_date::text AS created_date,
            COALESCE(ips_title, '') AS ips_title,
            COALESCE(ips_description, '') AS ips_description,
            COALESCE(technology::text, '') AS technology,
            COALESCE(predicted_category_existing::text, '') AS predicted_category_existing,
            COALESCE(ips_id::text, '') AS ips_id
        FROM src
        WHERE created_date >= DATE '{week_start.isoformat()}'
          AND created_date < DATE '{week_end.isoformat()}'
          AND ips_title IS NOT NULL
          AND TRIM(ips_title) <> ''
        ORDER BY created_date DESC;
    """
    rows = db.query_rows(query, None)

    deduped: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for row in rows:
        title = str(row.get("ips_title") or "").strip().replace("\r", "").replace("\n", " ")
        tech = str(row.get("technology") or "").strip()
        key = (tech.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "created_date": str(row.get("created_date") or "").strip(),
                "technology": tech,
                "ips_title": title,
                "ips_description": str(row.get("ips_description") or "").strip().replace("\r", " ").replace("\n", " "),
                "predicted_category_existing": str(row.get("predicted_category_existing") or "").strip(),
                "ips_id": str(row.get("ips_id") or "").strip(),
            }
        )
    return deduped


def _score_model_confidence(
    rows: List[Dict[str, str]],
    *,
    model_path: str,
) -> List[Dict[str, str]]:
    model_bundle = load_category_model(model_path)
    scored: List[Dict[str, str]] = []
    for row in rows:
        category, confidence = classify_issue_title(
            model_bundle,
            row.get("ips_title", ""),
            predicted_category=row.get("predicted_category_existing", ""),
            technology=row.get("technology", ""),
            description=row.get("ips_description", ""),
        )
        copied = dict(row)
        copied["predicted_category_model"] = str(category)
        copied["confidence"] = f"{float(confidence):.4f}"
        scored.append(copied)
    return scored


def _to_float(value: Any) -> float:
    try:
        return float(str(value or "").strip())
    except Exception:
        return 1.0


def _resolve_top_n(value: str, total_rows: int) -> int:
    text = str(value or "").strip().lower()
    if text in {"all", "*", "max"}:
        return max(0, total_rows)
    try:
        parsed = int(text)
    except Exception:
        raise ValueError(f"Invalid --top-n value: {value}. Use integer or 'all'.")
    if parsed <= 0:
        return max(0, total_rows)
    return parsed


def _looks_like_distribution_alias(email: str) -> bool:
    local = str(email or "").split("@", 1)[0].lower()
    return local.startswith("ccg.") or local.endswith(".gb")


def _dedupe_emails(items: List[Any]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for item in items:
        email = str(item or "").strip().lower()
        if not email or "@" not in email:
            continue
        if _looks_like_distribution_alias(email):
            continue
        if email in seen:
            continue
        seen.add(email)
        deduped.append(email)
    return deduped


def _load_assignees_from_recipients(path: str) -> Dict[str, List[str]]:
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Recipients file not found: {path}")

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError(f"Invalid recipients file format: {path}")

    raw_to = payload.get("to")
    if not isinstance(raw_to, list):
        raise ValueError(f"Recipients file missing 'to' list: {path}")

    tech_map = payload.get("technology_assignee_map")
    has_explicit_tech_map = isinstance(tech_map, dict)
    if has_explicit_tech_map:
        wifi = _dedupe_emails(list(tech_map.get("wifi") or []))
        bt = _dedupe_emails(list(tech_map.get("bt") or []))
    else:
        wifi = []
        bt = []

    everyone = _dedupe_emails(raw_to)
    if not everyone:
        raise RuntimeError("No individual assignees found in recipients.json 'to' list.")

    if not wifi:
        wifi = list(everyone)
    if not bt:
        bt = list(everyone)

    if has_explicit_tech_map:
        all_pool = _dedupe_emails(wifi + bt)
    else:
        all_pool = list(everyone)

    return {
        "wifi": wifi,
        "bt": bt,
        "all": all_pool,
    }


def _team_from_technology(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"wifi", "wi-fi", "wlan"}:
        return "wifi"
    if text in {"bt", "bluetooth"}:
        return "bt"
    return "other"


def _assign_rows_round_robin(rows: List[Dict[str, str]], assignee_map: Dict[str, List[str]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    idx_map = {"wifi": 0, "bt": 0, "other": 0}

    wifi_pool = list(assignee_map.get("wifi") or [])
    bt_pool = list(assignee_map.get("bt") or [])
    all_pool = list(assignee_map.get("all") or [])

    for email in set(wifi_pool + bt_pool + all_pool):
        counts[email] = 0

    for row in rows:
        team = _team_from_technology(row.get("technology", ""))
        if team == "wifi":
            pool = wifi_pool or all_pool
            team_label = "wifi"
        elif team == "bt":
            pool = bt_pool or all_pool
            team_label = "bt"
        else:
            pool = all_pool
            team_label = "other"

        if not pool:
            row["assignee_email"] = ""
            row["assignee_team"] = team_label
            continue

        pointer = idx_map.get(team_label, 0)
        assignee = pool[pointer % len(pool)]
        idx_map[team_label] = pointer + 1

        row["assignee_email"] = assignee
        row["assignee_team"] = team_label
        counts[assignee] = int(counts.get(assignee, 0)) + 1

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare weekly labeling template from new issues in last week.")
    parser.add_argument("--tuning-root", default="tuning_outputs")
    parser.add_argument("--weekly-dir", default="")
    parser.add_argument("--table", default="ips_jira_bugs")
    parser.add_argument("--model", default=os.path.join("models", "issue_category_model.joblib"))
    parser.add_argument("--recipients", default="recipients.json")
    parser.add_argument("--reference-date", default="", help="YYYY-MM-DD; defaults to today")
    parser.add_argument("--top-n", default="50", help="Number of new-issue rows to output, or 'all'")
    parser.add_argument("--low-conf-n", type=int, default=10, help="Number of low-confidence re-verify rows to mix in")
    parser.add_argument("--low-conf-csv", default="", help="Path to low_confidence_candidates.csv; auto-discovered if not set")
    parser.add_argument("--supplement-csv", default="", help="Path to targeted_labeling_supplement CSV; auto-discovered if not set")
    parser.add_argument("--supplement-n", type=int, default=0, help="Number of supplement rows to mix in; 0 means all")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    weekly_dir = args.weekly_dir.strip() if args.weekly_dir else _find_latest_weekly_dir(args.tuning_root)
    ref_date = _parse_reference_date(args.reference_date)
    week_start, week_end = _last_week_range(ref_date)

    raw_rows = _extract_new_issues_from_last_week(
        table=str(args.table or "").strip(),
        week_start=week_start,
        week_end=week_end,
    )
    if not raw_rows:
        print(f"[WARN] No new issues found for last week range {week_start} to {week_end}.")
    scored_rows = _score_model_confidence(raw_rows, model_path=str(args.model or "").strip())

    scored_rows.sort(key=lambda r: _to_float(r.get("confidence", "1.0")))

    top_n = _resolve_top_n(str(args.top_n), len(scored_rows))
    selected_new = scored_rows[:top_n] if top_n > 0 else []

    # Mix in low-confidence re-verify rows from the previous tuning run.
    low_conf_csv = str(args.low_conf_csv or "").strip()
    if not low_conf_csv:
        low_conf_csv = _find_latest_low_conf_csv(str(args.tuning_root), exclude_dir=weekly_dir)
    low_conf_rows = _load_low_confidence_candidates(low_conf_csv, top_n=int(args.low_conf_n))
    if low_conf_rows:
        print(f"[INFO] Mixing in {len(low_conf_rows)} low-confidence re-verify rows from: {low_conf_csv}")
    else:
        print("[INFO] No low-confidence re-verify rows found (first run or file missing).")

    supplement_csv = str(args.supplement_csv or "").strip()
    if not supplement_csv:
        supplement_csv = _find_latest_targeted_supplement()
    supplement_rows = _load_targeted_supplement(supplement_csv, top_n=int(args.supplement_n))
    if supplement_rows:
        print(f"[INFO] Mixing in {len(supplement_rows)} targeted supplement rows from: {supplement_csv}")
    else:
        print("[INFO] No targeted supplement rows found to mix in.")

    selected = _dedupe_issue_rows(selected_new + low_conf_rows + supplement_rows)
    assignee_map = _load_assignees_from_recipients(str(args.recipients or "").strip())
    assign_counts = _assign_rows_round_robin(selected, assignee_map)

    output_path = args.output.strip() if args.output else os.path.join(weekly_dir, "weekly_labeling_template.csv")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "created_date",
                "assignee_email",
                "assignee_team",
                "technology",
                "ips_id",
                "ips_title",
                "predicted_category_existing",
                "predicted_category_model",
                "confidence",
                "human_category",
                "label_notes",
            ],
        )
        writer.writeheader()
        for r in selected:
            writer.writerow(
                {
                    "created_date": (r.get("created_date") or "").strip(),
                    "assignee_email": (r.get("assignee_email") or "").strip(),
                    "assignee_team": (r.get("assignee_team") or "").strip(),
                    "technology": (r.get("technology") or "").strip(),
                    "ips_id": (r.get("ips_id") or "").strip(),
                    "ips_title": (r.get("ips_title") or "").strip(),
                    "predicted_category_existing": (r.get("predicted_category_existing") or "").strip(),
                    "predicted_category_model": (r.get("predicted_category_model") or "").strip(),
                    "confidence": (r.get("confidence") or "").strip(),
                    "human_category": (r.get("human_category") or "").strip(),
                    "label_notes": (r.get("label_notes") or "").strip(),
                }
            )

    print(f"[OK] Weekly labeling template: {output_path}")
    print(f"[OK] Last week range: {week_start} to {week_end}")
    print(f"[OK] New issues found: {len(raw_rows)}")
    print(f"[OK] New-issue rows prepared: {len(selected_new)}")
    print(f"[OK] Low-confidence re-verify rows: {len(low_conf_rows)}")
    print(f"[OK] Targeted supplement rows: {len(supplement_rows)}")
    print(f"[OK] Total rows in template: {len(selected)}")
    print(f"[OK] WiFi assignees: {len(assignee_map.get('wifi') or [])}")
    print(f"[OK] BT assignees: {len(assignee_map.get('bt') or [])}")
    print(f"[OK] Assignees loaded: {len(assignee_map.get('all') or [])}")
    for email in assignee_map.get("all") or []:
        print(f"[OK] Assigned {assign_counts.get(email, 0):>3} issue(s) -> {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
