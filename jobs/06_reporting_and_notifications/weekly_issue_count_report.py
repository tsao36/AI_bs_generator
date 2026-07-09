"""Weekly issue loading report per engineer (year-to-date).

Queries the Postgres database for total open issue counts per reporter for
the current calendar year, generates a plain-text table with a Traditional
Chinese LLM summary at the top, and sends the report to all recipients in
recipients.json.
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
    _get_reporter_current_counts as _get_counts,
    _get_reporter_current_breakdown as _get_breakdown,
    _get_table_columns as _get_cols,
    _has,
    _reporter_expr,
    _created_date_expr,
    _allowed_reporters_array_sql,
    _jira_key_expr,
)

LOG = logging.getLogger("weekly_issue_count_report")


def _get_yearly_created_counts(db: DbConnector, table: str) -> Dict[str, Dict[str, int]]:
    """Return {reporter: {ips: N, jira: N, hsd: N}} for all cases created this year (all statuses).

    'jira' counts distinct Jira-only entries: rows where a Jira key exists but
    no valid IPS case number is present (the issue originated in Jira, not IPS).
    """
    columns = _get_cols(db, table)
    created_expr = _created_date_expr(columns)
    if created_expr == "NULL":
        return {}
    allowed = _allowed_reporters_array_sql()
    reporter_filter = (
        f"AND LOWER(reporter) = ANY({allowed})" if allowed != "NULL" else ""
    )
    ips_case_expr = (
        "CASE WHEN ips_case_number::text ~ '^[0-9]+$' "
        "AND ips_case_number::int > 0 "
        "THEN ips_case_number::int ELSE NULL END"
        if _has(columns, "ips_case_number") else "NULL"
    )
    hsd_case_expr = (
        "NULLIF(NULLIF(TRIM(hsd_id::text), ''), 'NA')"
        if _has(columns, "hsd_id") else "NULL"
    )
    jira_expr = _jira_key_expr(columns)
    # Jira-only: has a valid Jira key but no valid IPS case number
    if jira_expr != "NULL" and _has(columns, "ips_case_number"):
        jira_only_expr = (
            f"CASE WHEN NOT (ips_case_number::text ~ '^[0-9]+$' "
            f"AND ips_case_number::int > 0) "
            f"THEN {jira_expr} ELSE NULL END"
        )
    elif jira_expr != "NULL":
        jira_only_expr = jira_expr
    else:
        jira_only_expr = "NULL"
    query = f"""
        SELECT {_reporter_expr()} AS reporter,
               COUNT(DISTINCT {ips_case_expr})  AS ips_count,
               COUNT(DISTINCT {jira_only_expr}) AS jira_count,
               COUNT(DISTINCT {hsd_case_expr})  AS hsd_count
        FROM {table}
        WHERE {created_expr} >= DATE_TRUNC('year', CURRENT_DATE)
          AND reporter IS NOT NULL
          {reporter_filter}
        GROUP BY reporter
    """
    try:
        rows = db.query_rows(query, None)
        return {
            (r.get("reporter") or "").strip(): {
                "ips":  int(r.get("ips_count")  or 0),
                "jira": int(r.get("jira_count") or 0),
                "hsd":  int(r.get("hsd_count")  or 0),
            }
            for r in rows
        }
    except Exception as exc:
        LOG.warning("Could not fetch yearly created counts: %s", exc)
        return {}

WIFI_GROUP = {
    "brenton wu", "kj fang", "frank lee", "frank yang",
    "charles chu", "zhiqiang cai", "timdaway lai",
}
BT_GROUP = {
    "bingyue sun", "leo chiang", "steven1 chen", "wesley kuo",
    "juan zou", "matt chen", "yu-wei chen", "brenton wu",
}

# Maps lowercase reporter name → canonical display name (merges case variants)
REPORTER_CANONICAL: Dict[str, str] = {
    "yu-wei chen": "Yu-wei Chen",
}


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s [%(levelname)s] %(message)s")
    LOG.setLevel(getattr(logging, level.upper(), logging.INFO))


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip() or default


def _normalize(name: str) -> str:
    return (name or "").strip().lower()


def _group_for(name: str) -> str:
    norm = _normalize(name)
    in_wifi = norm in WIFI_GROUP
    in_bt = norm in BT_GROUP
    if in_wifi and in_bt:
        return "WiFi/BT"
    if in_wifi:
        return "WiFi"
    if in_bt:
        return "BT"
    return "-"


def _total(r: Dict[str, Any]) -> int:
    return int(r.get("total") or r.get("total_current_issue_count") or 0)


def _name(r: Dict[str, Any]) -> str:
    return str(r.get("name") or r.get("reporter") or "")


def _format_table(rows: List[Dict[str, Any]]) -> str:
    # Columns: Reporter, Group, Jira(open), Stale IPS, Created IPS, Created JIRA, Created HSD, Total
    headers = ["Reporter", "Group", "Jira", "Stale IPS", "Created IPS", "Created JIRA", "Created HSD", "Total"]
    col_data = []
    for row in rows:
        n = _name(row)
        ips_c  = int(row.get("yearly_created") or 0)
        jira_c = int(row.get("yearly_jira") or 0)
        hsd_c  = int(row.get("yearly_hsd") or 0)
        total  = ips_c + jira_c + hsd_c
        col_data.append([
            n,
            _group_for(n),
            str(int(row.get("jira") or row.get("num_jira") or 0)),
            str(int(row.get("stale_ips") or row.get("num_stale") or 0)),
            str(ips_c),
            str(jira_c),
            str(hsd_c),
            str(total),
        ])

    widths = [len(h) for h in headers]
    for cols in col_data:
        for i, cell in enumerate(cols):
            widths[i] = max(widths[i], len(cell))

    # Columns 0=Reporter, 1=Group are text (left); 2-7 are numeric (right)
    numeric_cols = {2, 3, 4, 5, 6, 7}

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    header_line = "| " + " | ".join(
        h.rjust(widths[i]) if i in numeric_cols else h.ljust(widths[i])
        for i, h in enumerate(headers)
    ) + " |"
    lines = [sep, header_line, sep]
    for cols in col_data:
        line = "| " + " | ".join(
            cols[i].rjust(widths[i]) if i in numeric_cols else cols[i].ljust(widths[i])
            for i in range(len(headers))
        ) + " |"
        lines.append(line)
    lines.append(sep)
    return "\n".join(lines)


def _created_total(r: Dict[str, Any]) -> int:
    return (int(r.get("yearly_created") or 0)
            + int(r.get("yearly_jira")    or 0)
            + int(r.get("yearly_hsd")     or 0))


def _generate_zh_summary(rows: List[Dict[str, Any]], year: str) -> Optional[str]:
    base_url = _env_str("LOCAL_LLM_URL", "http://127.0.0.1:11434")
    model = _env_str("LOCAL_LLM_MODEL", "llama3.1:8b-instruct-q4_K_M")

    total_all = sum(_created_total(r) for r in rows)
    top3 = rows[:3]
    top3_str = "; ".join(
        f"{_name(r)} ({_created_total(r)} 筆，IPS {int(r.get('yearly_created') or 0)}"
        f" / Jira {int(r.get('yearly_jira') or 0)}"
        f" / HSD {int(r.get('yearly_hsd') or 0)})"
        for r in top3
    )
    zero_count = sum(1 for r in rows if _created_total(r) == 0)

    prompt = (
        f"你是一位無線工程團隊的問題追蹤分析師。請用繁體中文，以3到5句話，"
        f"用平易近人的語言摘要以下 {year} 年度各工程師新建案件貢獻報告。不要列點，直接寫成段落。\n\n"
        f"統計年度：{year} 年（年初至今）\n"
        f"所有工程師合計新建案件數（IPS + Jira + HSD）：{total_all} 筆\n"
        f"貢獻最高的前三名：{top3_str}\n"
        f"目前無新建案件的工程師人數：{zero_count}\n"
        f"共統計 {len(rows)} 位工程師\n"
    )

    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        summary = (result.get("response") or "").strip()
        if summary:
            LOG.info("Generated Traditional Chinese summary (%d chars).", len(summary))
            return summary
    except Exception as exc:
        LOG.warning("Local LLM unavailable, skipping summary: %s", exc)
    return None


def _generate_per_person_comments(rows: List[Dict[str, Any]], year: str) -> Dict[str, str]:
    """Call local LLM to produce a brief one-line English comment per engineer.

    Returns {name: comment_sentence}.  Empty dict if LLM is unavailable.
    """
    if not rows:
        return {}
    base_url = _env_str("LOCAL_LLM_URL", "http://127.0.0.1:11434")
    model    = _env_str("LOCAL_LLM_MODEL", "llama3.1:8b-instruct-q4_K_M")

    entries: List[str] = []
    for r in rows:
        name   = _name(r)
        ips_c  = int(r.get("yearly_created") or 0)
        jira_c = int(r.get("yearly_jira")    or 0)
        hsd_c  = int(r.get("yearly_hsd")     or 0)
        total  = ips_c + jira_c + hsd_c
        stale  = int(r.get("stale_ips") or r.get("num_stale") or 0)
        entries.append(
            f"  {name}: IPS={ips_c}, Jira={jira_c}, HSD={hsd_c}, Total={total}, StaleIPS={stale}"
        )

    data_str = "\n".join(entries)
    prompt = (
        f"You are a wireless engineering team lead reviewing {year} year-to-date issue contribution data.\n"
        f"For EACH engineer listed below, write exactly ONE concise English sentence (max 20 words) "
        f"of constructive recognition or encouragement based on their numbers.\n"
        f"Output format — one line per engineer, exactly:\n"
        f"Name: <your comment>\n\n"
        f"Columns: Created IPS, Created Jira (Jira-only), Created HSD, Total created, Stale IPS\n"
        f"{data_str}\n\n"
        f"Do not add extra lines, headers, or explanations."
    )

    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = (result.get("response") or "").strip()
        if not text:
            return {}
        comments: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if key and val:
                    comments[key] = val
        LOG.info("Generated per-person LLM comments for %d engineers.", len(comments))
        return comments
    except Exception as exc:
        LOG.warning("Local LLM unavailable for per-person comments: %s", exc)
        return {}


def _build_body(rows: List[Dict[str, Any]], table: str, as_of: str,
                zh_summary: Optional[str],
                person_comments: Optional[Dict[str, str]] = None) -> str:
    year = as_of[:4]
    lines: List[str] = []

    if zh_summary:
        lines.append(f"[{year} 年度各工程師開案負載摘要（繁體中文）]")
        lines.append(zh_summary)
        lines.append("")
        lines.append("=" * 60)
        lines.append("")

    lines.append(f"{year} Issue Loading per Engineer (Year-to-Date)")
    lines.append("=" * 60)
    lines.append(f"Generated at : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Period       : {year}-01-01 ~ {as_of}")
    lines.append(f"Data source  : {table}")
    lines.append(f"Engineers    : {len(rows)}")
    total_ips   = sum(int(r.get("yearly_created") or 0) for r in rows)
    total_jira  = sum(int(r.get("yearly_jira")    or 0) for r in rows)
    total_hsd   = sum(int(r.get("yearly_hsd")     or 0) for r in rows)
    grand_total = total_ips + total_jira + total_hsd
    lines.append(
        f"Grand total  : {grand_total} total created "
        f"(IPS {total_ips} + Jira {total_jira} + HSD {total_hsd}) in {year}"
    )
    lines.append("")

    if rows:
        lines.append(_format_table(rows))
    else:
        lines.append("(No data returned)")

    lines.append("")
    lines.append("Column definitions:")
    lines.append("  Jira         = currently open Jira issues assigned to the engineer")
    lines.append("  Stale IPS    = unpromoted IPS cases with no recent activity")
    lines.append(f"  Created IPS  = all IPS cases created in {year} (open + closed)")
    lines.append(f"  Created JIRA = Jira-only issues created in {year} (no linked IPS case)")
    lines.append(f"  Created HSD  = all HSD bugs created in {year} (open + closed)")
    lines.append("  Total        = Created IPS + Created JIRA + Created HSD")
    lines.append("")

    if person_comments:
        lines.append("=" * 60)
        lines.append("AI Feedback per Engineer  (generated by local LLM)")
        lines.append("=" * 60)
        for r in rows:
            n = _name(r)
            comment = person_comments.get(n, "")
            if comment:
                lines.append(f"  {n}: {comment}")
        lines.append("")

    lines.append("This email was auto-generated by run_weekly_current_yearly_issue_count.bat")
    return "\n".join(lines)


def _build_subject(rows: List[Dict[str, Any]], as_of: str) -> str:
    year        = as_of[:4]
    total_ips   = sum(int(r.get("yearly_created") or 0) for r in rows)
    total_jira  = sum(int(r.get("yearly_jira")    or 0) for r in rows)
    total_hsd   = sum(int(r.get("yearly_hsd")     or 0) for r in rows)
    grand_total = total_ips + total_jira + total_hsd
    return (
        f"[{year} Issue Contribution] {as_of} | "
        f"{len(rows)} engineers | {grand_total} created "
        f"(IPS {total_ips} / Jira {total_jira} / HSD {total_hsd})"
    )


def _get_token(graph_auth_mode: str) -> Tuple[str, str]:
    secret = resolve_graph_client_secret()
    mode = (graph_auth_mode or _env_str("GRAPH_AUTH_MODE", "app")).lower()
    if mode == "delegated":
        token = get_graph_token_delegated_with_secret(secret, scopes=["Mail.Send"])
        return token, ""
    sender_upn = _env_str("GRAPH_SENDER_UPN")
    if not sender_upn:
        raise RuntimeError("GRAPH_SENDER_UPN is required for app mode email sending.")
    token = get_graph_token_app_only(secret, scopes=["https://graph.microsoft.com/.default"])
    return token, sender_upn


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send year-to-date issue loading report per engineer.")
    parser.add_argument("--table", default=_env_str("DB_TABLE", "ips_jira_bugs"))
    parser.add_argument("--stale-days", type=int, default=int(_env_str("STALE_DAYS", "365")),
                        help="Days threshold for classifying stale issues (default 365 = full year).")
    parser.add_argument("--recipients", default="recipients.json")
    parser.add_argument("--extra-to", default="", help="Additional comma-separated To addresses.")
    parser.add_argument("--graph-auth-mode", choices=["app", "delegated"],
                        default=_env_str("GRAPH_AUTH_MODE", "delegated"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    _setup_logging(args.log_level)

    table = args.table.strip()
    import re as _re
    if not _re.match(r"^[a-zA-Z0-9_.]+$", table):
        LOG.error("Invalid table name: %s", table)
        return 1

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    year = as_of[:4]

    LOG.info("Querying %s issue loading from %s (stale_days=%d)...", year, table, args.stale_days)
    db = DbConnector()

    rows = _get_breakdown(db, table, args.stale_days, filter_allowed=True)
    if not rows:
        LOG.info("No breakdown data, falling back to simple counts.")
        rows = _get_counts(db, table, args.stale_days)

    LOG.info("Fetching yearly created counts (all statuses)...")
    created_counts = _get_yearly_created_counts(db, table)
    for row in rows:
        reporter = (_name(row) or "").strip()
        counts = created_counts.get(reporter, {"ips": 0, "jira": 0, "hsd": 0})
        row["yearly_created"] = counts.get("ips",  0)
        row["yearly_jira"]    = counts.get("jira", 0)
        row["yearly_hsd"]     = counts.get("hsd",  0)
    # Also include reporters who only appear in created_counts (e.g. all issues closed)
    existing = {(_name(r) or "").strip() for r in rows}
    for reporter, counts in created_counts.items():
        if reporter and reporter not in existing:
            rows.append({
                "reporter":       reporter,
                "yearly_created": counts.get("ips",  0),
                "yearly_jira":    counts.get("jira", 0),
                "yearly_hsd":     counts.get("hsd",  0),
            })

    # Merge rows that differ only in capitalisation of the reporter name.
    _merged: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        raw = (_name(row) or "").strip()
        canonical = REPORTER_CANONICAL.get(raw.lower(), raw)
        if canonical in _merged:
            base = _merged[canonical]
            for key in ("total", "total_current_issue_count",
                        "open_count", "stale_count",
                        "yearly_created", "yearly_jira", "yearly_hsd"):
                if key in row:
                    base[key] = int(base.get(key) or 0) + int(row.get(key) or 0)
        else:
            row["reporter"] = canonical
            row["name"]     = canonical
            _merged[canonical] = row
    rows = list(_merged.values())

    rows.sort(
        key=lambda r: (
            int(r.get("yearly_created") or 0)
            + int(r.get("yearly_jira")    or 0)
            + int(r.get("yearly_hsd")     or 0)
        ),
        reverse=True,
    )
    LOG.info("Found %d engineers with data.", len(rows))

    zh_summary      = _generate_zh_summary(rows, year)
    person_comments = _generate_per_person_comments(rows, year)
    body    = _build_body(rows, table, as_of, zh_summary, person_comments)
    subject = _build_subject(rows, as_of)

    to_list, cc_list = load_recipients(args.recipients)
    if args.extra_to:
        extras = [x.strip() for x in args.extra_to.split(",") if x.strip()]
        seen = {x.lower() for x in to_list}
        for addr in extras:
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
    html_body = (
        "<html><body>"
        "<pre style='font-family: Consolas, Courier New, monospace; font-size: 13px;'>"
        + _html.escape(body)
        + "</pre></body></html>"
    )
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
