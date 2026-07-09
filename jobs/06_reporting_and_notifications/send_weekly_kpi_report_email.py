from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
import urllib.request
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from Meeting_agenda_OneNote import (  # type: ignore
    get_graph_token_app_only,
    get_graph_token_delegated_with_secret,
    resolve_graph_client_secret,
    send_mail_via_graph,
)


def _configure_stdio() -> None:
    """Best-effort console encoding hardening for Windows scheduled runs."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _clean(value: object) -> str:
    return str(value or "").strip()


def _is_missing(value: object) -> bool:
    return _clean(value).lower() in {"", "na", "n/a", "none", "null"}


def _generate_zh_summary(report: Dict[str, Any]) -> Optional[str]:
    """Call local Ollama to generate a Traditional Chinese summary of the KPI report."""
    base_url = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("LOCAL_LLM_MODEL", "llama3.1:8b-instruct-q4_K_M")

    checks = report.get("checks") or {}
    round_obj = report.get("round_completion") or {}
    overall = "通過" if report.get("overall_pass") else "未通過"
    round_done = "已完成" if round_obj.get("round_complete") else "尚未完成"
    latest_week = os.path.basename(_clean(report.get("latest_week")) or "unknown")

    accepted = (checks.get("accepted_labels") or {}).get("details", "")
    gain = (checks.get("macro_f1_gain_trend") or {}).get("details", "")
    conf = (checks.get("top_confusions_shrink") or {}).get("details", "")

    prompt = (
        f"你是一位 AI 模型訓練進度分析師。請用繁體中文，以3到5句話，用平易近人的語言摘要以下每週 KPI 健康檢查結果。\n"
        f"不要列點，直接寫成段落。\n\n"
        f"最新週別：{latest_week}\n"
        f"整體結果：{overall}\n"
        f"本輪訓練狀態：{round_done}\n"
        f"已接受標籤數：{accepted}\n"
        f"Macro F1 趨勢：{gain}\n"
        f"混淆矩陣收斂：{conf}\n"
        f"Round completion 細節：{_clean(round_obj.get('details'))}\n"
    )

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        summary = _clean(result.get("response"))
        if summary:
            print(f"[LLM] Generated Traditional Chinese summary ({len(summary)} chars).")
            return summary
    except Exception as exc:
        print(f"[LLM] Local LLM unavailable, skipping summary: {exc}")
    return None


def _find_latest_weekly_dir(tuning_root: str) -> str:
    candidates = [p for p in glob.glob(os.path.join(tuning_root, "weekly_*")) if os.path.isdir(p)]
    if not candidates:
        raise FileNotFoundError(f"No weekly_* folders found under: {tuning_root}")
    candidates.sort(key=lambda p: os.path.basename(p).lower())
    return candidates[-1]


def _load_report(report_path: str) -> Dict[str, Any]:
    if not os.path.exists(report_path):
        raise FileNotFoundError(f"KPI report not found: {report_path}")
    with open(report_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid KPI report format: {report_path}")
    return payload


def _load_csv_rows_fallback(path: str) -> List[Dict[str, str]]:
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    last_error: Exception | None = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return []


def _build_pending_labeling_top_section(report: Dict[str, Any]) -> List[str]:
    """Build top-of-email section listing unfilled template lines by assignee email."""
    latest_week_dir = _clean(report.get("latest_week"))
    if not latest_week_dir:
        return []

    csv_path = os.path.join(latest_week_dir, "weekly_labeling_template.csv")
    if not os.path.exists(csv_path):
        return [
            "【標記填寫追蹤】",
            "找不到 weekly_labeling_template.csv，這份作業像是被 Wi-Fi 吃掉了。",
            "",
            "=" * 40,
            "",
        ]

    rows = _load_csv_rows_fallback(csv_path)
    if not rows:
        return [
            "【標記填寫追蹤】",
            "weekly_labeling_template.csv 目前沒有資料列，大家今天可能在放空，也可能在充電。",
            "",
            "=" * 40,
            "",
        ]

    required_cols = {"human_category", "assignee_email"}
    if not required_cols.issubset(set(rows[0].keys())):
        return [
            "【標記填寫追蹤】",
            "CSV 欄位不完整（需要 human_category、assignee_email），目前暫時無法點名誰還在觀望。",
            "",
            "=" * 40,
            "",
        ]

    pending_by_email: Dict[str, List[int]] = {}
    for idx, row in enumerate(rows, start=2):
        if _is_missing(row.get("human_category")):
            email = _clean(row.get("assignee_email")).lower() or "(unassigned)"
            pending_by_email.setdefault(email, []).append(idx)

    if not pending_by_email:
        return [
            "【標記填寫追蹤】",
            "本週全部都填好了，太神了。咖啡可以先放下，掌聲請持續 5 秒。",
            "",
            "=" * 40,
            "",
        ]

    total_pending = sum(len(v) for v in pending_by_email.values())
    sections: List[str] = []
    sections.append("【標記填寫追蹤（置頂）】")
    sections.append("重要提醒：若有人未填寫模板，整批資料就無法有效使用，會讓其他同仁前面的整理與分析時間被白白浪費；請大家一起補齊。")
    sections.append(f"未填寫總數：{total_pending} 筆（CSV 行號包含標頭，資料從第 2 行開始）")
    sections.append(f"來源檔案：{os.path.abspath(csv_path)}")
    sections.append("")
    sections.append("未填寫行號 by assignee_email：")

    for email, lines in sorted(pending_by_email.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        line_text = ", ".join(str(n) for n in lines)
        sections.append(f"- {email}: {line_text}")

    sections.append("")
    sections.append("小幽默：這些空白格看似安靜，實際上正在把團隊工時偷偷吃掉。")
    sections.append("")
    sections.append("=" * 40)
    sections.append("")
    return sections


def _fmt_check(name: str, item: Dict[str, Any]) -> str:
    status = "通過" if bool(item.get("pass")) else "未通過"
    details = _clean(item.get("details"))
    return f"- {name}：{status}\n  {details}"


def _get_latest_top_confusions(report: Dict[str, Any], top_n: int = 3) -> List[str]:
    weeks = report.get("weeks")
    if not isinstance(weeks, list) or not weeks:
        return []
    latest = weeks[-1]
    top = latest.get("top_confusions")
    if not isinstance(top, list):
        return []

    lines: List[str] = []
    for item in top[: max(1, top_n)]:
        if not isinstance(item, dict):
            continue
        t = _clean(item.get("true"))
        p = _clean(item.get("pred"))
        c = _clean(item.get("count"))
        lines.append(f"- {t} -> {p}: {c}")
    return lines


def _build_subject(report: Dict[str, Any], subject_prefix: str = "") -> str:
    overall = "通過" if bool(report.get("overall_pass")) else "未通過"
    round_obj = report.get("round_completion") if isinstance(report.get("round_completion"), dict) else {}
    round_state = "本輪完成" if bool(round_obj.get("round_complete")) else "本輪進行中"
    latest_week = os.path.basename(_clean(report.get("latest_week")) or "weekly_unknown")
    subject = f"[每週 KPI][{overall}][{round_state}] {latest_week}"
    prefix = _clean(subject_prefix)
    if prefix:
        return f"{prefix} {subject}"
    return subject


def _load_supplement_summary(supplement_path: str) -> Optional[Dict[str, int]]:
    """Load the targeted labeling supplement CSV and return category -> count mapping."""
    if not supplement_path or not os.path.exists(supplement_path):
        return None
    try:
        counts: Counter = Counter()
        with open(supplement_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                cat = _clean(row.get("predicted_category_model"))
                if cat:
                    counts[cat] += 1
        return dict(counts) if counts else None
    except Exception as exc:
        print(f"[WARN] 無法讀取補標清單：{exc}")
        return None


def _build_improvement_section(report: Dict[str, Any], supplement_path: str) -> List[str]:
    """Generate actionable improvement suggestions based on KPI failures."""
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    gain_check = checks.get("macro_f1_gain_trend") or {}
    conf_check = checks.get("top_confusions_shrink") or {}
    accepted_check = checks.get("accepted_labels") or {}
    weeks = report.get("weeks") or []

    lines: List[str] = []
    lines.append("【下週改善行動建議】")
    lines.append("=" * 40)

    any_suggestion = False

    # --- F1 trend failing ---
    if not bool(gain_check.get("pass")):
        any_suggestion = True
        details = _clean(gain_check.get("details"))
        lines.append("▶ Macro F1 趨勢未通過")
        lines.append(f"  {details}")
        lines.append("  建議：增加標記量（目標每週 ≥ 25 筆），優先補充混淆類別的反例")
        lines.append("")

    # --- Confusion pairs still present ---
    if not bool(conf_check.get("pass")):
        any_suggestion = True
        details = _clean(conf_check.get("details"))
        lines.append("▶ 混淆矩陣未收斂")
        lines.append(f"  {details}")
        # Parse pairs from details string e.g. "pairs=[A->B: 3->2; C->D: 1->3]"
        if "pairs=[" in details:
            pairs_str = details.split("pairs=[")[1].rstrip("]")
            lines.append("  本週持續混淆對（需針對補標）：")
            for pair in pairs_str.split(";"):
                pair = pair.strip()
                if pair:
                    lines.append(f"    - {pair}")
        lines.append("")

    # --- Low label volume ---
    if weeks:
        last_accepted = weeks[-1].get("accepted_labels", 0) or 0
        if last_accepted < 25:
            any_suggestion = True
            lines.append(f"▶ 本週標記量偏低（{last_accepted} 筆，建議 ≥ 25 筆）")
            if last_accepted > 0:
                lines.append(f"  本週 {last_accepted} 筆建議與下週合併後一次送訓，效果更佳")
            lines.append("")

    # --- Supplement file summary ---
    supplement_summary = _load_supplement_summary(supplement_path)
    if supplement_summary:
        lines.append("▶ 補標候選清單已自動生成")
        lines.append(f"  檔案：{os.path.abspath(supplement_path)}")
        lines.append("  各類別候選數量：")
        for cat, n in sorted(supplement_summary.items(), key=lambda x: -x[1]):
            lines.append(f"    - {cat}：{n} 筆")
        lines.append("  操作步驟：")
        lines.append("  1. 下週 Monday pipeline 執行後，將此檔案追加至 weekly_labeling_template.csv")
        lines.append("  2. 優先標記 label_notes 欄標注 [補標目標] 的項目")
        lines.append("")
    elif not any_suggestion:
        lines.append("本週所有 KPI 通過，維持現有標記流程即可。")
        lines.append("")

    return lines


def _build_body(
    report: Dict[str, Any],
    report_path: str,
    zh_summary: Optional[str] = None,
    supplement_path: str = "",
    extra_note: str = "",
) -> str:
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    round_obj = report.get("round_completion") if isinstance(report.get("round_completion"), dict) else {}
    accepted = checks.get("accepted_labels") if isinstance(checks.get("accepted_labels"), dict) else {}
    gain = checks.get("macro_f1_gain_trend") if isinstance(checks.get("macro_f1_gain_trend"), dict) else {}
    conf = checks.get("top_confusions_shrink") if isinstance(checks.get("top_confusions_shrink"), dict) else {}

    lines: List[str] = []
    pending_section = _build_pending_labeling_top_section(report)
    if pending_section:
        lines.extend(pending_section)

    round_complete = bool(round_obj.get("round_complete"))
    note_text = _clean(extra_note)
    if note_text:
        lines.append("【流程警示】")
        lines.append(note_text)
        lines.append("")
        lines.append("=" * 40)
        lines.append("")
    if zh_summary:
        lines.append("【本週 KPI 摘要（繁體中文）】")
        lines.append(zh_summary)
        lines.append("")
        lines.append("=" * 40)
        lines.append("")
    lines.append("每週 KPI 健康檢查結果")
    lines.append("=" * 40)
    lines.append(f"產生時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"整體結果：{'通過' if bool(report.get('overall_pass')) else '未通過'}")
    lines.append(f"本輪完成：{'是' if bool(round_obj.get('round_complete')) else '否'}")
    if round_complete:
        lines.append("摘要：調校已達完成條件，可進入維護模式。")
    else:
        lines.append("摘要：調校尚未達完成條件，請繼續每週標記與調校流程。")
    lines.append(f"最新週別：{_clean(report.get('latest_week'))}")
    lines.append(f"報告檔案：{os.path.abspath(report_path)}")
    lines.append("")
    lines.append("檢查項目：")
    lines.append(_fmt_check("已接受標籤數", accepted))
    lines.append(_fmt_check("Macro F1 趨勢", gain))
    lines.append(_fmt_check("混淆矩陣收斂", conf))
    lines.append("")
    lines.append("本輪完成細節：")
    lines.append(f"- 詳細說明：{_clean(round_obj.get('details')) or '無資料'}")
    lines.append(f"- 穩定週數：{_clean(round_obj.get('stability_weeks')) or '無資料'}")
    lines.append(f"- 最大絕對平均增益：{_clean(round_obj.get('max_abs_avg_gain')) or '無資料'}")
    lines.append(f"- 最大混淆降幅：{_clean(round_obj.get('max_confusion_drop')) or '無資料'}")
    lines.append("")
    lines.append("最新週前幾大混淆：")
    top_lines = _get_latest_top_confusions(report, top_n=3)
    if top_lines:
        lines.extend(top_lines)
    else:
        lines.append("- 無資料")
    lines.append("")
    improvement_lines = _build_improvement_section(report, supplement_path)
    if improvement_lines:
        lines.extend(improvement_lines)
    lines.append("此郵件由 run_weekly_kpi_health_check.bat 自動產生")
    return "\n".join(lines)


def _get_token(graph_auth_mode: str) -> Tuple[str, str]:
    secret = resolve_graph_client_secret()
    mode = _clean(graph_auth_mode).lower() or _clean(os.getenv("GRAPH_AUTH_MODE") or "app").lower()
    if mode == "delegated":
        token = get_graph_token_delegated_with_secret(secret, scopes=["Mail.Send"])
        return token, ""

    sender_upn = _clean(os.getenv("GRAPH_SENDER_UPN"))
    if not sender_upn:
        raise RuntimeError("GRAPH_SENDER_UPN is required for app mode email sending.")
    token = get_graph_token_app_only(secret, scopes=["https://graph.microsoft.com/.default"])
    return token, sender_upn


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Send weekly KPI health check email from JSON report.")
    parser.add_argument("--tuning-root", default="tuning_outputs")
    parser.add_argument("--report", default="")
    parser.add_argument("--to", default="tsao36@gmail.com")
    parser.add_argument("--cc", default="")
    parser.add_argument("--supplement", default="", help="Path to targeted_labeling_supplement_*.csv")
    parser.add_argument("--subject-prefix", default="", help="Optional subject prefix, e.g. [ACTION REQUIRED]")
    parser.add_argument("--extra-note", default="", help="Optional extra note to include at top of email body")
    parser.add_argument("--graph-auth-mode", choices=["app", "delegated"], default=os.getenv("GRAPH_AUTH_MODE", "app"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report_path = _clean(args.report)
    if not report_path:
        latest_week = _find_latest_weekly_dir(_clean(args.tuning_root))
        report_path = os.path.join(latest_week, "weekly_kpi_health_check.json")

    report = _load_report(report_path)
    subject = _build_subject(report, subject_prefix=_clean(args.subject_prefix))
    zh_summary = _generate_zh_summary(report)
    supplement_path = _clean(args.supplement)
    # Auto-detect supplement file if not specified
    if not supplement_path:
        from datetime import date
        candidate = f"targeted_labeling_supplement_{date.today().strftime('%Y%m%d')}.csv"
        if os.path.exists(candidate):
            supplement_path = candidate
    body = _build_body(
        report,
        report_path,
        zh_summary=zh_summary,
        supplement_path=supplement_path,
        extra_note=_clean(args.extra_note),
    )

    to_list = [x.strip() for x in _clean(args.to).split(",") if x.strip()]
    cc_list = [x.strip() for x in _clean(args.cc).split(",") if x.strip()]

    # Merge recipients from recipients.json if it exists
    recipients_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recipients.json")
    if os.path.exists(recipients_path):
        try:
            with open(recipients_path, "r", encoding="utf-8") as f:
                rdata = json.load(f)
            extra_to = [x.strip() for x in (rdata.get("to") or []) if isinstance(x, str) and x.strip()]
            extra_cc = [x.strip() for x in (rdata.get("cc") or []) if isinstance(x, str) and x.strip()]
            # Merge without duplicates, preserving order
            seen_to = set(x.lower() for x in to_list)
            for addr in extra_to:
                if addr.lower() not in seen_to:
                    to_list.append(addr)
                    seen_to.add(addr.lower())
            seen_cc = set(x.lower() for x in cc_list)
            for addr in extra_cc:
                if addr.lower() not in seen_cc:
                    cc_list.append(addr)
                    seen_cc.add(addr.lower())
            print(f"[INFO] Loaded recipients.json: +{len(extra_to)} to, +{len(extra_cc)} cc")
        except Exception as exc:
            print(f"[WARN] Could not load recipients.json: {exc}")

    if not to_list:
        raise RuntimeError("No email recipients specified for --to")

    if args.dry_run:
        print("[DRY RUN] To:", to_list)
        print("[DRY RUN] Cc:", cc_list)
        print("[DRY RUN] Subject:", subject)
        print("\n--- Body Preview ---\n")
        print(body)
        return 0

    token, sender_upn = _get_token(_clean(args.graph_auth_mode))
    send_mail_via_graph(
        token=token,
        subject=subject,
        body_text=body,
        to_addrs=to_list,
        cc_addrs=cc_list or None,
        save_to_sent_items=True,
        content_type="Text",
        sender_upn=sender_upn or None,
    )
    print(f"[OK] KPI 郵件已寄出至：{', '.join(to_list)}")
    if cc_list:
        print(f"[OK] KPI 郵件 CC：{', '.join(cc_list)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
