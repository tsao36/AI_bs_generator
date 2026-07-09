"""Validate LLM category predictions against rule-based cues.

Reads a CSV with columns:
    technology key, ips_title, predicted_category, jira_title

Rules:
- If jira_title is present/non-empty, use it as the primary text for classification; otherwise fallback to ips_title.
- Apply hard overrides and cue matches from bug_category_config.json.
- Filter cue hits by technology matrix when available.
- Pick the category by precedence; fallback to "Need-Triage" when no cues fire.

Outputs a mismatch report and overall accuracy, with optional CSV export.
Can also compute metrics against a labeled column and export samples for human review.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
from typing import Dict, List, Sequence, Tuple

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "bug_category_config.json")
DEFAULT_INPUT = os.path.join(os.path.dirname(__file__), "2025_issue_category_sample.csv")
FALLBACK_CATEGORY = "Need-Triage"


def _load_config(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_text(value: str) -> str:
    return (value or "").strip().lower()


def _nonempty(value: str) -> bool:
    return bool(_normalize_text(value)) and _normalize_text(value) not in {"na", "n/a", "none"}


def _allowed_for_technology(matrix: List[Dict], tech: str) -> List[str]:
    tech_key = _normalize_text(tech)
    return [row.get("issue_type") for row in matrix if _normalize_text(row.get("technology")) == tech_key]


def _apply_hard_overrides(text: str, overrides: Sequence[Dict], candidates: Sequence[str]) -> str | None:
    lower_text = text.lower()
    for rule in overrides:
        phrase = _normalize_text(rule.get("phrase"))
        if not phrase or phrase not in lower_text:
            continue
        unless = {_normalize_text(c) for c in rule.get("unless", []) if _normalize_text(c)}
        if unless and any(cat.lower() in unless for cat in candidates):
            continue
        category = rule.get("category")
        if category:
            return category
    return None


def _derive_category(
    text: str,
    technology: str,
    config: Dict,
) -> Tuple[str, List[str], str | None, str]:
    lower_text = text.lower()
    cues: Dict[str, List[str]] = config.get("category_cues", {})
    precedence: List[str] = config.get("precedence", [])
    overrides: List[Dict] = config.get("hard_overrides", [])
    matrix: List[Dict] = config.get("category_matrix", [])

    candidates: List[str] = []
    for category, cue_list in cues.items():
        for cue in cue_list:
            cue_norm = _normalize_text(cue)
            if cue_norm and cue_norm in lower_text:
                candidates.append(category)
                break

    # Filter by technology when we have a matrix entry.
    allowed = _allowed_for_technology(matrix, technology)
    if allowed:
        filtered = [c for c in candidates if c in allowed]
        if filtered:
            candidates = filtered

    override = _apply_hard_overrides(text, overrides, candidates)
    if override:
        return override, candidates, override, "override"

    # Deduplicate preserving order
    seen = set()
    deduped: List[str] = []
    for cat in candidates:
        key = cat.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cat)
    candidates = deduped

    if candidates:
        prec_map = {p.lower(): idx for idx, p in enumerate(precedence)}
        candidates.sort(key=lambda c: prec_map.get(c.lower(), len(prec_map)))
        return candidates[0], candidates, None, "cue"

    return FALLBACK_CATEGORY, [], None, "fallback"


def _pick_text(jira_title: str, ips_title: str) -> str:
    if _nonempty(jira_title):
        return jira_title
    return ips_title or ""


def evaluate_predictions(csv_path: str, config_path: str, label_col: str | None) -> Tuple[List[Dict], List[Dict], Dict]:
    config = _load_config(config_path)
    rows: List[Dict] = []
    mismatches: List[Dict] = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            tech = row.get("technology key") or row.get("technology") or ""
            ips_title = row.get("ips_title") or ""
            jira_title = row.get("jira_title") or ""
            predicted = (row.get("predicted_category") or "").strip()
            label = (row.get(label_col) or "").strip() if label_col else ""
            text = _pick_text(jira_title, ips_title)
            derived, candidates, override, source = _derive_category(text, tech, config)
            result = {
                "row": idx,
                "technology": tech,
                "text_used": text,
                "predicted": predicted,
                "derived": derived,
                "override": override or "",
                "decision_source": source,
                "candidates": ", ".join(candidates) if candidates else "",
                "jira_title": jira_title,
                "ips_title": ips_title,
                "label": label,
            }
            rows.append(result)
            if derived.lower() != predicted.lower():
                mismatches.append(result)

    summary = {
        "total": len(rows),
        "mismatches": len(mismatches),
        "accuracy": 0.0 if not rows else (len(rows) - len(mismatches)) / len(rows),
    }
    return rows, mismatches, summary


def _write_csv(rows: Sequence[Dict], path: str) -> None:
    fieldnames = [
        "row",
        "technology",
        "predicted",
        "derived",
        "override",
        "decision_source",
        "candidates",
        "label",
        "jira_title",
        "ips_title",
        "text_used",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _metrics(rows: Sequence[Dict], label_col: str) -> Dict:
    truthy = [r for r in rows if r.get(label_col)]
    if not truthy:
        return {}

    def score(pred_key: str) -> Dict:
        tp_counts: Dict[Tuple[str, str], int] = {}
        support: Dict[str, int] = {}
        pred_counts: Dict[str, int] = {}
        for r in truthy:
            true = r[label_col].lower()
            pred = (r.get(pred_key) or "").lower()
            support[true] = support.get(true, 0) + 1
            pred_counts[pred] = pred_counts.get(pred, 0) + 1
            tp_counts[(true, pred)] = tp_counts.get((true, pred), 0) + 1

        labels = sorted(set(support) | {p for (_, p) in tp_counts})
        per_label: Dict[str, Dict] = {}
        for lbl in labels:
            tp = tp_counts.get((lbl, lbl), 0)
            fp = sum(v for (t, p), v in tp_counts.items() if p == lbl and t != lbl)
            fn = sum(v for (t, p), v in tp_counts.items() if t == lbl and p != lbl)
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) else 0.0
            per_label[lbl] = {"support": support.get(lbl, 0), "precision": precision, "recall": recall, "f1": f1}

        micro_tp = sum(tp_counts.get((lbl, lbl), 0) for lbl in labels)
        total = len(truthy)
        micro_precision = micro_tp / total if total else 0.0
        micro_recall = micro_tp / total if total else 0.0
        micro_f1 = micro_precision  # same for single-label classification
        macro_f1 = sum(v["f1"] for v in per_label.values()) / len(per_label) if per_label else 0.0

        confusion: Dict[str, Dict[str, int]] = {t: {} for t in labels}
        for (t, p), v in tp_counts.items():
            confusion.setdefault(t, {})[p] = v

        return {
            "per_label": per_label,
            "micro_f1": micro_f1,
            "macro_f1": macro_f1,
            "confusion": confusion,
            "support": support,
        }

    return {
        "predicted_vs_label": score("predicted"),
        "derived_vs_label": score("derived"),
    }


def _write_sample(rows: Sequence[Dict], path: str, size: int, seed: int | None) -> None:
    if not rows:
        return
    rng = random.Random(seed)
    take = min(len(rows), size)
    selected = rng.sample(rows, take)
    _write_csv(selected, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check LLM issue category predictions against rules.")
    parser.add_argument("--csv", default=DEFAULT_INPUT, help="Input CSV path (default: 2025_issue_category_sample.csv)")
    parser.add_argument("--config", default=CONFIG_PATH, help="Category config path (default: bug_category_config.json)")
    parser.add_argument("--limit", type=int, default=50, help="Max mismatches to print (default: 50)")
    parser.add_argument(
        "--export",
        default=None,
        help="Optional path to write mismatches as CSV (columns include row, technology, predicted, derived, cues)",
    )
    parser.add_argument("--export-full", default=None, help="Optional path to write all rows with decisions")
    parser.add_argument(
        "--label-col",
        default=None,
        help="Optional column name in CSV that contains ground-truth labels for metrics and sampling",
    )
    parser.add_argument("--sample-path", default=None, help="Optional path to write a sample for human review")
    parser.add_argument("--sample-size", type=int, default=50, help="Sample size for human review (default: 50)")
    parser.add_argument("--sample-seed", type=int, default=13, help="Random seed for sampling (default: 13)")
    args = parser.parse_args()

    rows, mismatches, summary = evaluate_predictions(args.csv, args.config, args.label_col)

    print("Checking `predicted_category` vs rule-based `derived` category using config cues/overrides.")
    print("- Priority: jira_title is used when present; otherwise ips_title is used.")
    print("- Derivation: cue hits filtered by technology matrix, with hard overrides, precedence ordering, and Need-Triage fallback.")

    print(f"Total rows: {summary['total']}")
    print(f"Mismatches: {summary['mismatches']} (accuracy: {summary['accuracy']*100:.1f}%)")

    if args.label_col:
        metrics = _metrics(rows, "label")
        if metrics:
            print(f"\nMetrics vs ground truth (column: {args.label_col}):")
            for key, data in metrics.items():
                print(f"- {key}: micro_f1={data['micro_f1']:.3f}, macro_f1={data['macro_f1']:.3f}")
        else:
            print(f"\nLabel column '{args.label_col}' not found or empty; metrics skipped.")

    if mismatches:
        print("\nSample mismatches:")
        for item in mismatches[: args.limit]:
            print("- Row {row}: predicted='{predicted}' derived='{derived}' tech='{technology}'".format(**item))
            print(f"  Text used: {item['text_used'][:200]}")
            print(f"  Source: {item['decision_source']}")
            if item["override"]:
                print(f"  Override: {item['override']}")
            if item["candidates"]:
                print(f"  Cue hits: {item['candidates']}")
        if len(mismatches) > args.limit:
            print(f"... {len(mismatches) - args.limit} more mismatches not shown")

    if args.export:
        _write_csv(mismatches, args.export)
        print(f"\nWrote {len(mismatches)} mismatches to {args.export}")

    if args.export_full:
        _write_csv(rows, args.export_full)
        print(f"Wrote all {len(rows)} rows with decisions to {args.export_full}")

    if args.sample_path:
        base = mismatches if mismatches else rows
        _write_sample(base, args.sample_path, args.sample_size, args.sample_seed)
        print(f"Wrote sample of up to {args.sample_size} rows to {args.sample_path} for human review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
