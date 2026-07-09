from __future__ import annotations

import argparse
import csv
import json
import os
from typing import Dict, List


def _clean(value: object) -> str:
    return str(value or "").strip()


def _norm(value: object) -> str:
    return _clean(value).lower()


def _is_yes(value: object) -> bool:
    return _norm(value) in {"y", "yes", "true", "1"}


def _read_csv(path: str) -> List[Dict[str, str]]:
    encodings = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except Exception:
            continue
    raise RuntimeError(f"Unable to read CSV: {path}")


def _decide_status(row: Dict[str, str]) -> str:
    human = _clean(row.get("human_category_current"))
    pred = _clean(row.get("model_predicted"))
    new_human = _clean(row.get("new_human_category"))
    secondary = _clean(row.get("secondary_category"))
    keep_current = _is_yes(row.get("keep_current_label"))
    dual_valid = _is_yes(row.get("is_dual_valid"))

    if keep_current and not new_human:
        new_human = human

    if not keep_current and not new_human and not secondary and not dual_valid:
        return "pending_review"

    if pred and new_human and _norm(pred) == _norm(new_human):
        return "resolved_by_relabel"

    if pred and keep_current and _norm(pred) == _norm(human):
        return "model_correct_keep_current"

    if pred and secondary and dual_valid and _norm(pred) == _norm(secondary):
        return "acceptable_alternative"

    if pred and keep_current and secondary and dual_valid and _norm(pred) == _norm(secondary):
        return "acceptable_alternative"

    return "hard_wrong_or_unclear"


def _write_csv(path: str, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Split reviewed mismatches into hard wrong vs acceptable alternative.")
    parser.add_argument("--review-template", required=True)
    parser.add_argument("--out-summary-json", required=True)
    parser.add_argument("--out-all-csv", required=True)
    parser.add_argument("--out-hard-wrong-csv", required=True)
    parser.add_argument("--out-acceptable-csv", required=True)
    args = parser.parse_args()

    rows = _read_csv(args.review_template)

    enriched: List[Dict[str, str]] = []
    for row in rows:
        rec = dict(row)
        rec["review_status"] = _decide_status(row)
        enriched.append(rec)

    acceptable = [r for r in enriched if r["review_status"] == "acceptable_alternative"]
    hard_wrong = [
        r
        for r in enriched
        if r["review_status"] in {"hard_wrong_or_unclear", "resolved_by_relabel", "model_correct_keep_current"}
    ]

    summary = {
        "total_rows": len(enriched),
        "pending_review": sum(1 for r in enriched if r["review_status"] == "pending_review"),
        "acceptable_alternative": len(acceptable),
        "hard_wrong_or_unclear": sum(1 for r in enriched if r["review_status"] == "hard_wrong_or_unclear"),
        "resolved_by_relabel": sum(1 for r in enriched if r["review_status"] == "resolved_by_relabel"),
        "model_correct_keep_current": sum(1 for r in enriched if r["review_status"] == "model_correct_keep_current"),
    }

    fieldnames = list(enriched[0].keys()) if enriched else ["review_status"]
    _write_csv(args.out_all_csv, enriched, fieldnames)
    _write_csv(args.out_hard_wrong_csv, hard_wrong, fieldnames)
    _write_csv(args.out_acceptable_csv, acceptable, fieldnames)

    os.makedirs(os.path.dirname(args.out_summary_json), exist_ok=True)
    with open(args.out_summary_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(json.dumps(summary, ensure_ascii=False))
    print(f"All rows report: {args.out_all_csv}")
    print(f"Hard wrong report: {args.out_hard_wrong_csv}")
    print(f"Acceptable alternative report: {args.out_acceptable_csv}")
    print(f"Summary: {args.out_summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
