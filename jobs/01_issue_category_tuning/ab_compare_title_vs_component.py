from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import psycopg2

from APIs import Sherlock
from issue_category_model import _compose_feature_text
from train_issue_category_model import _normalise_label


def _try_import_ml() -> Dict[str, Any]:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
        from sklearn.linear_model import LogisticRegression  # type: ignore
        from sklearn.metrics import accuracy_score, classification_report, f1_score  # type: ignore
        from sklearn.model_selection import train_test_split  # type: ignore
        from sklearn.pipeline import Pipeline  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Missing dependencies. Install with: pip install scikit-learn") from exc

    return {
        "TfidfVectorizer": TfidfVectorizer,
        "LogisticRegression": LogisticRegression,
        "accuracy_score": accuracy_score,
        "classification_report": classification_report,
        "f1_score": f1_score,
        "train_test_split": train_test_split,
        "Pipeline": Pipeline,
    }


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.upper() == "NA":
        return ""
    return text


def _read_csv_dict_rows(path: str) -> List[Dict[str, Any]]:
    encodings = ["utf-8-sig", "cp1252", "latin-1"]
    last_error: Exception | None = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return []


def _load_labeled_rows(input_dir: str) -> List[Dict[str, str]]:
    pattern = os.path.join(input_dir, "*.csv")
    csv_files = [p for p in glob.glob(pattern) if not os.path.basename(p).startswith("~$")]
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found under: {input_dir}")

    rows: List[Dict[str, str]] = []
    for path in sorted(csv_files):
        data_rows = _read_csv_dict_rows(path)
        if not data_rows:
            continue
        fieldnames = set(data_rows[0].keys())
        if "ips_title" not in fieldnames or "human_category" not in fieldnames:
            continue

        for row in data_rows:
            title = _clean_text(row.get("ips_title"))
            human_category = _normalise_label(_clean_text(row.get("human_category")))
            if not title or not human_category:
                continue
            rows.append(
                {
                    "ips_title": title,
                    "predicted_category": _clean_text(row.get("predicted_category")),
                    "technology": _clean_text(row.get("technology")),
                    "human_category": human_category,
                }
            )

    if not rows:
        raise RuntimeError("No usable rows found. Required columns: ips_title, human_category")
    return rows


def _connect_db():
    return psycopg2.connect(
        database=Sherlock.PostgresCustomerEngineeringDb.database,
        user=Sherlock.PostgresCustomerEngineeringDb.user,
        password=Sherlock.PostgresCustomerEngineeringDb.password,
        host=Sherlock.PostgresCustomerEngineeringDb.host,
        port=Sherlock.PostgresCustomerEngineeringDb.port,
    )


def _fetch_title_component_map(titles: List[str]) -> Dict[str, str]:
    if not titles:
        return {}

    conn = _connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TEMP TABLE tmp_titles (
                    ips_title TEXT PRIMARY KEY
                ) ON COMMIT DROP
                """
            )
            args = [(t,) for t in set(titles)]
            values_sql = b",".join(cur.mogrify("(%s)", x) for x in args).decode("utf-8")
            cur.execute(f"INSERT INTO tmp_titles (ips_title) VALUES {values_sql} ON CONFLICT DO NOTHING")

            cur.execute(
                """
                SELECT
                    TRIM(t.ips_title) AS ips_title,
                    TRIM(b.jira_final_component) AS jira_component,
                    COUNT(*) AS n
                FROM tmp_titles t
                JOIN ips_jira_bugs b
                  ON TRIM(b.ips_title) = TRIM(t.ips_title)
                WHERE NULLIF(TRIM(COALESCE(b.jira_final_component, '')), '') IS NOT NULL
                  AND UPPER(TRIM(COALESCE(b.jira_final_component, ''))) <> 'NA'
                GROUP BY TRIM(t.ips_title), TRIM(b.jira_final_component)
                ORDER BY TRIM(t.ips_title), n DESC, TRIM(b.jira_final_component)
                """
            )
            rows = cur.fetchall()

    finally:
        conn.close()

    by_title: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for title, component, n in rows:
        by_title[title].append((component, int(n)))

    best_map: Dict[str, str] = {}
    for title, comp_rows in by_title.items():
        comp_rows.sort(key=lambda x: (-x[1], x[0]))
        best_map[title] = comp_rows[0][0]
    return best_map


def _build_features(rows: List[Dict[str, str]], title_component_map: Dict[str, str]) -> Tuple[List[str], List[str], List[str], Dict[str, Any]]:
    X_title_only: List[str] = []
    X_with_component: List[str] = []
    y: List[str] = []

    with_component = 0
    component_counter: Counter[str] = Counter()

    for row in rows:
        title = row["ips_title"]
        pred = row.get("predicted_category", "")
        tech = row.get("technology", "")
        label = row["human_category"]

        base_text = _compose_feature_text(title, predicted_category=pred, technology=tech)
        component = title_component_map.get(title, "COMPONENT_UNKNOWN")
        feat_with_comp = f"[COMPONENT={component}] {base_text}".strip()

        X_title_only.append(base_text)
        X_with_component.append(feat_with_comp)
        y.append(label)

        if component != "COMPONENT_UNKNOWN":
            with_component += 1
            component_counter[component] += 1

    stats = {
        "rows_total": len(rows),
        "rows_with_component": with_component,
        "rows_without_component": len(rows) - with_component,
        "distinct_components_used": len(component_counter),
        "top_components": component_counter.most_common(10),
    }
    return X_title_only, X_with_component, y, stats


def _train_eval(ml: Dict[str, Any], X_train: List[str], X_test: List[str], y_train: List[str], y_test: List[str]) -> Dict[str, Any]:
    Pipeline = ml["Pipeline"]
    TfidfVectorizer = ml["TfidfVectorizer"]
    LogisticRegression = ml["LogisticRegression"]
    accuracy_score = ml["accuracy_score"]
    classification_report = ml["classification_report"]
    f1_score = ml["f1_score"]

    pipeline = Pipeline(
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

    pipeline.fit(X_train, y_train)
    y_pred = pipeline.predict(X_test)

    return {
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "macro_f1": float(f1_score(y_test, y_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_test, y_pred, average="weighted", zero_division=0)),
        "classification_report": classification_report(y_test, y_pred, output_dict=True, zero_division=0),
    }


def run_ab_compare(input_dir: str, out_json: str, test_size: float, random_state: int) -> Dict[str, Any]:
    ml = _try_import_ml()
    train_test_split = ml["train_test_split"]

    rows = _load_labeled_rows(input_dir)

    # Keep labels with enough support.
    min_class_support = 5
    label_counts: Dict[str, int] = Counter(row["human_category"] for row in rows)
    dropped_rare = sorted([k for k, v in label_counts.items() if v < min_class_support])
    filtered_rows = [row for row in rows if label_counts.get(row["human_category"], 0) >= min_class_support]

    titles = [r["ips_title"] for r in filtered_rows]
    title_component_map = _fetch_title_component_map(titles)
    X_title_only, X_with_component, y, feature_stats = _build_features(filtered_rows, title_component_map)

    idx = list(range(len(y)))
    idx_train, idx_test, y_train, y_test = train_test_split(
        idx,
        y,
        test_size=test_size,
        random_state=random_state,
        stratify=y,
    )

    X_train_title = [X_title_only[i] for i in idx_train]
    X_test_title = [X_title_only[i] for i in idx_test]
    X_train_comp = [X_with_component[i] for i in idx_train]
    X_test_comp = [X_with_component[i] for i in idx_test]

    title_only_metrics = _train_eval(ml, X_train_title, X_test_title, y_train, y_test)
    with_component_metrics = _train_eval(ml, X_train_comp, X_test_comp, y_train, y_test)

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_dir": os.path.abspath(input_dir),
        "rows_total": len(rows),
        "rows_used": len(filtered_rows),
        "dropped_rare_labels": dropped_rare,
        "label_counts": dict(sorted(Counter(r["human_category"] for r in filtered_rows).items())),
        "feature_stats": feature_stats,
        "split": {
            "test_size": test_size,
            "random_state": random_state,
            "train_rows": len(idx_train),
            "test_rows": len(idx_test),
        },
        "title_only": title_only_metrics,
        "title_plus_component": with_component_metrics,
        "delta": {
            "accuracy": with_component_metrics["accuracy"] - title_only_metrics["accuracy"],
            "macro_f1": with_component_metrics["macro_f1"] - title_only_metrics["macro_f1"],
            "weighted_f1": with_component_metrics["weighted_f1"] - title_only_metrics["weighted_f1"],
        },
    }

    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B compare title-only vs title+component for issue category model.")
    parser.add_argument("--input-dir", default="CFE_input")
    parser.add_argument("--out-json", default=os.path.join("models", "ab_title_vs_component_metrics.json"))
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    result = run_ab_compare(args.input_dir, args.out_json, args.test_size, args.random_state)
    print("[OK] A/B compare completed")
    print(f"[OK] Output: {args.out_json}")
    print(f"[INFO] rows_used={result['rows_used']} rows_with_component={result['feature_stats']['rows_with_component']}")
    print(
        "[TITLE_ONLY] "
        f"acc={result['title_only']['accuracy']:.4f} "
        f"macro_f1={result['title_only']['macro_f1']:.4f} "
        f"weighted_f1={result['title_only']['weighted_f1']:.4f}"
    )
    print(
        "[TITLE+COMP] "
        f"acc={result['title_plus_component']['accuracy']:.4f} "
        f"macro_f1={result['title_plus_component']['macro_f1']:.4f} "
        f"weighted_f1={result['title_plus_component']['weighted_f1']:.4f}"
    )
    print(
        "[DELTA] "
        f"acc={result['delta']['accuracy']:+.4f} "
        f"macro_f1={result['delta']['macro_f1']:+.4f} "
        f"weighted_f1={result['delta']['weighted_f1']:+.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
