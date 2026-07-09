from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.request
import urllib.error
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

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


def _read_rows(csv_path: str) -> List[Dict[str, str]]:
    encodings = ["utf-8-sig", "cp1252", "latin-1"]
    data_rows: List[Dict[str, Any]] = []
    last_err: Exception | None = None
    for enc in encodings:
        try:
            with open(csv_path, "r", encoding=enc, newline="") as handle:
                data_rows = list(csv.DictReader(handle))
            break
        except UnicodeDecodeError as exc:
            last_err = exc
            continue

    if not data_rows and last_err is not None:
        raise last_err

    rows: List[Dict[str, str]] = []
    for row in data_rows:
        title = _clean_text(row.get("ips_title"))
        if not title:
            continue
        human = _normalise_label(_clean_text(row.get("human_category")))
        if not human:
            continue

        rows.append(
            {
                "ips_title": title,
                "ips_description": _clean_text(row.get("ips_description") or row.get("description") or row.get("jira_description")),
                "predicted_category": _clean_text(row.get("predicted_category")),
                "technology": _clean_text(row.get("technology")),
                "human_category": human,
            }
        )

    if not rows:
        raise RuntimeError("No usable rows found. Required columns: ips_title, human_category")
    return rows


def _safe_parse_json(payload: str) -> Dict[str, Any]:
    text = str(payload or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                return {}
    return {}


def _llm_predict(
    *,
    base_url: str,
    api_key: str,
    model: str,
    title: str,
    description: str,
    technology: str,
    predicted_category_hint: str,
    allowed_categories: List[str],
) -> Tuple[str, float, str]:
    system_prompt = (
        "You are a strict issue-category classifier. "
        "Choose exactly one category from the allowed list. "
        "Return only JSON: {\"category\": <string>, \"confidence\": <0-1>, \"reasoning\": <string>}."
    )

    user_prompt = (
        "Classify this historical IPS issue record.\n"
        f"Title: {title or '(empty)'}\n"
        f"Description: {description or '(empty)'}\n"
        f"Technology: {technology or '(empty)'}\n"
        f"Historical hint category: {predicted_category_hint or '(empty)'}\n\n"
        "Allowed categories:\n"
        + "\n".join(f"- {cat}" for cat in allowed_categories)
    )

    payload = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    url = base_url.rstrip("/") + "/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp_payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"LLM HTTP error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM URL error: {exc}") from exc

    choices = resp_payload.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned empty choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = str((message or {}).get("content") or "")
    parsed = _safe_parse_json(content)
    category = _clean_text(parsed.get("category"))
    if category not in allowed_categories:
        category = ""
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return category, confidence, content


def run_compare(
    *,
    golden_csv: str,
    out_json: str,
    test_size: float,
    random_state: int,
    min_class_support: int,
    llm_model: str,
    llm_base_url: str,
    llm_api_key: str,
    max_llm_samples: int,
) -> Dict[str, Any]:
    ml = _try_import_ml()
    train_test_split = ml["train_test_split"]
    Pipeline = ml["Pipeline"]
    TfidfVectorizer = ml["TfidfVectorizer"]
    LogisticRegression = ml["LogisticRegression"]
    accuracy_score = ml["accuracy_score"]
    classification_report = ml["classification_report"]
    f1_score = ml["f1_score"]

    rows = _read_rows(golden_csv)

    label_counts: Dict[str, int] = Counter(r["human_category"] for r in rows)
    dropped_rare = sorted([k for k, v in label_counts.items() if v < min_class_support])
    filtered = [r for r in rows if label_counts.get(r["human_category"], 0) >= min_class_support]
    if len(filtered) < 10:
        raise RuntimeError("Too few rows after filtering; lower --min-class-support.")

    x_all = [
        _compose_feature_text(
            r["ips_title"],
            predicted_category=r.get("predicted_category", ""),
            technology=r.get("technology", ""),
            description=r.get("ips_description", ""),
        )
        for r in filtered
    ]
    y_all = [r["human_category"] for r in filtered]

    idx = list(range(len(filtered)))
    idx_train, idx_test, y_train, y_test = train_test_split(
        idx,
        y_all,
        test_size=test_size,
        random_state=random_state,
        stratify=y_all,
    )

    x_train = [x_all[i] for i in idx_train]
    x_test = [x_all[i] for i in idx_test]

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

    pipeline.fit(x_train, y_train)
    ml_pred = list(pipeline.predict(x_test))

    ml_metrics = {
        "accuracy": float(accuracy_score(y_test, ml_pred)),
        "macro_f1": float(f1_score(y_test, ml_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_test, ml_pred, average="weighted", zero_division=0)),
        "classification_report": classification_report(y_test, ml_pred, output_dict=True, zero_division=0),
    }

    if not llm_model:
        raise RuntimeError("LLM model is empty. Set EXPERTGPT_MODEL/MODEL or pass --llm-model.")
    if not llm_api_key:
        raise RuntimeError("LLM API key is empty. Set EXPERTGPT_TOKEN or pass --llm-api-key.")

    allowed_categories = sorted(set(y_train))

    llm_rows = idx_test
    if max_llm_samples > 0:
        llm_rows = idx_test[:max_llm_samples]

    llm_truth: List[str] = []
    llm_pred: List[str] = []
    llm_conf: List[float] = []
    invalid_response = 0
    cache: Dict[Tuple[str, str, str, str], Tuple[str, float]] = {}

    for row_idx in llm_rows:
        row = filtered[row_idx]
        key = (
            row.get("ips_title", ""),
            row.get("ips_description", ""),
            row.get("technology", ""),
            row.get("predicted_category", ""),
        )
        if key in cache:
            pred_cat, conf = cache[key]
        else:
            pred_cat, conf, _raw = _llm_predict(
                base_url=llm_base_url,
                api_key=llm_api_key,
                model=llm_model,
                title=row.get("ips_title", ""),
                description=row.get("ips_description", ""),
                technology=row.get("technology", ""),
                predicted_category_hint=row.get("predicted_category", ""),
                allowed_categories=allowed_categories,
            )
            cache[key] = (pred_cat, conf)

        truth = row["human_category"]
        if not pred_cat:
            invalid_response += 1
            pred_cat = allowed_categories[0]
            conf = 0.0

        llm_truth.append(truth)
        llm_pred.append(pred_cat)
        llm_conf.append(conf)

    llm_metrics = {
        "accuracy": float(accuracy_score(llm_truth, llm_pred)),
        "macro_f1": float(f1_score(llm_truth, llm_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(llm_truth, llm_pred, average="weighted", zero_division=0)),
        "classification_report": classification_report(llm_truth, llm_pred, output_dict=True, zero_division=0),
        "avg_confidence": float(sum(llm_conf) / len(llm_conf)) if llm_conf else 0.0,
        "invalid_response_count": int(invalid_response),
    }

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "golden_csv": os.path.abspath(golden_csv),
        "rows_total": len(rows),
        "rows_used": len(filtered),
        "dropped_rare_labels": dropped_rare,
        "label_counts": dict(sorted(Counter(r["human_category"] for r in filtered).items())),
        "split": {
            "test_size": test_size,
            "random_state": random_state,
            "train_rows": len(idx_train),
            "test_rows": len(idx_test),
            "llm_eval_rows": len(llm_rows),
        },
        "ml": ml_metrics,
        "llm": llm_metrics,
        "delta_llm_minus_ml": {
            "accuracy": llm_metrics["accuracy"] - ml_metrics["accuracy"],
            "macro_f1": llm_metrics["macro_f1"] - ml_metrics["macro_f1"],
            "weighted_f1": llm_metrics["weighted_f1"] - ml_metrics["weighted_f1"],
        },
        "winner_by_macro_f1": "llm" if llm_metrics["macro_f1"] > ml_metrics["macro_f1"] else "ml",
        "llm_model": llm_model,
        "llm_base_url": llm_base_url,
    }

    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    return result


def parse_args() -> argparse.Namespace:
    load_dotenv(override=False)
    parser = argparse.ArgumentParser(description="Compare ML vs LLM issue-category prediction on golden dataset")
    parser.add_argument("--golden-csv", default="golden_training_set_20260603.csv")
    parser.add_argument("--out-json", default=os.path.join("models", "ab_ml_vs_llm_golden_metrics.json"))
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--min-class-support", type=int, default=5)
    parser.add_argument("--llm-model", default=(os.getenv("EXPERTGPT_MODEL") or os.getenv("MODEL") or "").strip())
    parser.add_argument("--llm-base-url", default=(os.getenv("EXPERTGPT_URL") or "https://expertgpt.intel.com").strip())
    parser.add_argument("--llm-api-key", default=(os.getenv("EXPERTGPT_TOKEN") or "").strip())
    parser.add_argument("--max-llm-samples", type=int, default=0, help="0 means evaluate all test rows")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_compare(
        golden_csv=args.golden_csv,
        out_json=args.out_json,
        test_size=args.test_size,
        random_state=args.random_state,
        min_class_support=args.min_class_support,
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
        llm_api_key=args.llm_api_key,
        max_llm_samples=args.max_llm_samples,
    )

    print("[OK] ML vs LLM comparison complete")
    print(f"[OK] Output: {args.out_json}")
    print(
        "[ML] "
        f"acc={result['ml']['accuracy']:.4f} "
        f"macro_f1={result['ml']['macro_f1']:.4f} "
        f"weighted_f1={result['ml']['weighted_f1']:.4f}"
    )
    print(
        "[LLM] "
        f"acc={result['llm']['accuracy']:.4f} "
        f"macro_f1={result['llm']['macro_f1']:.4f} "
        f"weighted_f1={result['llm']['weighted_f1']:.4f} "
        f"invalid={result['llm'].get('invalid_response_count', 0)}"
    )
    print(
        "[DELTA llm-ml] "
        f"acc={result['delta_llm_minus_ml']['accuracy']:+.4f} "
        f"macro_f1={result['delta_llm_minus_ml']['macro_f1']:+.4f} "
        f"weighted_f1={result['delta_llm_minus_ml']['weighted_f1']:+.4f}"
    )
    print(f"[WINNER macro_f1] {result['winner_by_macro_f1']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
