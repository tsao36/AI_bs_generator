from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple


def _load_training_rows(input_dirs: List[str]) -> List[Dict[str, str]]:
    from train_issue_category_model import _load_training_rows as _loader

    return _loader(input_dirs)


def _load_ml_model(path: str) -> Any:
    import joblib  # type: ignore

    artifact = joblib.load(path)
    if isinstance(artifact, dict) and "pipeline" in artifact:
        return artifact["pipeline"]
    return artifact


def _normalize_tech(technology: str) -> str:
    t = (technology or "").strip().lower()
    aliases = {
        "wifi": "wifi",
        "wi-fi": "wifi",
        "wlan": "wifi",
        "bt": "bt",
        "bluetooth": "bt",
        "software": "software",
        "icps/killer": "software",
        "sw": "software",
        "tools": "tools",
        "wcs validation tool": "tools",
    }
    return aliases.get(t, t)


def _load_category_map(config_path: str) -> Tuple[Dict[str, List[str]], List[str]]:
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    default_categories = [str(x).strip() for x in cfg.get("default_categories", []) if str(x).strip()]

    by_tech: Dict[str, set] = defaultdict(set)
    for row in cfg.get("category_matrix", []):
        cat = str(row.get("issue_type", "")).strip()
        tech = _normalize_tech(str(row.get("technology", "")))
        if cat and tech:
            by_tech[tech].add(cat)

    mapped = {k: sorted(v) for k, v in by_tech.items()}
    return mapped, default_categories


def _choose_allowed_categories(technology: str, by_tech: Dict[str, List[str]], default_categories: List[str]) -> List[str]:
    tech = _normalize_tech(technology)
    cats = by_tech.get(tech)
    if cats:
        return cats
    return default_categories


def _safe_json_extract(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}

    try:
        return json.loads(raw)
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            return {}

    return {}


def _llm_predict_one(
    base_url: str,
    api_key: str,
    model: str,
    title: str,
    technology: str,
    allowed_categories: List[str],
    temperature: float,
) -> Tuple[str, float, str]:
    system_prompt = (
        "You are an IPS bug triage assistant. "
        "Return ONLY JSON: {\"category\": string, \"confidence\": number}."
    )
    user_prompt = (
        "Classify IPS issue title into one category from the allowed list.\n"
        f"Technology: {technology or 'unspecified'}\n"
        f"Title: {title}\n"
        f"Allowed categories: {', '.join(allowed_categories)}\n"
        "Rules: choose exactly one allowed category."
    )

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    content = ""
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        content = str(parsed["choices"][0]["message"].get("content") or "")
    except urllib.error.HTTPError as exc:
        content = f"HTTPError: {exc.code}"
    except Exception:
        content = ""

    payload = _safe_json_extract(content)
    category = str(payload.get("category", "")).strip()

    if category not in allowed_categories:
        lowered = {c.lower(): c for c in allowed_categories}
        category = lowered.get(category.lower(), "")

    if category not in allowed_categories:
        if "Need-Triage" in allowed_categories or "Needs-Triage" in allowed_categories:
            category = "Need-Triage"
        else:
            category = allowed_categories[0]

    conf = payload.get("confidence", 0.0)
    try:
        confidence = float(conf)
    except Exception:
        confidence = 0.0

    return category, max(0.0, min(1.0, confidence)), content


def _metrics(y_true: List[str], y_pred: List[str]) -> Dict[str, float]:
    from sklearn.metrics import accuracy_score, f1_score  # type: ignore

    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark local/external LLM vs current ML model on same holdout split.")
    p.add_argument("--input-dirs", default="CFE_reviewed_issue,CFE_input")
    p.add_argument("--model-path", default=os.path.join("models", "issue_category_model.joblib"))
    p.add_argument("--config", default="bug_category_config.json")
    p.add_argument("--test-size", type=float, default=0.2)
    p.add_argument("--random-state", type=int, default=42)
    p.add_argument("--min-class-support", type=int, default=5)
    p.add_argument("--max-test-rows", type=int, default=80)
    p.add_argument("--llm-base-url", default="http://127.0.0.1:11434/v1")
    p.add_argument("--llm-model", default="qwen2.5:14b")
    p.add_argument("--llm-api-key", default="ollama")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--restrict-to-training-labels", action="store_true", default=True)
    p.add_argument("--output", default=os.path.join("tuning_outputs", "llm_vs_ml_benchmark.json"))
    return p.parse_args()


def main() -> int:
    args = parse_args()

    from sklearn.model_selection import train_test_split  # type: ignore

    input_dirs = [x.strip() for x in str(args.input_dirs).split(",") if x.strip()]
    rows = _load_training_rows(input_dirs)

    label_counts = Counter(r["human_category"] for r in rows)
    filtered = [r for r in rows if label_counts.get(r["human_category"], 0) >= int(args.min_class_support)]

    x_all = [r["feature_text"] for r in filtered]
    y_all = [r["human_category"] for r in filtered]

    _, x_test, _, y_test, _, rows_test = train_test_split(
        x_all,
        y_all,
        filtered,
        test_size=float(args.test_size),
        random_state=int(args.random_state),
        stratify=y_all,
    )

    if args.max_test_rows and len(rows_test) > int(args.max_test_rows):
        try:
            _, _, _, _, _, rows_test = train_test_split(
                x_test,
                y_test,
                rows_test,
                test_size=int(args.max_test_rows),
                random_state=int(args.random_state),
                stratify=y_test,
            )
        except ValueError:
            _, _, _, _, _, rows_test = train_test_split(
                x_test,
                y_test,
                rows_test,
                test_size=int(args.max_test_rows),
                random_state=int(args.random_state),
                stratify=None,
            )

    y_true = [r["human_category"] for r in rows_test]
    x_eval = [r["feature_text"] for r in rows_test]
    training_labels = set(y_all)

    ml_pipeline = _load_ml_model(args.model_path)
    ml_pred = [str(x) for x in ml_pipeline.predict(x_eval)]
    ml_metrics = _metrics(y_true, ml_pred)

    by_tech, default_categories = _load_category_map(args.config)

    llm_pred: List[str] = []
    llm_conf: List[float] = []
    timings: List[float] = []

    total = len(rows_test)
    for i, row in enumerate(rows_test, start=1):
        allowed = _choose_allowed_categories(row.get("technology", ""), by_tech, default_categories)
        if args.restrict_to_training_labels:
            allowed = [c for c in allowed if c in training_labels]
            if not allowed:
                allowed = sorted(training_labels)
        t0 = time.perf_counter()
        pred, conf, _raw = _llm_predict_one(
            base_url=args.llm_base_url,
            api_key=args.llm_api_key,
            model=args.llm_model,
            title=row.get("ips_title", ""),
            technology=row.get("technology", ""),
            allowed_categories=allowed,
            temperature=float(args.temperature),
        )
        timings.append(time.perf_counter() - t0)
        llm_pred.append(pred)
        llm_conf.append(conf)
        print(f"[LLM] {i}/{total} -> {pred} ({conf:.3f})")

    llm_metrics = _metrics(y_true, llm_pred)

    result = {
        "rows_total": len(rows),
        "rows_used": len(filtered),
        "test_rows_evaluated": len(rows_test),
        "test_size": float(args.test_size),
        "random_state": int(args.random_state),
        "llm": {
            "base_url": args.llm_base_url,
            "model": args.llm_model,
            "temperature": float(args.temperature),
            "avg_latency_sec": (sum(timings) / len(timings)) if timings else 0.0,
        },
        "ml_metrics": ml_metrics,
        "llm_metrics": llm_metrics,
        "delta_llm_minus_ml": {
            "accuracy": llm_metrics["accuracy"] - ml_metrics["accuracy"],
            "macro_f1": llm_metrics["macro_f1"] - ml_metrics["macro_f1"],
            "weighted_f1": llm_metrics["weighted_f1"] - ml_metrics["weighted_f1"],
        },
    }

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)

    print("\n=== Benchmark complete ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"[OK] Wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
