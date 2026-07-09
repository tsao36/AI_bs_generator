from __future__ import annotations

import argparse
import csv
import json
import os
from typing import Any, Dict, List, Tuple

from train_issue_category_model import _load_training_rows


def _ensure_ml() -> Dict[str, Any]:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
        from sklearn.linear_model import LogisticRegression  # type: ignore
        from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score  # type: ignore
        from sklearn.model_selection import train_test_split  # type: ignore
        from sklearn.pipeline import Pipeline  # type: ignore
        from sklearn.svm import LinearSVC  # type: ignore
        import joblib  # type: ignore
    except Exception as exc:
        raise RuntimeError("Missing dependencies. Install with: pip install scikit-learn joblib") from exc

    return {
        "TfidfVectorizer": TfidfVectorizer,
        "LogisticRegression": LogisticRegression,
        "LinearSVC": LinearSVC,
        "accuracy_score": accuracy_score,
        "classification_report": classification_report,
        "confusion_matrix": confusion_matrix,
        "f1_score": f1_score,
        "train_test_split": train_test_split,
        "Pipeline": Pipeline,
        "joblib": joblib,
    }


def _build_lr_pipeline(ml: Dict[str, Any]) -> Any:
    Pipeline = ml["Pipeline"]
    TfidfVectorizer = ml["TfidfVectorizer"]
    LogisticRegression = ml["LogisticRegression"]
    return Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=2,
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
                ),
            ),
        ]
    )


def _build_svc_pipeline(ml: Dict[str, Any]) -> Any:
    Pipeline = ml["Pipeline"]
    TfidfVectorizer = ml["TfidfVectorizer"]
    LinearSVC = ml["LinearSVC"]
    return Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=2,
                    max_df=0.98,
                    sublinear_tf=True,
                    lowercase=True,
                ),
            ),
            (
                "clf",
                LinearSVC(class_weight="balanced"),
            ),
        ]
    )


def _score_model(name: str, model: Any, x_train: List[str], x_test: List[str], y_train: List[str], y_test: List[str], ml: Dict[str, Any]) -> Dict[str, Any]:
    accuracy_score = ml["accuracy_score"]
    f1_score = ml["f1_score"]
    classification_report = ml["classification_report"]

    model.fit(x_train, y_train)
    y_pred = model.predict(x_test)

    return {
        "model": name,
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "macro_f1": float(f1_score(y_test, y_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_test, y_pred, average="weighted", zero_division=0)),
        "classification_report": classification_report(y_test, y_pred, output_dict=True, zero_division=0),
        "y_pred": list(y_pred),
    }


def _write_confusion_csv(y_true: List[str], y_pred: List[str], out_csv: str, ml: Dict[str, Any]) -> None:
    confusion_matrix = ml["confusion_matrix"]
    labels = sorted(set(y_true) | set(y_pred))
    matrix = confusion_matrix(y_true, y_pred, labels=labels)

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["true\\pred"] + labels)
        for i, label in enumerate(labels):
            writer.writerow([label] + list(matrix[i]))


def _extract_confidence(pipeline: Any, x_text: List[str]) -> List[float]:
    if hasattr(pipeline, "predict_proba"):
        probs = pipeline.predict_proba(x_text)
        return [float(max(row)) for row in probs]

    if hasattr(pipeline, "decision_function"):
        scores = pipeline.decision_function(x_text)
        confidences: List[float] = []
        for row in scores:
            if hasattr(row, "__len__"):
                top = float(max(row))
            else:
                top = float(row)
            confidences.append(1.0 / (1.0 + pow(2.71828, -top)))
        return confidences

    return [0.0 for _ in x_text]


def _export_low_confidence_rows(current_model_path: str, rows: List[Dict[str, str]], threshold: float, out_csv: str, ml: Dict[str, Any]) -> int:
    joblib = ml["joblib"]
    artifact = joblib.load(current_model_path)
    pipeline = artifact["pipeline"] if isinstance(artifact, dict) and "pipeline" in artifact else artifact

    x_text = [r["feature_text"] for r in rows]
    y_pred = [str(v) for v in pipeline.predict(x_text)]
    y_conf = _extract_confidence(pipeline, x_text)

    selected = []
    for row, pred, conf in zip(rows, y_pred, y_conf):
        if conf <= threshold:
            selected.append(
                {
                    "source_file": row.get("source_file", ""),
                    "technology": row.get("technology", ""),
                    "ips_title": row.get("ips_title", ""),
                    "predicted_category_model": pred,
                    "predicted_category_existing": row.get("predicted_category", ""),
                    "human_category": row.get("human_category", ""),
                    "confidence": f"{conf:.4f}",
                }
            )

    selected.sort(key=lambda r: float(r["confidence"]))

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source_file",
                "technology",
                "ips_title",
                "predicted_category_model",
                "predicted_category_existing",
                "human_category",
                "confidence",
            ],
        )
        writer.writeheader()
        writer.writerows(selected)

    return len(selected)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple and executable category tuning cycle.")
    parser.add_argument("--input-dir", default="", help="Deprecated single input dir; use --input-dirs")
    parser.add_argument("--input-dirs", default="CFE_reviewed_issue,CFE_input")
    parser.add_argument("--current-model", default=os.path.join("models", "issue_category_model.joblib"))
    parser.add_argument("--output-dir", default="tuning_outputs")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--low-confidence-threshold", type=float, default=0.45)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ml = _ensure_ml()

    input_dirs = [p.strip() for p in str(args.input_dirs).split(",") if p.strip()]
    if args.input_dir:
        input_dirs = [args.input_dir]

    rows = _load_training_rows(input_dirs)
    label_counts: Dict[str, int] = {}
    for r in rows:
        label = r["human_category"]
        label_counts[label] = label_counts.get(label, 0) + 1

    rows_filtered = [r for r in rows if label_counts.get(r["human_category"], 0) >= 5]
    dropped_labels = sorted([k for k, v in label_counts.items() if v < 5])

    if len({r["human_category"] for r in rows_filtered}) < 2:
        raise RuntimeError("Not enough class support after filtering rare labels (need at least 2 labels with >=2 samples).")

    x_all = [r["feature_text"] for r in rows_filtered]
    y_all = [r["human_category"] for r in rows_filtered]

    train_test_split = ml["train_test_split"]
    x_train, x_test, y_train, y_test = train_test_split(
        x_all,
        y_all,
        test_size=float(args.test_size),
        random_state=int(args.random_state),
        stratify=y_all,
    )

    lr = _build_lr_pipeline(ml)
    svc = _build_svc_pipeline(ml)

    lr_scores = _score_model("LogisticRegression", lr, x_train, x_test, y_train, y_test, ml)
    svc_scores = _score_model("LinearSVC", svc, x_train, x_test, y_train, y_test, ml)

    compare = [
        {
            "model": lr_scores["model"],
            "accuracy": lr_scores["accuracy"],
            "macro_f1": lr_scores["macro_f1"],
            "weighted_f1": lr_scores["weighted_f1"],
        },
        {
            "model": svc_scores["model"],
            "accuracy": svc_scores["accuracy"],
            "macro_f1": svc_scores["macro_f1"],
            "weighted_f1": svc_scores["weighted_f1"],
        },
    ]

    best = lr_scores if lr_scores["macro_f1"] >= svc_scores["macro_f1"] else svc_scores

    os.makedirs(args.output_dir, exist_ok=True)

    compare_path = os.path.join(args.output_dir, "model_compare_metrics.json")
    with open(compare_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "input_dirs": [os.path.abspath(p) for p in input_dirs],
                "rows_total": len(rows),
                "rows_used": len(rows_filtered),
                "dropped_rare_labels": dropped_labels,
                "test_size": float(args.test_size),
                "random_state": int(args.random_state),
                "models": compare,
                "best_model": best["model"],
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )

    confusion_path = os.path.join(args.output_dir, "best_model_confusion_matrix.csv")
    _write_confusion_csv(y_test, best["y_pred"], confusion_path, ml)

    low_conf_path = os.path.join(args.output_dir, "low_confidence_candidates.csv")
    low_conf_count = _export_low_confidence_rows(
        args.current_model,
        rows_filtered,
        float(args.low_confidence_threshold),
        low_conf_path,
        ml,
    )

    print("Tuning cycle complete")
    print(f"Rows total: {len(rows)}")
    print(f"Rows used: {len(rows_filtered)}")
    if dropped_labels:
        print("Dropped rare labels (<5 rows): " + ", ".join(dropped_labels))
    print(f"Best model by macro_f1: {best['model']} ({best['macro_f1']:.4f})")
    print(f"Model compare: {compare_path}")
    print(f"Confusion matrix: {confusion_path}")
    print(f"Low-confidence candidates: {low_conf_path} ({low_conf_count} rows, threshold={args.low_confidence_threshold})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
