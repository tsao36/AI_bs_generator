from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from Meeting_agenda_OneNote import (  # type: ignore
    get_graph_token_app_only,
    get_graph_token_delegated_with_secret,
    load_recipients,
    resolve_graph_client_secret,
    send_mail_via_graph,
)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _run_base_analysis(
    *,
    python_exe: str,
    script_path: str,
    table: str,
    model: str,
    output_dir: str,
    year: int,
) -> None:
    cmd = [
        python_exe,
        script_path,
        "--start-year",
        str(year),
        "--end-year",
        str(year),
        "--table",
        table,
        "--model",
        model,
        "--output-dir",
        output_dir,
        "--customer",
        "all",
    ]
    print("[INFO] Running base customer analysis for management monthly report...")
    print(f"[INFO] Command: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def _load_rows(detail_csv: str, year: int) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with open(detail_csv, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if _clean(row.get("issue_year")) != str(year):
                continue
            rows.append({k: _clean(v) for k, v in row.items()})
    return rows


def _month_key(ips_created_date: str) -> str:
    text = _clean(ips_created_date)
    if len(text) >= 7:
        return text[:7]
    return "unknown"


def _pct(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part * 100.0) / whole, 2)


def _build_management_html(year: int, rows: List[Dict[str, str]]) -> str:
    month_counts: Counter[str] = Counter()
    month_wifi: Counter[str] = Counter()
    month_bt: Counter[str] = Counter()
    month_critical: Counter[str] = Counter()

    cat_counts: Counter[str] = Counter()
    exp_counts: Counter[str] = Counter()

    for row in rows:
        month = _month_key(row.get("ips_created_date", ""))
        month_counts[month] += 1

        tech = _clean(row.get("technology"))
        if tech.upper() == "WIFI":
            month_wifi[month] += 1
        elif tech.upper() == "BT":
            month_bt[month] += 1

        exposure = _clean(row.get("jira_exposure")) or "(missing)"
        if exposure == "1-Critical":
            month_critical[month] += 1
        exp_counts[exposure] += 1

        category = _clean(row.get("deduced_issue_category")) or "(missing)"
        cat_counts[category] += 1

    sorted_months = sorted([m for m in month_counts.keys() if m != "unknown"])
    if "unknown" in month_counts:
        sorted_months.append("unknown")

    total = len(rows)
    wifi_total = sum(1 for r in rows if _clean(r.get("technology")).upper() == "WIFI")
    bt_total = sum(1 for r in rows if _clean(r.get("technology")).upper() == "BT")

    top_cats = cat_counts.most_common(5)
    top_exps = exp_counts.most_common(5)

    month_table_rows = "\n".join(
        [
            "<tr>"
            f"<td>{m}</td>"
            f"<td>{month_counts[m]}</td>"
            f"<td>{month_wifi[m]}</td>"
            f"<td>{month_bt[m]}</td>"
            f"<td>{month_critical[m]}</td>"
            "</tr>"
            for m in sorted_months
        ]
    )

    cat_rows = "\n".join(
        [
            "<tr>"
            f"<td>{name}</td>"
            f"<td>{count}</td>"
            f"<td>{_pct(count, total)}%</td>"
            "</tr>"
            for name, count in top_cats
        ]
    )

    exp_rows = "\n".join(
        [
            "<tr>"
            f"<td>{name}</td>"
            f"<td>{count}</td>"
            f"<td>{_pct(count, total)}%</td>"
            "</tr>"
            for name, count in top_exps
        ]
    )

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Management Monthly Customer Issue Summary ({year})</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #222; }}
    h1 {{ margin-bottom: 6px; }}
    .meta {{ color: #555; margin-bottom: 16px; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 10px; margin-bottom: 18px; }}
    .card {{ border: 1px solid #d8d8d8; border-radius: 8px; padding: 10px; background: #fafafa; }}
    .k {{ font-size: 24px; font-weight: bold; }}
    .l {{ font-size: 12px; color: #555; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
    th {{ background: #f2f5f8; }}
  </style>
</head>
<body>
  <h1>All Customers - Management Monthly Issue Summary ({year})</h1>
  <div class=\"meta\">Generated at: {generated_at} | Scope: 2026 only | Simplified for management monthly review</div>

  <div class=\"cards\">
    <div class=\"card\"><div class=\"k\">{total}</div><div class=\"l\">Total Issues ({year})</div></div>
    <div class=\"card\"><div class=\"k\">{wifi_total}</div><div class=\"l\">WiFi Issues</div></div>
    <div class=\"card\"><div class=\"k\">{bt_total}</div><div class=\"l\">BT Issues</div></div>
    <div class=\"card\"><div class=\"k\">{sum(month_critical.values())}</div><div class=\"l\">Critical (1-Critical)</div></div>
  </div>

  <h2>Monthly Breakdown ({year})</h2>
  <table>
    <thead>
      <tr><th>Month</th><th>Total Issues</th><th>WiFi</th><th>BT</th><th>Critical (1-Critical)</th></tr>
    </thead>
    <tbody>
      {month_table_rows}
    </tbody>
  </table>

  <div class=\"grid\">
    <div>
      <h2>Top 5 Issue Categories ({year})</h2>
      <table>
        <thead><tr><th>Category</th><th>Count</th><th>%</th></tr></thead>
        <tbody>{cat_rows}</tbody>
      </table>
    </div>
    <div>
      <h2>Top 5 Jira Exposure Buckets ({year})</h2>
      <table>
        <thead><tr><th>Exposure</th><th>Count</th><th>%</th></tr></thead>
        <tbody>{exp_rows}</tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""


def _get_token(graph_auth_mode: str) -> Tuple[str, str]:
    mode = _clean(graph_auth_mode).lower() or _clean(os.getenv("GRAPH_AUTH_MODE") or "delegated").lower()
    client_secret = resolve_graph_client_secret()
    if mode == "delegated":
        token = get_graph_token_delegated_with_secret(client_secret, scopes=["Mail.Send"])
        return token, ""

    sender_upn = _clean(os.getenv("GRAPH_SENDER_UPN"))
    if not sender_upn:
        raise RuntimeError("GRAPH_SENDER_UPN is required when GRAPH_AUTH_MODE=app")
    token = get_graph_token_app_only(client_secret, scopes=["https://graph.microsoft.com/.default"])
    return token, sender_upn


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and send management monthly customer issue HTML report.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--table", default="ips_jira_bugs")
    parser.add_argument("--model", default=os.path.join("models", "issue_category_model.joblib"))
    parser.add_argument("--output-dir", default=os.path.join("customer issue analysis", "outputs"))
    parser.add_argument(
        "--recipients",
        default=os.path.join("customer issue analysis", "recipients_management_monthly.json"),
        help="Recipients JSON file (to/cc).",
    )
    parser.add_argument("--subject", default="")
    parser.add_argument("--graph-auth-mode", choices=["app", "delegated"], default=os.getenv("GRAPH_AUTH_MODE", "delegated"))
    parser.add_argument("--dry-run-email", action="store_true", help="Generate files but do not send email.")
    parser.add_argument(
        "--allow-email-failure",
        action="store_true",
        help="Do not fail the process when Graph email sending fails.",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    customer_analysis_script = os.path.join(script_dir, "customer_issue_analysis.py")
    python_exe = sys.executable

    _run_base_analysis(
        python_exe=python_exe,
        script_path=customer_analysis_script,
        table=args.table,
        model=args.model,
        output_dir=args.output_dir,
        year=args.year,
    )

    range_tag = f"{args.year}_{args.year}"
    detail_csv = os.path.join(args.output_dir, f"all_customers_issue_analysis_{range_tag}_detail.csv")
    if not os.path.exists(detail_csv):
        raise FileNotFoundError(f"Detail CSV not found: {detail_csv}")

    rows = _load_rows(detail_csv, args.year)
    html_content = _build_management_html(args.year, rows)

    mgmt_html = os.path.join(args.output_dir, f"all_customers_issue_analysis_management_monthly_{args.year}_report.html")
    with open(mgmt_html, "w", encoding="utf-8") as handle:
        handle.write(html_content)

    print(f"[OK] Management monthly report generated: {mgmt_html}")

    if args.dry_run_email:
        print("[INFO] Dry run enabled; skip email sending.")
        return 0

    recipients_path = args.recipients
    if not os.path.isabs(recipients_path):
        recipients_path = os.path.join(repo_root, recipients_path)

    to_list, cc_list = load_recipients(recipients_path)
    if not to_list:
        raise RuntimeError(f"Recipient list is empty. Check: {recipients_path}")

    try:
        token, sender_upn = _get_token(args.graph_auth_mode)
        subject = _clean(args.subject) or f"[Management Monthly] All Customers Issue Summary ({args.year})"

        send_mail_via_graph(
            token,
            subject,
            html_content,
            to_list,
            cc_list,
            content_type="HTML",
            sender_upn=(sender_upn or None),
        )

        print(f"[OK] Management monthly HTML report email sent to: {', '.join(to_list)}")
        if cc_list:
            print(f"[OK] CC: {', '.join(cc_list)}")
    except Exception as exc:
        if not args.allow_email_failure:
            raise
        print(f"[WARN] Email send skipped due to error: {exc}")
        print("[WARN] Report files are generated successfully. Scheduler is allowed to continue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
