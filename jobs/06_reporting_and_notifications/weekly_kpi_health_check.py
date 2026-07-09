from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class WeekStats:
    weekly_dir: str
    accepted_labels: int
    macro_f1: Optional[float]
    macro_f1_gain: Optional[float]
    top_confusions: List[Tuple[str, str, int]]


def _clean(v: object) -> str:
    return str(v or "").strip()


def _is_missing(v: object) -> bool:
    return _clean(v).lower() in {"", "na", "n/a", "none", "null"}


def _find_weekly_dirs(tuning_root: str) -> List[str]:
    dirs = [p for p in glob.glob(os.path.join(tuning_root, "weekly_*")) if os.path.isdir(p)]
    dirs.sort(key=lambda p: os.path.basename(p).lower())
    return dirs


def _safe_read_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv_rows(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    return []


def _accepted_labels_from_weekly_template(weekly_dir: str) -> int:
    path = os.path.join(weekly_dir, "weekly_labeling_template.csv")
    rows = _read_csv_rows(path)
    if not rows:
        return 0
    if "human_category" not in rows[0]:
        return 0
    return sum(0 if _is_missing(r.get("human_category")) else 1 for r in rows)


def _best_macro_f1_from_compare(weekly_dir: str) -> Optional[float]:
    path = os.path.join(weekly_dir, "model_compare_metrics.json")
    payload = _safe_read_json(path)
    if not isinstance(payload, dict):
        return None
    models = payload.get("models")
    if not isinstance(models, list) or not models:
        return None
    values: List[float] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        try:
            values.append(float(item.get("macro_f1")))
        except Exception:
            continue
    if not values:
        return None
    return max(values)


def _top_confusions(weekly_dir: str, top_n: int) -> List[Tuple[str, str, int]]:
    path = os.path.join(weekly_dir, "best_model_confusion_matrix.csv")
    rows = _read_csv_rows(path)
    if not rows:
        return []

    first_col = None
    if rows and rows[0]:
        first_col = list(rows[0].keys())[0]
    if not first_col:
        return []

    pairs: List[Tuple[str, str, int]] = []
    for row in rows:
        true_label = _clean(row.get(first_col))
        for pred_label, raw_val in row.items():
            if pred_label == first_col:
                continue
            if pred_label == true_label:
                continue
            try:
                val = int(float(_clean(raw_val) or 0))
            except Exception:
                val = 0
            if val > 0:
                pairs.append((true_label, pred_label, val))

    pairs.sort(key=lambda x: (-x[2], x[0], x[1]))
    return pairs[: max(1, int(top_n))]


def _build_week_stats(weekly_dirs: List[str], top_n: int) -> List[WeekStats]:
    stats: List[WeekStats] = []
    prev_macro: Optional[float] = None

    for wd in weekly_dirs:
        decision_path = os.path.join(wd, "model_promotion_decision.json")
        decision = _safe_read_json(decision_path) or {}

        accepted = decision.get("accepted_labels")
        if accepted is None:
            accepted_labels = _accepted_labels_from_weekly_template(wd)
        else:
            try:
                accepted_labels = int(accepted)
            except Exception:
                accepted_labels = _accepted_labels_from_weekly_template(wd)

        macro = _best_macro_f1_from_compare(wd)

        gain_raw = decision.get("macro_f1_gain")
        if gain_raw is not None:
            try:
                gain = float(gain_raw)
            except Exception:
                gain = None
        else:
            gain = None
            if macro is not None and prev_macro is not None:
                gain = float(macro - prev_macro)

        top_conf = _top_confusions(wd, top_n=top_n)

        stats.append(
            WeekStats(
                weekly_dir=wd,
                accepted_labels=accepted_labels,
                macro_f1=macro,
                macro_f1_gain=gain,
                top_confusions=top_conf,
            )
        )

        if macro is not None:
            prev_macro = macro

    return stats


def _check_accepted_labels(latest: WeekStats, min_labels: int) -> Tuple[bool, str]:
    ok = latest.accepted_labels >= int(min_labels)
    msg = (
        f"accepted_labels={latest.accepted_labels}, threshold={min_labels}"
    )
    return ok, msg


def _check_macro_gain_trend(stats: List[WeekStats], gain_window: int) -> Tuple[bool, str]:
    gains: List[float] = []
    for s in stats:
        if s.macro_f1_gain is not None:
            gains.append(float(s.macro_f1_gain))

    if not gains:
        return False, "No macro_f1_gain data available yet."

    window = max(1, int(gain_window))
    recent = gains[-window:]
    avg_gain = sum(recent) / len(recent)
    positive_ratio = sum(1 for g in recent if g > 0) / len(recent)

    # "long-term positive": recent average positive and majority of recent gains > 0
    ok = (avg_gain > 0) and (positive_ratio >= 0.6)
    msg = (
        f"recent_gains={recent}, avg_gain={avg_gain:.6f}, positive_ratio={positive_ratio:.2f}"
    )
    return ok, msg


def _confusion_to_map(items: List[Tuple[str, str, int]]) -> Dict[Tuple[str, str], int]:
    return {(a, b): int(v) for a, b, v in items}


def _check_top_confusions_shrink(stats: List[WeekStats], top_n: int) -> Tuple[bool, str]:
    if len(stats) < 2:
        return False, "Need at least 2 weekly folders to compare top confusions trend."

    prev = stats[-2]
    latest = stats[-1]

    prev_top = prev.top_confusions[:top_n]
    latest_top = latest.top_confusions[:top_n]

    if not prev_top or not latest_top:
        return False, "Missing confusion matrix data in one of the latest two weeks."

    prev_map = _confusion_to_map(prev_top)
    latest_map = _confusion_to_map(latest_top)

    prev_sum = sum(v for _, _, v in prev_top)
    latest_on_prev_pairs_sum = sum(latest_map.get((a, b), 0) for a, b, _ in prev_top)

    per_pair = []
    non_increase_all = True
    for a, b, old_v in prev_top:
        new_v = latest_map.get((a, b), 0)
        per_pair.append(f"{a}->{b}: {old_v}->{new_v}")
        if new_v > old_v:
            non_increase_all = False

    ok = non_increase_all and (latest_on_prev_pairs_sum < prev_sum)
    msg = (
        f"prev_top{top_n}_sum={prev_sum}, latest_on_prev_pairs_sum={latest_on_prev_pairs_sum}, "
        f"pairs=[{'; '.join(per_pair)}]"
    )
    return ok, msg


def _check_round_completion(
    stats: List[WeekStats],
    *,
    min_accepted_labels: int,
    stability_weeks: int,
    max_abs_avg_gain: float,
    top_n: int,
    max_confusion_drop: int,
) -> Tuple[bool, str]:
    weeks_required = max(2, int(stability_weeks))
    if len(stats) < weeks_required:
        return False, f"Need at least {weeks_required} weekly folders; only {len(stats)} found."

    recent = stats[-weeks_required:]

    labels_stable = all(s.accepted_labels >= int(min_accepted_labels) for s in recent)

    gains = [float(s.macro_f1_gain) for s in recent if s.macro_f1_gain is not None]
    if len(gains) < max(1, weeks_required - 1):
        return False, "Not enough macro_f1_gain points to determine plateau."
    avg_gain = sum(gains) / len(gains)
    gain_plateau = abs(avg_gain) <= float(max_abs_avg_gain)

    conf_sums: List[int] = []
    for s in recent:
        conf_sums.append(sum(v for _, _, v in s.top_confusions[: max(1, int(top_n))]))
    conf_non_increasing = all(b <= a for a, b in zip(conf_sums, conf_sums[1:]))
    conf_drop = conf_sums[0] - conf_sums[-1]
    conf_plateau = conf_drop <= int(max_confusion_drop)

    ok = labels_stable and gain_plateau and conf_non_increasing and conf_plateau
    details = (
        f"labels_stable={labels_stable}, recent_labels={[s.accepted_labels for s in recent]}, "
        f"avg_gain={avg_gain:.6f}, max_abs_avg_gain={max_abs_avg_gain}, gain_plateau={gain_plateau}, "
        f"conf_sums={conf_sums}, conf_non_increasing={conf_non_increasing}, "
        f"conf_drop={conf_drop}, max_confusion_drop={max_confusion_drop}, conf_plateau={conf_plateau}"
    )
    return ok, details


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly KPI health check for issue-category closed loop.")
    parser.add_argument("--tuning-root", default="tuning_outputs")
    parser.add_argument("--min-accepted-labels", type=int, default=10)
    parser.add_argument("--gain-window", type=int, default=4, help="How many recent gain points define long-term trend.")
    parser.add_argument("--top-confusions", type=int, default=3)
    parser.add_argument("--round-stability-weeks", type=int, default=3, help="How many recent weeks are required to declare tuning round completion.")
    parser.add_argument("--round-max-abs-avg-gain", type=float, default=0.003, help="Round completion requires |recent avg macro_f1_gain| <= this threshold.")
    parser.add_argument("--round-max-confusion-drop", type=int, default=1, help="Round completion requires top confusion total drop within this plateau range.")
    parser.add_argument("--report-out", default="")
    args = parser.parse_args()

    weekly_dirs = _find_weekly_dirs(_clean(args.tuning_root))
    if not weekly_dirs:
        raise FileNotFoundError(f"No weekly_* folders found under: {args.tuning_root}")

    stats = _build_week_stats(weekly_dirs, top_n=max(1, int(args.top_confusions)))
    latest = stats[-1]

    labels_ok, labels_msg = _check_accepted_labels(latest, int(args.min_accepted_labels))
    gain_ok, gain_msg = _check_macro_gain_trend(stats, int(args.gain_window))
    conf_ok, conf_msg = _check_top_confusions_shrink(stats, int(args.top_confusions))
    round_complete, round_msg = _check_round_completion(
        stats,
        min_accepted_labels=int(args.min_accepted_labels),
        stability_weeks=int(args.round_stability_weeks),
        max_abs_avg_gain=float(args.round_max_abs_avg_gain),
        top_n=int(args.top_confusions),
        max_confusion_drop=int(args.round_max_confusion_drop),
    )

    all_ok = labels_ok and gain_ok and conf_ok

    result = {
        "overall_pass": all_ok,
        "round_completion": {
            "round_complete": round_complete,
            "details": round_msg,
            "stability_weeks": int(args.round_stability_weeks),
            "max_abs_avg_gain": float(args.round_max_abs_avg_gain),
            "max_confusion_drop": int(args.round_max_confusion_drop),
        },
        "latest_week": os.path.abspath(latest.weekly_dir),
        "checks": {
            "accepted_labels": {"pass": labels_ok, "details": labels_msg},
            "macro_f1_gain_trend": {"pass": gain_ok, "details": gain_msg},
            "top_confusions_shrink": {"pass": conf_ok, "details": conf_msg},
        },
        "weeks": [
            {
                "weekly_dir": os.path.abspath(s.weekly_dir),
                "accepted_labels": s.accepted_labels,
                "macro_f1": s.macro_f1,
                "macro_f1_gain": s.macro_f1_gain,
                "top_confusions": [
                    {"true": a, "pred": b, "count": v} for a, b, v in s.top_confusions
                ],
            }
            for s in stats
        ],
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))

    report_out = _clean(args.report_out)
    if not report_out:
        report_out = os.path.join(latest.weekly_dir, "weekly_kpi_health_check.json")
    os.makedirs(os.path.dirname(report_out) or ".", exist_ok=True)
    with open(report_out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)

    print(f"[INFO] KPI report written: {os.path.abspath(report_out)}")

    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
