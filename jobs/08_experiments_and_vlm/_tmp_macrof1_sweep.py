from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any, Dict, List

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

import train_issue_category_model as tm


def load_human_rows(input_dir: str) -> List[Dict[str, Any]]:
    return tm._load_training_rows(input_dir)


def load_process_gold_rows(path: str) -> List[Dict[str, Any]]:
    return tm._load_pseudo_rows(path, pseudo_weight=1.0)


def cap_pseudo_rows_by_k(pseudo_rows: List[Dict[str, Any]], human_counts: Dict[str, int], k: int) -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    bucket: Dict[str, List[Dict[str, Any]]] = {}
    for row in pseudo_rows:
        label = row["human_category"]
        bucket.setdefault(label, []).append(row)

    for label, rows in bucket.items():
        h = human_counts.get(label, 0)
        if h <= 0:
            continue
        cap = h * k
        # Stable deterministic order for reproducible sweep.
        rows_sorted = sorted(rows, key=lambda r: (str(r.get("ips_title", "")), str(r.get("source_file", ""))))
        kept.extend(rows_sorted[:cap])

    return kept


def main() -> int:
    input_dir = "CFE_input"
    pseudo_csv = "high_confidence_training_from_component_map.csv"
    out_json = Path("models") / "macro_f1_k_param_sweep.json"

    data = load_human_rows(input_dir)
    pseudo_all = load_process_gold_rows(pseudo_csv)

    min_class_support = 5
    label_counts: Dict[str, int] = {}
    for row in data:
        label = row["human_category"]
        label_counts[label] = label_counts.get(label, 0) + 1

    filtered = [row for row in data if label_counts.get(row["human_category"], 0) >= min_class_support]

    X = [row["feature_text"] for row in filtered]
    y = [row["human_category"] for row in filtered]

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y,
    )

    train_label_counts: Dict[str, int] = {}
    for lbl in y_train:
        train_label_counts[lbl] = train_label_counts.get(lbl, 0) + 1

    test_feature_set = set(X_test)

    results: List[Dict[str, Any]] = []
    for k in [1, 2, 3]:
        pseudo_kept = cap_pseudo_rows_by_k(pseudo_all, train_label_counts, k)
        pseudo_kept = [r for r in pseudo_kept if r["feature_text"] not in test_feature_set]

        X_train_aug = list(X_train)
        y_train_aug = list(y_train)
        w_train_aug = [1.0] * len(X_train)

        for row in pseudo_kept:
            X_train_aug.append(row["feature_text"])
            y_train_aug.append(row["human_category"])
            w_train_aug.append(1.0)

        for ngram_max in [1, 2]:
            for min_df in [1, 2]:
                for c in [0.5, 1.0, 2.0, 4.0]:
                    pipeline = Pipeline(
                        steps=[
                            (
                                "tfidf",
                                TfidfVectorizer(
                                    ngram_range=(1, ngram_max),
                                    min_df=min_df,
                                    max_df=0.98,
                                    sublinear_tf=True,
                                    lowercase=True,
                                ),
                            ),
                            (
                                "clf",
                                LogisticRegression(
                                    max_iter=2000,
                                    class_weight="balanced",
                                    C=c,
                                ),
                            ),
                        ]
                    )

                    pipeline.fit(X_train_aug, y_train_aug, clf__sample_weight=w_train_aug)
                    y_pred = pipeline.predict(X_test)

                    report = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
                    results.append(
                        {
                            "k": k,
                            "ngram_max": ngram_max,
                            "min_df": min_df,
                            "lr_c": c,
                            "train_rows_human": len(X_train),
                            "train_rows_pseudo": len(pseudo_kept),
                            "train_rows_total": len(X_train_aug),
                            "test_rows_human": len(X_test),
                            "accuracy": float(accuracy_score(y_test, y_pred)),
                            "macro_f1": float(report["macro avg"]["f1-score"]),
                            "weighted_f1": float(report["weighted avg"]["f1-score"]),
                        }
                    )

    results_sorted = sorted(results, key=lambda r: (r["macro_f1"], r["accuracy"]), reverse=True)
    summary = {
        "total_runs": len(results),
        "best": results_sorted[0],
        "top10": results_sorted[:10],
        "all": results_sorted,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"total_runs={summary['total_runs']}")
    print("best=" + json.dumps(summary["best"], ensure_ascii=False))
    print(f"out_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
