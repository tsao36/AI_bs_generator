from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import shutil
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Tuple

import pandas as pd  # type: ignore

import joblib  # type: ignore

from train_issue_category_model import _load_training_rows, train_model, _normalise_label


def _find_latest_weekly_dir(tuning_root: str) -> str:
    from datetime import date, timedelta
    import re

    candidates = [p for p in glob.glob(os.path.join(tuning_root, "weekly_*")) if os.path.isdir(p)]
    if not candidates:
        raise FileNotFoundError(f"No weekly output folder found under: {tuning_root}")

    today = date.today()
    # Folders are created every Monday; this script runs every Friday (~4 days later).
    # Look for the most recent folder whose name-date falls in the [today-7, today-3] window.
    dated: List[Tuple[date, str]] = []
    for p in candidates:
        m = re.search(r"weekly_(\d{8})$", p)
        if m:
            try:
                folder_date = datetime.strptime(m.group(1), "%Y%m%d").date()
                dated.append((folder_date, p))
            except ValueError:
                pass

    if dated:
        in_window = [(d, p) for d, p in dated if timedelta(days=3) <= today - d <= timedelta(days=7)]
        if in_window:
            in_window.sort(key=lambda x: x[0], reverse=True)
            chosen = in_window[0][1]
            print(f"[INFO] Selected weekly folder (Mon–Fri window): {chosen}")
            return chosen
        # Fallback: most recent dated folder
        dated.sort(key=lambda x: x[0], reverse=True)
        chosen = dated[0][1]
        print(f"[WARN] No folder in Mon–Fri window; falling back to most recent dated folder: {chosen}")
        return chosen

    # Fallback: sort by mtime if no dated folders found
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _load_reviewed_dataframe(reviewed_path: str) -> pd.DataFrame:
    reviewed_path = os.path.abspath(reviewed_path)
    ext = os.path.splitext(reviewed_path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(reviewed_path, dtype=str)

    if ext == ".xlsx":
        try:
            return pd.read_excel(reviewed_path, dtype=str)
        except ImportError as exc:
            csv_fallback = os.path.splitext(reviewed_path)[0] + ".csv"
            if os.path.exists(csv_fallback):
                print(f"[WARN] openpyxl not available ({exc}); fallback to CSV: {csv_fallback}")
                return pd.read_csv(csv_fallback, dtype=str)
            raise

    raise ValueError(f"Unsupported reviewed file format: {reviewed_path}")


def _ingest_reviewed_labels(reviewed_xlsx: str, output_dir: str, stamp: str) -> Tuple[str, int, int]:
    df = _load_reviewed_dataframe(reviewed_xlsx)
    df = df.where(df.notna(), other="")
    rows = df.to_dict(orient="records")
    accepted: List[Dict[str, str]] = []

    for r in rows:
        title = _clean(r.get("ips_title"))
        human_category = _normalise_label(_clean(r.get("human_category")))
        technology = _clean(r.get("technology"))
        pred_existing = _clean(r.get("predicted_category_existing"))
        pred_model = _clean(r.get("predicted_category_model"))
        pred = pred_existing or pred_model

        if not title or not human_category:
            continue

        accepted.append(
            {
                "ips_title": title,
                "predicted_category": pred,
                "technology": technology,
                "human_category": human_category,
            }
        )

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"weekly_labels_{stamp}.csv")
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ips_title", "predicted_category", "technology", "human_category"])
        writer.writeheader()
        writer.writerows(accepted)

    return out_path, len(accepted), len(rows)


def _prepare_eval_data(input_dirs: List[str]) -> Tuple[List[str], List[str], List[str], List[str]]:
    from sklearn.model_selection import train_test_split  # type: ignore

    rows = _load_training_rows(input_dirs)
    label_counts: Dict[str, int] = {}
    for r in rows:
        label = r["human_category"]
        label_counts[label] = label_counts.get(label, 0) + 1
    rows = [r for r in rows if label_counts.get(r["human_category"], 0) >= 2]

    x = [r["feature_text"] for r in rows]
    y = [r["human_category"] for r in rows]

    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y,
    )
    return x_train, x_test, y_train, y_test


def _eval_pipeline(pipeline: Any, x_test: List[str], y_test: List[str]) -> Dict[str, Any]:
    from sklearn.metrics import classification_report, f1_score  # type: ignore

    y_pred = pipeline.predict(x_test)
    report = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
    macro_f1 = float(f1_score(y_test, y_pred, average="macro", zero_division=0))
    return {
        "macro_f1": macro_f1,
        "report": report,
    }


def _get_pipeline(model_path: str) -> Any:
    artifact = joblib.load(model_path)
    if isinstance(artifact, dict) and "pipeline" in artifact:
        return artifact["pipeline"]
    return artifact


def _recall(report: Dict[str, Any], label: str) -> float | None:
    item = report.get(label)
    if isinstance(item, dict) and "recall" in item:
        return float(item["recall"])
    return None


def _backup_file(path: str, archive_dir: str, suffix: str) -> None:
    if not os.path.exists(path):
        return
    os.makedirs(archive_dir, exist_ok=True)
    name = os.path.basename(path)
    dst = os.path.join(archive_dir, f"{os.path.splitext(name)[0]}_{suffix}{os.path.splitext(name)[1]}")
    shutil.copy2(path, dst)


def main() -> int:
    parser = argparse.ArgumentParser(description="Friday auto close-loop with auto model promotion.")
    parser.add_argument("--tuning-root", default="tuning_outputs")
    parser.add_argument("--input-dir", default="", help="Deprecated single input dir; use --input-dirs")
    parser.add_argument("--input-dirs", default="CFE_reviewed_issue,CFE_input")
    parser.add_argument("--weekly-output-dir", default="CFE_input")
    parser.add_argument("--active-model", default=os.path.join("models", "issue_category_model.joblib"))
    parser.add_argument("--active-metrics", default=os.path.join("models", "issue_category_model_metrics.json"))
    parser.add_argument("--candidate-dir", default=os.path.join("models", "candidates"))
    parser.add_argument("--archive-dir", default=os.path.join("models", "archive"))
    parser.add_argument("--min-new-labels", type=int, default=10)
    parser.add_argument("--min-gain", type=float, default=0.005)
    parser.add_argument("--key-recall-drop-tolerance", type=float, default=0.02)
    parser.add_argument("--key-classes", default="Audio,Connectivity,BSOD,System Hang,Performance")
    parser.add_argument("--rerun-threshold", type=float, default=0.45)
    args = parser.parse_args()
    input_dirs = [p.strip() for p in str(args.input_dirs).split(",") if p.strip()]
    if args.input_dir:
        input_dirs = [args.input_dir]
    if args.weekly_output_dir and args.weekly_output_dir not in input_dirs:
        input_dirs.append(args.weekly_output_dir)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest_weekly = _find_latest_weekly_dir(args.tuning_root)
    reviewed_xlsx = os.path.join(latest_weekly, "weekly_labeling_template.xlsx")
    reviewed_csv = os.path.join(latest_weekly, "weekly_labeling_template.csv")
    if os.path.exists(reviewed_xlsx):
        reviewed_source = reviewed_xlsx
    elif os.path.exists(reviewed_csv):
        reviewed_source = reviewed_csv
        print(f"[WARN] XLSX reviewed file not found; using CSV fallback: {reviewed_source}")
    else:
        raise FileNotFoundError(
            f"Reviewed file not found: {reviewed_xlsx} or {reviewed_csv}"
        )

    ingested_path, accepted_count, total_rows = _ingest_reviewed_labels(reviewed_source, args.weekly_output_dir, stamp)
    print(f"[INFO] Reviewed file: {reviewed_source}")
    print(f"[INFO] Accepted labels: {accepted_count}/{total_rows}")
    print(f"[INFO] Ingested to: {ingested_path}")

    decision_report_path = os.path.join(latest_weekly, "model_promotion_decision.json")

    if accepted_count < int(args.min_new_labels):
        report = {
            "timestamp": stamp,
            "action": "skip",
            "reason": f"accepted labels {accepted_count} < min_new_labels {args.min_new_labels}",
            "accepted_labels": accepted_count,
            "reviewed_rows": total_rows,
            "weekly_dir": latest_weekly,
        }
        with open(decision_report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
        print("[WARN] Skip promotion due to insufficient newly labeled rows.")
        return 0

    os.makedirs(args.candidate_dir, exist_ok=True)
    candidate_model = os.path.join(args.candidate_dir, "issue_category_model_candidate.joblib")
    candidate_metrics = os.path.join(args.candidate_dir, "issue_category_model_candidate_metrics.json")

    train_model(
        input_dirs=input_dirs,
        model_out=candidate_model,
        metrics_out=candidate_metrics,
        test_size=0.2,
        random_state=42,
    )

    _, x_test, _, y_test = _prepare_eval_data(input_dirs)
    active_eval = _eval_pipeline(_get_pipeline(args.active_model), x_test, y_test)
    candidate_eval = _eval_pipeline(_get_pipeline(candidate_model), x_test, y_test)

    active_macro = float(active_eval["macro_f1"])
    candidate_macro = float(candidate_eval["macro_f1"])
    gain = candidate_macro - active_macro

    key_classes = [c.strip() for c in str(args.key_classes).split(",") if c.strip()]
    recall_checks: List[Dict[str, Any]] = []
    recall_ok = True
    for cls in key_classes:
        old_r = _recall(active_eval["report"], cls)
        new_r = _recall(candidate_eval["report"], cls)
        if old_r is None or new_r is None:
            recall_checks.append({"class": cls, "active_recall": old_r, "candidate_recall": new_r, "status": "not_present"})
            continue
        ok = new_r >= (old_r - float(args.key_recall_drop_tolerance))
        recall_checks.append({"class": cls, "active_recall": old_r, "candidate_recall": new_r, "status": "pass" if ok else "fail"})
        if not ok:
            recall_ok = False

    pass_gain = gain >= float(args.min_gain)
    promote = bool(pass_gain and recall_ok)

    report = {
        "timestamp": stamp,
        "weekly_dir": latest_weekly,
        "accepted_labels": accepted_count,
        "reviewed_rows": total_rows,
        "active_macro_f1": active_macro,
        "candidate_macro_f1": candidate_macro,
        "macro_f1_gain": gain,
        "min_gain": float(args.min_gain),
        "pass_gain": pass_gain,
        "key_recall_drop_tolerance": float(args.key_recall_drop_tolerance),
        "recall_checks": recall_checks,
        "promoted": promote,
    }

    if promote:
        _backup_file(args.active_model, args.archive_dir, stamp)
        _backup_file(args.active_metrics, args.archive_dir, stamp)
        shutil.copy2(candidate_model, args.active_model)
        if os.path.exists(candidate_metrics):
            shutil.copy2(candidate_metrics, args.active_metrics)
        report["action"] = "promote"
        print("[OK] Candidate promoted to active model.")
    else:
        report["action"] = "keep_active"
        print("[INFO] Candidate not promoted; keep active model.")

    with open(decision_report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    print(f"[OK] Decision report: {decision_report_path}")

    cmd = [
        os.sys.executable,
        "run_category_tuning_cycle.py",
        "--input-dirs",
        ",".join(input_dirs),
        "--output-dir",
        latest_weekly,
        "--low-confidence-threshold",
        str(args.rerun_threshold),
    ]
    subprocess.run(cmd, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
