from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from collections import defaultdict
from typing import Dict, List


def _clean(value: object) -> str:
    return str(value or "").strip()


def _is_missing(value: object) -> bool:
    return _clean(value).lower() in {"", "na", "n/a", "none", "null"}


def _find_latest_weekly_dir(tuning_root: str) -> str:
    candidates = [p for p in glob.glob(os.path.join(tuning_root, "weekly_*")) if os.path.isdir(p)]
    if not candidates:
        raise FileNotFoundError(f"No weekly output folder found under: {tuning_root}")
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for weekly_dir in candidates:
        template_path = os.path.join(weekly_dir, "weekly_labeling_template.csv")
        if os.path.exists(template_path):
            return weekly_dir
    raise FileNotFoundError(
        f"No weekly_labeling_template.csv found under any weekly_* folder in: {tuning_root}"
    )


def _load_rows(path: str) -> List[Dict[str, str]]:
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    last_error: Exception | None = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Check weekly labeling completion status.")
    parser.add_argument("--tuning-root", default="tuning_outputs")
    parser.add_argument("--weekly-dir", default="")
    parser.add_argument("--csv", default="")
    parser.add_argument("--out-pending-csv", default="")
    args = parser.parse_args()

    if args.csv:
        csv_path = os.path.abspath(_clean(args.csv))
        weekly_dir = os.path.dirname(csv_path)
    else:
        weekly_dir = _clean(args.weekly_dir)
        if not weekly_dir:
            weekly_dir = _find_latest_weekly_dir(_clean(args.tuning_root))
        csv_path = os.path.abspath(os.path.join(weekly_dir, "weekly_labeling_template.csv"))

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"weekly_labeling_template.csv not found: {csv_path}")

    rows = _load_rows(csv_path)
    if not rows:
        summary = {
            "weekly_dir": os.path.abspath(weekly_dir),
            "csv": csv_path,
            "total_rows": 0,
            "filled_rows": 0,
            "pending_rows": 0,
            "pending_by_assignee": {},
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    required = {"human_category", "assignee_email", "ips_title"}
    fieldnames = set(rows[0].keys())
    missing_cols = sorted([c for c in required if c not in fieldnames])
    if missing_cols:
        raise RuntimeError(f"CSV missing required columns: {missing_cols}")

    pending_by_assignee: Dict[str, int] = defaultdict(int)
    pending_rows_for_export: List[Dict[str, str]] = []
    pending = 0
    filled = 0

    for row in rows:
        if _is_missing(row.get("human_category")):
            pending += 1
            assignee = _clean(row.get("assignee_email")).lower() or "(unassigned)"
            pending_by_assignee[assignee] += 1
            pending_rows_for_export.append(
                {
                    "assignee_email": _clean(row.get("assignee_email")),
                    "assignee_team": _clean(row.get("assignee_team")),
                    "technology": _clean(row.get("technology")),
                    "created_date": _clean(row.get("created_date")),
                    "ips_title": _clean(row.get("ips_title")),
                }
            )
        else:
            filled += 1

    if pending_rows_for_export:
        out_pending_csv = _clean(args.out_pending_csv)
        if not out_pending_csv:
            out_pending_csv = os.path.join(weekly_dir, "pending_human_category_rows.csv")
        os.makedirs(os.path.dirname(out_pending_csv) or ".", exist_ok=True)
        with open(out_pending_csv, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["assignee_email", "assignee_team", "technology", "created_date", "ips_title"],
            )
            writer.writeheader()
            writer.writerows(pending_rows_for_export)

    summary = {
        "weekly_dir": os.path.abspath(weekly_dir),
        "csv": csv_path,
        "total_rows": len(rows),
        "filled_rows": filled,
        "pending_rows": pending,
        "pending_by_assignee": dict(sorted(pending_by_assignee.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    return 0 if pending == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
