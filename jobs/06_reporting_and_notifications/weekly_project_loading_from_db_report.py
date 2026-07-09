"""Weekly project/customer loading report from ips_jira_bugs.

This report intentionally uses the same database source as
weekly_issue_count_report.py so the weekly batch has one source of truth.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from Wireless_bug_dashboard import DbConnector  # type: ignore
from Meeting_agenda_OneNote import (  # type: ignore
    get_graph_token_app_only,
    get_graph_token_delegated_with_secret,
    load_recipients,
    resolve_graph_client_secret,
    send_mail_via_graph,
)
from offload_reporter_issues import (  # type: ignore
    _allowed_reporters_array_sql,
    _created_date_expr,
    _get_table_columns,
    _has,
    _reporter_expr,
)

LOG = logging.getLogger("weekly_project_loading_from_db_report")


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s [%(levelname)s] %(message)s")
    LOG.setLevel(getattr(logging, level.upper(), logging.INFO))


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip() or default


def _clean_expr(column: str) -> str:
    return f"NULLIF(NULLIF(TRIM({column}::text), ''), 'NA')"


def _first_available(columns: set[str], names: List[str], default: str = "'-'") -> str:
    parts = [_clean_expr(name) for name in names if _has(columns, name)]
    if not parts:
        return default
    if len(parts) == 1:
        return f"COALESCE({parts[0]}, {default})"
    return f"COALESCE({', '.join(parts)}, {default})"


def _project_expr(columns: set[str]) -> str:
    return _first_available(columns, ["bug_project", "ips_product", "jira_team"], "'Unknown'")


def _customer_expr(columns: set[str]) -> str:
    return _first_available(columns, ["hsd_customer_detail", "customer", "jira_customer_name", "ips_oem"], "'Unknown'")


def _issue_count_exprs(columns: set[str]) -> Tuple[str, str, str]:
    ips_present = "FALSE"
    ips_count_expr = "0"
    if _has(columns, "ips_case_number"):
        ips_present = "(ips_case_number::text ~ '^[0-9]+$' AND ips_case_number::int > 0)"
        ips_count_expr = f"CASE WHEN {ips_present} THEN 1 ELSE 0 END"

    jira_parts = []
    if _has(columns, "jira_id"):
        jira_parts.append("NULLIF(NULLIF(TRIM(jira_id::text), ''), 'NA')")
    if _has(columns, "ips_jira_id"):
        jira_parts.append("NULLIF(NULLIF(TRIM(ips_jira_id::text), ''), 'NA')")
    jira_count_expr = "0"
    if jira_parts:
        jira_value = f"COALESCE({', '.join(jira_parts)})" if len(jira_parts) > 1 else jira_parts[0]
        jira_count_expr = f"CASE WHEN NOT ({ips_present}) AND {jira_value} IS NOT NULL THEN 1 ELSE 0 END"

    hsd_count_expr = "0"
    if _has(columns, "hsd_id"):
        hsd_present = "NULLIF(NULLIF(TRIM(hsd_id::text), ''), 'NA') IS NOT NULL"
        hsd_count_expr = f"CASE WHEN {hsd_present} THEN 1 ELSE 0 END"

    return ips_count_expr, jira_count_expr, hsd_count_expr


def _fetch_rows(db: DbConnector, table: str, top_customers: int) -> List[Dict[str, Any]]:
    columns = _get_table_columns(db, table)
    created_expr = _created_date_expr(columns)
    if created_expr == "NULL":
        raise RuntimeError(f"No supported created date column found in {table}.")

    reporter_expr = _reporter_expr(columns)
    reporter_filter = _allowed_reporters_array_sql()
    reporter_where = f"AND LOWER(TRIM(reporter)) = ANY({reporter_filter})" if reporter_filter != "NULL" else ""
    project_expr = _project_expr(columns)
    customer_expr = _customer_expr(columns)
    ips_count_expr, jira_count_expr, hsd_count_expr = _issue_count_exprs(columns)

    query = f"""
        WITH base AS (
            SELECT
                {reporter_expr} AS owner,
                {project_expr} AS project,
                {customer_expr} AS customer,
                {ips_count_expr} AS ips_count,
                {jira_count_expr} AS jira_count,
                {hsd_count_expr} AS hsd_count
            FROM {table}
            WHERE {created_expr} >= DATE_TRUNC('year', CURRENT_DATE)
              AND {reporter_expr} IS NOT NULL
              {reporter_where}
        ), owner_counts AS (
            SELECT
                owner,
                SUM(CASE WHEN UPPER(project) = 'WIFI' THEN 1 ELSE 0 END) AS wifi_count,
                SUM(CASE WHEN UPPER(project) = 'BT' THEN 1 ELSE 0 END) AS bt_count,
                SUM(CASE WHEN UPPER(project) NOT IN ('WIFI', 'BT') THEN 1 ELSE 0 END) AS other_project_count,
                SUM(ips_count) AS ips_count,
                SUM(jira_count) AS jira_count,
                SUM(hsd_count) AS hsd_count,
                SUM(ips_count + jira_count + hsd_count) AS total,
                COUNT(DISTINCT NULLIF(customer, 'Unknown')) AS customer_count
            FROM base
            GROUP BY owner
        ), top_customers AS (
            SELECT owner, STRING_AGG(customer || ' (' || issue_count::text || ')', ', ' ORDER BY issue_count DESC, customer) AS top_customers
            FROM (
                SELECT owner, customer, COUNT(*) AS issue_count,
                       ROW_NUMBER() OVER (PARTITION BY owner ORDER BY COUNT(*) DESC, customer) AS rn
                FROM base
                WHERE customer IS NOT NULL AND customer <> 'Unknown'
                GROUP BY owner, customer
            ) ranked
            WHERE rn <= %s
            GROUP BY owner
        )
        SELECT
            oc.owner,
            oc.total,
            oc.wifi_count,
            oc.bt_count,
            oc.other_project_count,
            oc.ips_count,
            oc.jira_count,
            oc.hsd_count,
            oc.customer_count,
            COALESCE(tc.top_customers, '-') AS top_customers
        FROM owner_counts oc
        LEFT JOIN top_customers tc ON tc.owner = oc.owner
        ORDER BY oc.total DESC, oc.owner;
    """
    return db.query_rows(query, [top_customers])


def _format_table(rows: List[Dict[str, Any]]) -> str:
    headers = ["Owner", "WiFi", "BT", "Other", "IPS", "Jira", "HSD", "Customers", "Total", "Top Customers"]
    numeric = {1, 2, 3, 4, 5, 6, 7, 8}
    data = [
        [
            str(row.get("owner") or "-"),
            str(int(row.get("wifi_count") or 0)),
            str(int(row.get("bt_count") or 0)),
            str(int(row.get("other_project_count") or 0)),
            str(int(row.get("ips_count") or 0)),
            str(int(row.get("jira_count") or 0)),
            str(int(row.get("hsd_count") or 0)),
            str(int(row.get("customer_count") or 0)),
            str(int(row.get("total") or 0)),
            str(row.get("top_customers") or "-"),
        ]
        for row in rows
    ]
    widths = [len(header) for header in headers]
    for row in data:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    sep = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    lines = [sep]
    lines.append("| " + " | ".join(headers[i].rjust(widths[i]) if i in numeric else headers[i].ljust(widths[i]) for i in range(len(headers))) + " |")
    lines.append(sep)
    for row in data:
        lines.append("| " + " | ".join(row[i].rjust(widths[i]) if i in numeric else row[i].ljust(widths[i]) for i in range(len(headers))) + " |")
    lines.append(sep)
    return "\n".join(lines)


def _generate_zh_summary(rows: List[Dict[str, Any]], year: str) -> Optional[str]:
    if not rows:
        return None
    base_url = _env_str("LOCAL_LLM_URL", "http://127.0.0.1:11434")
    model = _env_str("LOCAL_LLM_MODEL", "llama3.1:8b-instruct-q4_K_M")
    total = sum(int(row.get("total") or 0) for row in rows)
    top = "; ".join(f"{row.get('owner')} {int(row.get('total') or 0)}" for row in rows[:5])
    prompt = (
        f"你是一位無線工程團隊的資料分析師。請用繁體中文，以3到5句話摘要 {year} 年度 project/customer issue loading。"
        f"資料來源只限 ips_jira_bugs DB。不要列點，直接寫成段落。\n\n"
        f"總 issue 數：{total}\n工程師數：{len(rows)}\nTop owners：{top}\n"
    )
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    try:
        req = urllib.request.Request(f"{base_url}/api/generate", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        summary = str(result.get("response") or "").strip()
        if summary:
            LOG.info("Generated Traditional Chinese DB project summary (%d chars).", len(summary))
            return summary
    except Exception as exc:
        LOG.warning("Local LLM unavailable for DB project summary: %s", exc)
    return None


def _build_body(rows: List[Dict[str, Any]], table: str, as_of: str, zh_summary: Optional[str]) -> str:
    year = as_of[:4]
    total = sum(int(row.get("total") or 0) for row in rows)
    lines: List[str] = []
    if zh_summary:
        lines.append(f"[{year} 年度 Project/Customer Issue Loading 摘要（繁體中文）]")
        lines.append(zh_summary)
        lines.append("")
        lines.append("=" * 60)
        lines.append("")

    lines.append(f"{year} Project / Customer Issue Loading per Engineer")
    lines.append("=" * 60)
    lines.append(f"Generated at : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Period       : {year}-01-01 ~ {as_of}")
    lines.append(f"Data source  : {table}")
    lines.append(f"Engineers    : {len(rows)}")
    lines.append(f"Total issues : {total}")
    lines.append("")
    lines.append(_format_table(rows) if rows else "(No data returned)")
    lines.append("")
    lines.append("Column definitions:")
    lines.append("  WiFi / BT / Other = issue count by bug_project / ips_product / jira_team")
    lines.append("  IPS / Jira / HSD  = issue count by available case identifier")
    lines.append("  Customers         = distinct customer count for that owner")
    lines.append("  Top Customers     = top customer names by issue count")
    lines.append("  Total             = all issues created this year in the DB source")
    lines.append("")
    lines.append("This email was auto-generated by run_weekly_current_yearly_issue_count.bat")
    return "\n".join(lines)


def _build_subject(rows: List[Dict[str, Any]], as_of: str) -> str:
    total = sum(int(row.get("total") or 0) for row in rows)
    return f"[{as_of[:4]} Project/Customer Loading] {as_of} | {len(rows)} engineers | {total} DB issues"


def _get_token(graph_auth_mode: str) -> Tuple[str, str]:
    secret = resolve_graph_client_secret()
    mode = (graph_auth_mode or _env_str("GRAPH_AUTH_MODE", "app")).lower()
    if mode == "delegated":
        token = get_graph_token_delegated_with_secret(secret, scopes=["Mail.Send"])
        return token, ""
    sender_upn = _env_str("GRAPH_SENDER_UPN")
    if not sender_upn:
        raise RuntimeError("GRAPH_SENDER_UPN is required for app mode.")
    token = get_graph_token_app_only(secret, scopes=["https://graph.microsoft.com/.default"])
    return token, sender_upn


def main() -> int:
    parser = argparse.ArgumentParser(description="Send weekly project/customer issue loading report from DB.")
    parser.add_argument("--table", default=_env_str("DB_TABLE", "ips_jira_bugs"))
    parser.add_argument("--recipients", default="recipients.json")
    parser.add_argument("--extra-to", default="", help="Additional comma-separated To addresses.")
    parser.add_argument("--top-customers", type=int, default=int(_env_str("PROJECT_LOADING_TOP_CUSTOMERS", "3")))
    parser.add_argument("--graph-auth-mode", choices=["app", "delegated"], default=_env_str("GRAPH_AUTH_MODE", "delegated"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    _setup_logging(args.log_level)
    table = str(args.table or "").strip()
    if not table.replace("_", "").replace(".", "").isalnum():
        LOG.error("Invalid table name: %s", table)
        return 1

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    year = as_of[:4]
    LOG.info("Querying %s project/customer loading from %s...", year, table)
    rows = _fetch_rows(DbConnector(), table, max(1, int(args.top_customers)))
    LOG.info("Found %d engineers with DB project/customer loading data.", len(rows))

    zh_summary = None if args.skip_llm else _generate_zh_summary(rows, year)
    body = _build_body(rows, table, as_of, zh_summary)
    subject = _build_subject(rows, as_of)

    to_list, cc_list = load_recipients(args.recipients)
    if args.extra_to:
        seen = {addr.lower() for addr in to_list}
        for addr in [part.strip() for part in args.extra_to.split(",") if part.strip()]:
            if addr.lower() not in seen:
                to_list.append(addr)
                seen.add(addr.lower())

    if not to_list:
        LOG.error("No recipients configured.")
        return 1

    if args.dry_run:
        print("[DRY RUN] To:", to_list)
        print("[DRY RUN] Cc:", cc_list)
        print("[DRY RUN] Subject:", subject)
        print("\n--- Body Preview ---\n")
        print(body)
        return 0

    token, sender_upn = _get_token(args.graph_auth_mode)
    import html as _html
    html_body = "<html><body><pre style='font-family: Consolas, Courier New, monospace; font-size: 13px;'>" + _html.escape(body) + "</pre></body></html>"
    send_mail_via_graph(
        token=token,
        subject=subject,
        body_text=html_body,
        to_addrs=to_list,
        cc_addrs=cc_list or None,
        save_to_sent_items=True,
        content_type="HTML",
        sender_upn=sender_upn or None,
    )
    LOG.info("Email sent to %d recipients.", len(to_list))
    if cc_list:
        LOG.info("Cc: %s", ", ".join(cc_list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())