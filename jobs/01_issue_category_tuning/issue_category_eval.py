#!/usr/bin/env python3
"""
Issue-category QA helper

Input CSV: 2025_issue_category_sample.csv
Columns expected:
  - technology
  - ips_title
  - predicted_category
  - jira_title (ignored for now)

What it does:
1) Creates a stratified + "risky" sample for human labeling.
2) After you fill in `human_category`, it scores accuracy/F1 + confusion matrix + top confusions.

Requires: pandas
Optional (recommended): scikit-learn (for nicer reports). Script works without it too.

USAGE
-----
# Step 1: create labeling sample (defaults are good)
python issue_category_eval.py sample

# Step 2: after labeling (fill human_category column), score it
python issue_category_eval.py score --labeled sample_for_labeling.csv
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


DEFAULT_INPUT = "2025_issue_category_sample.csv"


GENERIC_WORDS = {
    "issue",
    "problem",
    "bug",
    "error",
    "fail",
    "failure",
    "crash",
    "hang",
    "not working",
    "doesn't work",
    "doesnt work",
    "unable",
    "can't",
    "cant",
    "broken",
    "regression",
    "intermittent",
}


def _normalize_text(x: str) -> str:
    x = (x or "").strip()
    x = re.sub(r"\s+", " ", x)
    return x


def _title_risk_flags(title: str) -> Tuple[int, List[str]]:
    """
    Simple heuristics to oversample 'risky' titles (likely ambiguous / low-info).
    Higher score => more likely to be sampled in risky bucket.
    """
    t = _normalize_text(title)
    tl = t.lower()

    flags: List[str] = []
    score = 0

    # very short titles
    if len(t) <= 18:
        score += 3
        flags.append("very_short<=18c")
    elif len(t) <= 28:
        score += 2
        flags.append("short<=28c")

    # low word count
    wc = len(t.split())
    if wc <= 3:
        score += 3
        flags.append("few_words<=3")
    elif wc <= 5:
        score += 1
        flags.append("few_words<=5")

    # mostly generic wording
    for g in GENERIC_WORDS:
        if g in tl:
            score += 2
            flags.append(f"generic:{g}")
            break

    # no digits / versions / build hints can be a signal of low specificity
    if not re.search(r"\d", tl):
        score += 1
        flags.append("no_digits")

    # contains only broad placeholders
    if tl in {"issue", "problem", "bug", "error"}:
        score += 5
        flags.append("pure_generic")

    return score, flags


def _allocate_stratified(
    counts: pd.Series,
    total_n: int,
    min_per_class: int,
) -> Dict[str, int]:
    """
    Allocate sample sizes per class:
      - Try to give each class at least min_per_class (capped by availability).
      - Distribute remainder proportional to remaining capacity.
    """
    classes = list(counts.index)
    avail = counts.astype(int).to_dict()

    # Start with base allocation = min(min_per_class, available)
    alloc = {c: min(min_per_class, avail[c]) for c in classes}
    base_sum = sum(alloc.values())

    # If total_n is too small, do round-robin until we hit total_n.
    if total_n <= 0:
        return {c: 0 for c in classes}

    if base_sum > total_n:
        alloc = {c: 0 for c in classes}
        remaining = total_n
        # Round-robin assign 1 while capacity remains
        while remaining > 0:
            progressed = False
            for c in classes:
                if remaining <= 0:
                    break
                if alloc[c] < avail[c]:
                    alloc[c] += 1
                    remaining -= 1
                    progressed = True
            if not progressed:
                break
        return alloc

    remaining = total_n - base_sum
    if remaining == 0:
        return alloc

    # Remaining capacity per class
    cap = {c: max(0, avail[c] - alloc[c]) for c in classes}
    cap_sum = sum(cap.values())
    if cap_sum == 0:
        return alloc

    # Proportional distribution with largest remainder
    raw = {c: remaining * (cap[c] / cap_sum) for c in classes}
    add = {c: int(np.floor(raw[c])) for c in classes}
    used = sum(add.values())
    leftovers = remaining - used

    # Distribute leftovers by fractional remainder (descending)
    frac_sorted = sorted(
        classes,
        key=lambda c: (raw[c] - np.floor(raw[c])),
        reverse=True,
    )
    for c in frac_sorted:
        if leftovers <= 0:
            break
        if add[c] < cap[c]:
            add[c] += 1
            leftovers -= 1

    # Apply adds (capped again)
    for c in classes:
        alloc[c] += min(add[c], cap[c])

    return alloc


def create_labeling_sample(
    input_csv: str,
    out_csv: str,
    total_n: int = 600,
    risky_n: int = 100,
    min_per_class: int = 20,
    seed: int = 7,
) -> None:
    df = pd.read_csv(input_csv)

    required = {"technology_key", "ips_title", "predicted_category"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    # Clean / normalize
    df = df.copy()
    df["ips_title"] = df["ips_title"].fillna("").astype(str).map(_normalize_text)
    df["predicted_category"] = df["predicted_category"].fillna("").astype(str).map(_normalize_text)
    df["technology"] = df["technology_key"].fillna("").astype(str).map(_normalize_text)

    # Basic filtering: keep rows that have a predicted category + title
    df_valid = df[(df["predicted_category"] != "") & (df["ips_title"] != "")].copy()
    if df_valid.empty:
        raise ValueError("No valid rows found (need non-empty ips_title and predicted_category).")

    rng = np.random.default_rng(seed)

    # Stratified sample by predicted_category
    counts = df_valid["predicted_category"].value_counts()
    alloc = _allocate_stratified(counts, total_n=max(0, total_n - risky_n), min_per_class=min_per_class)

    strat_indices: List[int] = []
    for cat, n in alloc.items():
        if n <= 0:
            continue
        group_idx = df_valid.index[df_valid["predicted_category"] == cat].to_numpy()
        n = min(n, len(group_idx))
        chosen = rng.choice(group_idx, size=n, replace=False)
        strat_indices.extend(chosen.tolist())

    strat_set = set(strat_indices)

    # Risky sampling from remaining rows
    remaining_df = df_valid.loc[~df_valid.index.isin(strat_set)].copy()
    if risky_n > 0 and not remaining_df.empty:
        risk_scores = []
        risk_reasons = []
        for t in remaining_df["ips_title"].tolist():
            s, flags = _title_risk_flags(t)
            risk_scores.append(s)
            risk_reasons.append(";".join(flags) if flags else "")

        remaining_df["_risk_score"] = risk_scores
        remaining_df["_risk_reason"] = risk_reasons

        # Weighted sampling: prefer higher risk, but allow all
        weights = remaining_df["_risk_score"].astype(float).to_numpy()
        weights = np.clip(weights, 0.0, None)
        if weights.sum() == 0:
            weights = np.ones_like(weights)

        risky_n_eff = min(risky_n, len(remaining_df))
        chosen_risky = rng.choice(
            remaining_df.index.to_numpy(),
            size=risky_n_eff,
            replace=False,
            p=weights / weights.sum(),
        ).tolist()
    else:
        chosen_risky = []

    sample_idx = strat_indices + chosen_risky
    sample_df = df_valid.loc[sample_idx].copy()

    # Add risk reasons (for review)
    sample_df["_risk_score"] = 0
    sample_df["_risk_reason"] = ""
    for i in sample_df.index:
        s, flags = _title_risk_flags(sample_df.at[i, "ips_title"])
        sample_df.at[i, "_risk_score"] = s
        sample_df.at[i, "_risk_reason"] = ";".join(flags) if flags else ""

    # Build labeling template
    out = pd.DataFrame(
        {
            "row_id": sample_df.index.astype(int),
            "technology": sample_df["technology"],
            "ips_title": sample_df["ips_title"],
            "predicted_category": sample_df["predicted_category"],
            "human_category": "",  # <-- you fill this in
            "label_notes": "",
            "risk_score": sample_df["_risk_score"],
            "risk_reason": sample_df["_risk_reason"],
        }
    ).reset_index(drop=True)

    out.to_csv(out_csv, index=False)

    # Also write a quick class distribution summary
    summary_path = os.path.splitext(out_csv)[0] + "_summary.csv"
    dist = out["predicted_category"].value_counts().rename_axis("predicted_category").reset_index(name="sample_count")
    dist.to_csv(summary_path, index=False)

    print(f"[OK] Wrote labeling sample: {out_csv} ({len(out)} rows)")
    print(f"[OK] Wrote sample distribution: {summary_path}")
    print("Next: open the sample CSV, fill `human_category`, then run:")
    print(f"  python {os.path.basename(__file__)} score --labeled {out_csv}")


@dataclass
class Scores:
    accuracy: float
    macro_f1: float
    weighted_f1: float
    per_class: pd.DataFrame
    confusion: pd.DataFrame
    top_confusions: pd.DataFrame


def _compute_f1_from_confusion(conf: pd.DataFrame) -> Tuple[pd.DataFrame, float, float, float]:
    """
    Manual per-class precision/recall/F1 from a confusion matrix where:
      - rows = true labels
      - cols = predicted labels
    """
    labels = list(conf.index.union(conf.columns))
    conf2 = conf.reindex(index=labels, columns=labels, fill_value=0)

    tp = np.diag(conf2.to_numpy())
    support = conf2.sum(axis=1).to_numpy()  # true counts per class
    pred_sum = conf2.sum(axis=0).to_numpy()  # predicted counts per class

    precision = np.divide(tp, pred_sum, out=np.zeros_like(tp, dtype=float), where=pred_sum != 0)
    recall = np.divide(tp, support, out=np.zeros_like(tp, dtype=float), where=support != 0)
    f1 = np.divide(
        2 * precision * recall,
        (precision + recall),
        out=np.zeros_like(tp, dtype=float),
        where=(precision + recall) != 0,
    )

    per = pd.DataFrame(
        {
            "label": labels,
            "support": support.astype(int),
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    )

    # Macro F1: mean over classes that appear in true labels (support > 0)
    mask = per["support"].to_numpy() > 0
    macro_f1 = float(per.loc[mask, "f1"].mean()) if mask.any() else 0.0

    # Weighted F1: weighted by support
    total = per["support"].sum()
    weighted_f1 = float((per["f1"] * per["support"]).sum() / total) if total else 0.0

    # Accuracy: sum diagonal / total
    acc = float(tp.sum() / conf2.to_numpy().sum()) if conf2.to_numpy().sum() else 0.0

    return per, acc, macro_f1, weighted_f1


def score_labeled_file(
    labeled_csv: str,
    out_dir: str,
    pred_col: str = "predicted_category",
    label_col: str = "human_category",
    title_col: str = "ips_title",
) -> None:
    df = pd.read_csv(labeled_csv)

    for c in [pred_col, label_col]:
        if c not in df.columns:
            raise ValueError(f"Missing column '{c}' in {labeled_csv}")

    df = df.copy()
    df[pred_col] = df[pred_col].fillna("").astype(str).map(_normalize_text)
    df[label_col] = df[label_col].fillna("").astype(str).map(_normalize_text)

    # Keep labeled rows only
    scored = df[(df[pred_col] != "") & (df[label_col] != "")].copy()
    if scored.empty:
        raise ValueError(f"No labeled rows found. Fill '{label_col}' first.")

    os.makedirs(out_dir, exist_ok=True)

    # Confusion matrix (rows=true, cols=pred)
    conf = pd.crosstab(scored[label_col], scored[pred_col], dropna=False)

    per_class, acc, macro_f1, weighted_f1 = _compute_f1_from_confusion(conf)

    # Top confusions (off-diagonal)
    conf2 = conf.copy()
    labels = list(conf2.index.union(conf2.columns))
    conf2 = conf2.reindex(index=labels, columns=labels, fill_value=0)

    records = []
    for true_label in labels:
        for pred_label in labels:
            if true_label == pred_label:
                continue
            n = int(conf2.loc[true_label, pred_label])
            if n > 0:
                records.append((true_label, pred_label, n))

    top_conf = pd.DataFrame(records, columns=["true_label", "pred_label", "count"]).sort_values(
        "count", ascending=False
    )
    top_conf = top_conf.head(25).reset_index(drop=True)

    # Save outputs
    per_class_path = os.path.join(out_dir, "per_class_metrics.csv")
    conf_path = os.path.join(out_dir, "confusion_matrix.csv")
    top_conf_path = os.path.join(out_dir, "top_confusions.csv")
    summary_path = os.path.join(out_dir, "summary.txt")

    per_class.to_csv(per_class_path, index=False)
    conf2.to_csv(conf_path)
    top_conf.to_csv(top_conf_path, index=False)

    # Also export a focused error set for quick review (optional)
    if title_col in scored.columns:
        scored["is_correct"] = (scored[label_col] == scored[pred_col])
        errors = scored[~scored["is_correct"]].copy()
        errors_path = os.path.join(out_dir, "errors.csv")
        errors[[title_col, pred_col, label_col]].to_csv(errors_path, index=False)
    else:
        errors_path = None

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"Rows scored: {len(scored)}\n")
        f.write(f"Accuracy: {acc:.4f}\n")
        f.write(f"Macro F1: {macro_f1:.4f}\n")
        f.write(f"Weighted F1: {weighted_f1:.4f}\n")
        f.write("\nTop confusions (true -> pred):\n")
        if top_conf.empty:
            f.write("  (none)\n")
        else:
            for _, r in top_conf.iterrows():
                f.write(f"  {r['true_label']} -> {r['pred_label']}: {int(r['count'])}\n")

    print(f"[OK] Rows scored: {len(scored)}")
    print(f"[OK] Accuracy      : {acc:.4f}")
    print(f"[OK] Macro F1      : {macro_f1:.4f}")
    print(f"[OK] Weighted F1   : {weighted_f1:.4f}")
    print("")
    print(f"[OK] Wrote: {summary_path}")
    print(f"[OK] Wrote: {per_class_path}")
    print(f"[OK] Wrote: {conf_path}")
    print(f"[OK] Wrote: {top_conf_path}")
    if errors_path:
        print(f"[OK] Wrote: {errors_path}")


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Evaluate LLM-predicted issue categories with sampling + scoring.")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("sample", help="Create a stratified + risky sample for human labeling.")
    ps.add_argument("--input", default=DEFAULT_INPUT, help=f"Input CSV (default: {DEFAULT_INPUT})")
    ps.add_argument("--out", default="sample_for_labeling.csv", help="Output CSV to label (default: sample_for_labeling.csv)")
    ps.add_argument("--total", type=int, default=600, help="Total rows in labeling sample (default: 600)")
    ps.add_argument("--risky", type=int, default=100, help="Extra risky rows (included within total) (default: 100)")
    ps.add_argument("--min-per-class", type=int, default=20, help="Minimum per predicted category (default: 20)")
    ps.add_argument("--seed", type=int, default=7, help="Random seed (default: 7)")

    pe = sub.add_parser("score", help="Score a labeled file (after filling human_category).")
    pe.add_argument("--labeled", required=True, help="CSV you labeled (must contain human_category)")
    pe.add_argument("--outdir", default="eval_results", help="Output directory (default: eval_results)")
    pe.add_argument("--pred-col", default="predicted_category", help="Predicted column name")
    pe.add_argument("--label-col", default="human_category", help="Human label column name")
    pe.add_argument("--title-col", default="ips_title", help="Title column name (for exporting errors.csv)")

    return p


def main() -> None:
    args = build_argparser().parse_args()

    if args.cmd == "sample":
        if args.risky > args.total:
            raise ValueError("--risky must be <= --total")
        create_labeling_sample(
            input_csv=args.input,
            out_csv=args.out,
            total_n=args.total,
            risky_n=args.risky,
            min_per_class=args.min_per_class,
            seed=args.seed,
        )
    elif args.cmd == "score":
        score_labeled_file(
            labeled_csv=args.labeled,
            out_dir=args.outdir,
            pred_col=args.pred_col,
            label_col=args.label_col,
            title_col=args.title_col,
        )
    else:
        raise RuntimeError("Unknown command")


if __name__ == "__main__":
    main()
