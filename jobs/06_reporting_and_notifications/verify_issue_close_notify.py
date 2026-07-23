from __future__ import annotations

import argparse
import html
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Sequence, Tuple

from Meeting_agenda_OneNote import (
    get_graph_token_app_only,
    get_graph_token_delegated_with_secret,
    load_recipients,
    resolve_graph_client_secret,
    send_mail_via_graph,
)
from Wireless_bug_dashboard import DbConnector  # type: ignore

LOG = logging.getLogger("verify_issue_notify")


def _setup_logging(level: str = "INFO") -> None:
    lv = getattr(logging, str(level or "INFO").upper(), logging.INFO)
    logging.basicConfig(level=lv, format="%(asctime)s [%(levelname)s] %(message)s")
    LOG.setLevel(lv)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip() or default


def _split_table(full_name: str) -> Tuple[str, str]:
    text = (full_name or "").strip()
    if not text:
        return "public", "ips_jira_bugs"
    if "." in text:
        schema, table = text.rsplit(".", 1)
    else:
        schema, table = "public", text
    return schema.strip().lower(), table.strip().lower()


def _valid_ident(name: str) -> bool:
    return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name or ""))


def _qid(name: str) -> str:
    if not _valid_ident(name):
        raise ValueError(f"Invalid SQL identifier: {name}")
    return f'"{name}"'


def _table_columns(db: DbConnector, schema: str, table: str) -> List[str]:
    rows = db.query_rows(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return [str(r.get("column_name") or "").strip() for r in rows if str(r.get("column_name") or "").strip()]


def _pick_first(columns: Sequence[str], candidates: Sequence[str]) -> str:
    lowered = {c.lower(): c for c in columns}
    for cand in candidates:
        key = cand.lower()
        if key in lowered:
            return lowered[key]
    return ""


def _build_verify_query(schema: str, table: str, columns: Sequence[str], limit: int) -> Tuple[str, List[object], Dict[str, str]]:
    status_col = _pick_first(columns, ["jira_status", "status"])
    if not status_col:
        raise ValueError("No jira status column found (expected jira_status or status).")

    id_col = _pick_first(columns, ["jira_id", "jira_key", "issue_key", "issue_id"])
    title_col = _pick_first(columns, ["jira_title", "jira_summary", "title", "summary"])
    owner_col = _pick_first(columns, ["engineer", "reporter", "assignee", "jira_assignee"])
    updated_col = _pick_first(columns, ["jira_updated_date", "updated_date", "jira_verify_date", "jira_created_date", "ips_created_date"])

    select_parts = [
        f"TRIM(COALESCE({_qid(status_col)}::text, '')) AS jira_status",
    ]
    if id_col:
        select_parts.append(f"TRIM(COALESCE({_qid(id_col)}::text, '')) AS issue_id")
    else:
        select_parts.append("'' AS issue_id")

    if title_col:
        select_parts.append(f"TRIM(COALESCE({_qid(title_col)}::text, '')) AS issue_title")
    else:
        select_parts.append("'' AS issue_title")

    if owner_col:
        select_parts.append(f"TRIM(COALESCE({_qid(owner_col)}::text, '')) AS issue_owner")
    else:
        select_parts.append("'' AS issue_owner")

    if updated_col:
        select_parts.append(f"{_qid(updated_col)} AS issue_updated")
    else:
        select_parts.append("NULL AS issue_updated")

    where_status = f"LOWER(TRIM(COALESCE({_qid(status_col)}::text, ''))) = 'verify'"
    query = (
        f"SELECT {', '.join(select_parts)} "
        f"FROM {_qid(schema)}.{_qid(table)} "
        f"WHERE {where_status} "
        f"ORDER BY issue_updated DESC NULLS LAST, issue_id ASC "
        f"LIMIT %s"
    )

    mapping = {
        "status": status_col,
        "id": id_col,
        "title": title_col,
        "owner": owner_col,
        "updated": updated_col,
    }
    return query, [int(limit)], mapping


def _safe_text(v: object) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _build_html_body(team_name: str, rows: Sequence[dict], jira_browse_base: str) -> str:
    count = len(rows)

    zh_header = (
        "<h2 style='color:#b00020;margin-bottom:6px;'>【中文提醒（超級誠懇版）】</h2>"
        "<p style='font-size:14px;line-height:1.6;'>"
        "各位神隊友辛苦了！我們真的、真的、真的非常誠懇地提醒："
        "目前還有 Jira 狀態停在 <b>Verify</b> 的議題，"
        "如果方便的話，懇請撥冗幫忙收尾關單。"
        "<br/>"
        "是的，我們知道大家都很忙，忙到連 Verify 都捨不得關，"
        "但為了報表與流程健康，拜託拜託再拜託，幫我們把它們關起來，感激不盡！"
        "</p>"
        "<div style='margin:10px 0 14px;padding:10px 12px;border:1px solid #d9534f;border-left:4px solid #b00020;background:#fff1f1;color:#7a1f1f;border-radius:4px;font-size:14px;line-height:1.6;'>"
        "<b>重要提醒：</b>請務必在 <b>PV released</b> 之後，才關閉 Verify issue。"
        "</div>"
    )

    en_header = (
        "<h2 style='color:#0f4c81;margin-top:18px;margin-bottom:6px;'>English Reminder</h2>"
        "<p style='font-size:14px;line-height:1.6;'>"
        f"Team <b>{html.escape(team_name)}</b>, this is a reminder that there are "
        f"<b>{count}</b> Jira issue(s) currently in <b>Verify</b> status. "
        "Please review and close them when appropriate."
        "</p>"
        "<div style='margin:10px 0 14px;padding:10px 12px;border:1px solid #d9534f;border-left:4px solid #b00020;background:#fff1f1;color:#7a1f1f;border-radius:4px;font-size:14px;line-height:1.6;'>"
        "<b>Important:</b> Please close Verify issues <b>only after PV is released</b>."
        "</div>"
    )

    table_head = (
        "<table border='1' cellspacing='0' cellpadding='6' style='border-collapse:collapse;font-size:13px;'>"
        "<thead style='background:#eef5fb;'>"
        "<tr><th>#</th><th>Issue</th><th>Title</th><th>Owner</th><th>Status</th><th>Updated</th></tr>"
        "</thead><tbody>"
    )

    table_rows: List[str] = []
    for idx, row in enumerate(rows, start=1):
        issue_id = _safe_text(row.get("issue_id"))
        title = _safe_text(row.get("issue_title"))
        owner = _safe_text(row.get("issue_owner"))
        status = _safe_text(row.get("jira_status")) or "Verify"
        updated = _safe_text(row.get("issue_updated"))

        if issue_id and jira_browse_base:
            issue_cell = f"<a href='{html.escape(jira_browse_base.rstrip('/') + '/' + issue_id)}'>{html.escape(issue_id)}</a>"
        else:
            issue_cell = html.escape(issue_id or "(N/A)")

        table_rows.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td>{issue_cell}</td>"
            f"<td>{html.escape(title)}</td>"
            f"<td>{html.escape(owner)}</td>"
            f"<td>{html.escape(status)}</td>"
            f"<td>{html.escape(updated)}</td>"
            "</tr>"
        )

    table_tail = "</tbody></table>"

    footer = (
        "<p style='margin-top:14px;font-size:12px;color:#5f6b7a;'>"
        f"Generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        "</p>"
    )

    return zh_header + en_header + table_head + "".join(table_rows) + table_tail + footer


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Notify team to close Jira issues in Verify status.")
    ap.add_argument("--table", default="ips_jira_bugs", help="Table name, supports schema.table")
    ap.add_argument("--limit", type=int, default=300, help="Max number of verify issues to include")
    ap.add_argument("--team-name", default=_env("VERIFY_NOTIFY_TEAM", "Wireless Team"))
    ap.add_argument("--recipients", default=_env("VERIFY_NOTIFY_RECIPIENTS", "verify_issue_recipients.json"))
    ap.add_argument("--subject", default=_env("VERIFY_NOTIFY_SUBJECT", "[Reminder] Please close Jira Verify issues"))
    ap.add_argument("--send-email", action="store_true", help="Actually send email")
    ap.add_argument("--send-empty", action="store_true", help="Send notification even when no verify issue exists")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log-level", default=_env("VERIFY_NOTIFY_LOG_LEVEL", "INFO"))
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    _setup_logging(args.log_level)

    schema, table = _split_table(args.table)
    if not _valid_ident(schema) or not _valid_ident(table):
        raise SystemExit(f"Invalid table identifier: {args.table}")

    db = DbConnector()
    columns = _table_columns(db, schema, table)
    if not columns:
        raise SystemExit(f"Table not found or has no columns: {schema}.{table}")

    query, params, mapping = _build_verify_query(schema, table, columns, max(1, args.limit))
    rows = db.query_rows(query, params)
    LOG.info("Found %d verify issue(s) in %s.%s", len(rows), schema, table)
    LOG.info("Column mapping used: %s", mapping)

    if not rows and not args.send_empty:
        LOG.info("No verify issues found; skip sending notification.")
        return 0

    jira_browse_base = _env("JIRA_BROWSE_BASE", "https://jira.devtools.intel.com/browse")
    subject = args.subject
    if rows:
        subject = f"{subject} ({len(rows)})"

    body = _build_html_body(args.team_name, rows, jira_browse_base)

    if args.dry_run or not args.send_email:
        LOG.info("Dry-run/email disabled. Subject: %s", subject)
        preview = body[:1200]
        LOG.info("Body preview (first 1200 chars): %s", preview)
        return 0

    to_list, cc_list = load_recipients(args.recipients)
    if not to_list:
        raise SystemExit("Recipient list is empty; update recipients.json or DEFAULT_TO in .env.")

    graph_auth_mode = _env("GRAPH_AUTH_MODE", "delegated").lower()
    sender_upn = _env("GRAPH_SENDER_UPN", "")
    secret = resolve_graph_client_secret()

    if graph_auth_mode == "delegated":
        token = get_graph_token_delegated_with_secret(secret)
        send_mail_via_graph(token, subject, body, to_list, cc_list, content_type="HTML")
    else:
        if not sender_upn:
            raise SystemExit("GRAPH_SENDER_UPN is required when GRAPH_AUTH_MODE=app.")
        token = get_graph_token_app_only(secret)
        send_mail_via_graph(
            token,
            subject,
            body,
            to_list,
            cc_list,
            content_type="HTML",
            sender_upn=sender_upn,
        )

    LOG.info("Verify-status reminder sent. to=%d cc=%d issues=%d", len(to_list), len(cc_list), len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
