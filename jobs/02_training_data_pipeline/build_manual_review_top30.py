from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from typing import Dict, List, Tuple

import joblib

from issue_category_model import _compose_feature_text
from train_issue_category_model import _normalise_label


def _clean(value: object) -> str:
    text = str(value or "").strip()
    if text.upper() == "NA":
        return ""
    return text


def _load_rows(path: str) -> List[Dict[str, str]]:
    encodings = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except Exception:
            continue
    raise RuntimeError(f"Unable to read CSV: {path}")


def _predict(bundle: Dict, title: str, technology: str, predicted_hint: str) -> Tuple[str, float]:
    pipeline = bundle["pipeline"]
    feature_text = _compose_feature_text(
        title,
        predicted_category=predicted_hint,
        technology=technology,
        description="",
    )
    predicted = str(pipeline.predict([feature_text])[0])
    confidence = 0.0
    try:
        if hasattr(pipeline, "predict_proba"):
            probs = pipeline.predict_proba([feature_text])[0]
            confidence = float(max(probs))
    except Exception:
        confidence = 0.0
    return predicted, confidence


def _build_candidates(rows: List[Dict[str, str]], bundle: Dict, top_n: int) -> List[Dict[str, str]]:
    labeled_rows: List[Dict[str, str]] = []
    true_support: Counter[str] = Counter()
    wrong_pair_counts: Counter[Tuple[str, str]] = Counter()

    for row in rows:
        human = _normalise_label(_clean(row.get("human_category")))
        title = _clean(row.get("ips_title"))
        if not human or not title:
            continue
        tech = _clean(row.get("technology"))
        predicted_hint = _clean(row.get("predicted_category"))
        model_pred, model_conf = _predict(bundle, title, tech, predicted_hint)
        true_support[human] += 1
        rec = {
            "human": human,
            "title": title,
            "tech": tech,
            "predicted_hint": predicted_hint,
            "model_pred": model_pred,
            "model_conf": model_conf,
            "source_folder": _clean(row.get("source_dir") or row.get("source_folder")),
            "source_file": _clean(row.get("source_file")),
        }
        labeled_rows.append(rec)
        if model_pred != human:
            wrong_pair_counts[(human, model_pred)] += 1

    ranked: List[Dict[str, str]] = []
    for rec in labeled_rows:
        human = rec["human"]
        pred = rec["model_pred"]
        if pred == human:
            continue
        pair_count = int(wrong_pair_counts[(human, pred)])
        support = int(true_support[human])
        pair_err_rate = (pair_count / support) if support else 0.0
        conf = float(rec["model_conf"])
        loss_score = conf * (1.0 + pair_err_rate)
        review_priority = int(round(loss_score * 10000))
        ranked.append(
            {
                "review_priority": str(review_priority),
                "pair_count": str(pair_count),
                "pair_error_rate_within_true": f"{pair_err_rate:.4f}",
                "human_category_current": human,
                "model_predicted": pred,
                "ips_title": rec["title"],
                "technology": rec["tech"],
                "predicted_category_hint": rec["predicted_hint"],
                "source_folder": rec["source_folder"],
                "source_file": rec["source_file"],
                "model_confidence": f"{conf:.4f}",
                "loss_score": f"{loss_score:.6f}",
                "action": "review_human_category",
                "review_notes": "",
            }
        )

    ranked.sort(
        key=lambda x: (
            float(x["loss_score"]),
            float(x["model_confidence"]),
            int(x["pair_count"]),
        ),
        reverse=True,
    )
    return ranked[:top_n]


def _write_csv(path: str, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build top-N manual review candidates ranked by confidence-weighted error loss.")
    parser.add_argument("--golden-csv", required=True, help="Input golden CSV with human_category/title fields")
    parser.add_argument("--model-path", required=True, help="Model bundle joblib path")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--out-csv", required=True, help="Output ranked candidate CSV")
    parser.add_argument("--out-template-csv", required=True, help="Output editable template CSV")
    args = parser.parse_args()

    rows = _load_rows(args.golden_csv)
    bundle = joblib.load(args.model_path)
    top_rows = _build_candidates(rows, bundle, int(args.top_n))

    base_fields = [
        "review_priority",
        "pair_count",
        "pair_error_rate_within_true",
        "human_category_current",
        "model_predicted",
        "ips_title",
        "technology",
        "predicted_category_hint",
        "source_folder",
        "source_file",
        "model_confidence",
        "loss_score",
        "action",
        "review_notes",
    ]
    _write_csv(args.out_csv, top_rows, base_fields)

    template_rows: List[Dict[str, str]] = []
    for row in top_rows:
        ext = dict(row)
        ext.update(
            {
                "new_human_category": "",
                "secondary_category": "",
                "is_dual_valid": "",
                "keep_current_label": "",
                "reviewer": "",
                "decision_notes": "",
            }
        )
        template_rows.append(ext)

    template_fields = base_fields + [
        "new_human_category",
        "secondary_category",
        "is_dual_valid",
        "keep_current_label",
        "reviewer",
        "decision_notes",
    ]
    _write_csv(args.out_template_csv, template_rows, template_fields)

    print(f"Loaded rows: {len(rows)}")
    print(f"Top candidates: {len(top_rows)}")
    print(f"Candidate CSV: {args.out_csv}")
    print(f"Template CSV: {args.out_template_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
