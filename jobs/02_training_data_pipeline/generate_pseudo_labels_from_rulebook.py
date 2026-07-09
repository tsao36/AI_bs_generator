from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, Tuple


def _clean(value: object) -> str:
    return str(value or "").strip()


def _load_rules(rulebook_csv: Path, auto_enable_hard: bool) -> Dict[str, Tuple[str, str]]:
    if not rulebook_csv.exists():
        raise FileNotFoundError(f"Rulebook not found: {rulebook_csv}")

    rules: Dict[str, Tuple[str, str]] = {}
    with rulebook_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            comp = _clean(row.get("component"))
            if not comp:
                continue

            approved = _clean(row.get("approved_category"))
            enabled = _clean(row.get("enabled")).lower()
            level = _clean(row.get("rule_level")).lower() or "review"
            suggested = _clean(row.get("suggested_category"))

            category = approved or suggested
            if not category:
                continue

            is_enabled = enabled in {"1", "true", "yes", "y", "on"}
            if not is_enabled and auto_enable_hard and level == "hard":
                is_enabled = True

            if is_enabled:
                rules[comp] = (category, level)

    return rules


def _level_weight(level: str) -> float:
    level = (level or "").lower()
    if level == "hard":
        return 0.7
    if level == "soft":
        return 0.35
    return 0.2


def generate_pseudo(input_csv: Path, rulebook_csv: Path, output_csv: Path, auto_enable_hard: bool) -> dict:
    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    rules = _load_rules(rulebook_csv, auto_enable_hard=auto_enable_hard)

    total = 0
    kept = 0
    by_level: Dict[str, int] = {"hard": 0, "soft": 0, "review": 0}

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with input_csv.open("r", encoding="utf-8", newline="") as src, output_csv.open(
        "w", encoding="utf-8", newline=""
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(
            dst,
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

        for row in reader:
            total += 1
            comp = _clean(row.get("jira_component"))
            title = _clean(row.get("ips_title"))
            if not title or title.upper() == "NA":
                continue
            if comp not in rules:
                continue

            category, level = rules[comp]
            by_level[level] = by_level.get(level, 0) + 1
            weight = _level_weight(level)
            confidence = 0.95 if level == "hard" else 0.75 if level == "soft" else 0.6

            writer.writerow(
                {
                    "jira_id": _clean(row.get("jira_id")),
                    "ips_title": title,
                    "jira_component": comp,
                    "human_category": category,
                    "label_source": f"pseudo_component_rule_{level}",
                    "pseudo_confidence": f"{confidence:.2f}",
                    "sample_weight": f"{weight:.2f}",
                    "feature_text": _clean(row.get("feature_text")),
                }
            )
            kept += 1

    return {
        "rows_in": total,
        "rows_out": kept,
        "rules_enabled": len(rules),
        "by_level": by_level,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate pseudo labels from approved component rulebook.")
    parser.add_argument("--input-csv", default="training_title_component_pairs.csv")
    parser.add_argument("--rulebook-csv", default="component_category_rulebook_candidates.csv")
    parser.add_argument("--output-csv", default="pseudo_labels_from_component_rules.csv")
    parser.add_argument("--auto-enable-hard", action="store_true")
    args = parser.parse_args()

    summary = generate_pseudo(
        input_csv=Path(args.input_csv),
        rulebook_csv=Path(args.rulebook_csv),
        output_csv=Path(args.output_csv),
        auto_enable_hard=args.auto_enable_hard,
    )

    print(f"[OK] wrote: {args.output_csv}")
    print(f"[INFO] rows_in={summary['rows_in']} rows_out={summary['rows_out']}")
    print(f"[INFO] rules_enabled={summary['rules_enabled']} by_level={summary['by_level']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
