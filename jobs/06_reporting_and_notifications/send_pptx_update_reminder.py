from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from Meeting_agenda_OneNote import (  # type: ignore
    get_graph_token_app_only,
    get_graph_token_delegated_with_secret,
    load_recipients,
    resolve_graph_client_secret,
    send_mail_via_graph,
)


PPTX_URL = (
    "https://intel.sharepoint.com/:p:/s/intelwirelesstamteam/"
    "IQDBtFKlXhZrSaDiQCLKi-ZXARqdxKM1Iqnvcn6iT4FAUoQ?e=gM3Pm9"
)

PPTX_URL_CRITICAL = (
    "https://intel.sharepoint.com/:p:/r/sites/intelwirelesstamteam/"
    "Shared%20Documents/WHP2%20Customer%20Enablement/WhP2%20customer%20enablement%20plan/"
    "CE_Team_Activity_Critical_topics.pptx?d=wa552b4c1165e496ba0e24022ca8be657&csf=1&web=1&e=5BzdeD"
)

DEFAULT_TO_LIST = [
    "cce.wireless.ce.cfe.bt@intel.com",
    "cce.wireless.ce.cfe.wifi@intel.com",
    "cce.wireless.ce.tam@intel.com",
    "gisele.tseng@intel.com",
    "milena.chechik@intel.com",
    "sam.hsu@intel.com",
]

DEFAULT_CC_LIST = []

TEMPLATES: Dict[str, Dict[str, object]] = {
    "weekly": {
        "subject": "[Reminder] CE Team Activity Critical Topics PPTX Update / 每週更新提醒",
        "title": "Weekly Reminder: Please Update CE Team Activity Critical Topics PPTX",
        "intro": "Please update this deck every week:",
        "url": PPTX_URL,
        "closing": "Thank you / 謝謝",
        "english_rules": [
            "Need information with measurement and impact, not just status. Example: Customer XYZ First Customer Ship (FCS) changed to WW10 (was WW8) due to ball cracking issue.",
            "Spell out acronym the first time. Example: Customer Shift Left Program (CSLP), Advance Co-engineering (ACE).",
            "Use one space after punctuation (period, comma, etc.).",
            "Use capital WW for work week.",
            "Use hyphens for Power-on and Tape-out.",
            "Use active tense: \"we are finalizing xyz\" instead of \"xyz is being finalized\".",
            "Avoid using \"Engaging\". Use \"working\", \"driving to improve\", etc.",
            "Do not use dash in Platform SKUs. Example: PTL H (not PTL-H).",
        ],
        "chinese_rules": [
            "請提供有量測與影響的資訊，不要只有狀態。範例：Customer XYZ 的 First Customer Ship (FCS) 從 WW8 延到 WW10，原因是 ball cracking issue。",
            "縮寫第一次出現要先寫全名。範例：Customer Shift Left Program (CSLP)、Advance Co-engineering (ACE)。",
            "標點符號後面請保留一個空格。",
            "工作週請使用大寫 WW。",
            "Power-on、Tape-out 請使用連字號。",
            "請用主動語態：例如 \"we are finalizing xyz\"，不要用 \"xyz is being finalized\"。",
            "避免使用 \"Engaging\"，請改用 \"working\"、\"driving to improve\" 等。",
            "Platform SKU 不要用 dash。範例：請寫 PTL H，不要寫 PTL-H。",
        ],
    },
    "critical-topic": {
        "subject": "[Reminder] Please update CE critical topic and feature enablement latest status",
        "title": "Reminder: Please Update Critical Topic and Feature Enablement Latest Status",
        "intro": "Dear Team,<br/><br/>Please help update the latest status in the PowerPoint file below (critical topics and feature enablement):",
        "url": PPTX_URL_CRITICAL,
        "closing": "Thanks for your support and timely update.<br/><br/>Best regards,",
    },
    "management": {
        "subject": "[Action Required] CE status update for management review",
        "title": "Action Required: CE Critical Topic and Feature Enablement Update",
        "intro": "Please update the latest status in the management review deck before the next review cycle:",
        "url": PPTX_URL_CRITICAL,
        "closing": "Thank you for keeping the management review content current.",
    },
}


def _clean(value: object) -> str:
    return str(value or "").strip()


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


def _render_rule_list(title: str, rules: List[str]) -> str:
        if not rules:
                return ""
        items = "".join(f"<li>{rule}</li>" for rule in rules)
        return f"""
    <div class=\"section\">
        <strong>{title}</strong>
        <ul>{items}</ul>
    </div>
"""


def _build_html_body(template: Dict[str, object]) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = str(template.get("title") or "Reminder")
        intro = str(template.get("intro") or "Please review the following file:")
        url = str(template.get("url") or PPTX_URL)
        closing = str(template.get("closing") or "Thank you")
        english_rules = list(template.get("english_rules") or [])
        chinese_rules = list(template.get("chinese_rules") or [])
        english_section = _render_rule_list("English (Simple):", english_rules)
        chinese_section = _render_rule_list("中文（簡單）：", chinese_rules)

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; color: #222; line-height: 1.5; }}
    h2 {{ margin-bottom: 6px; }}
    .meta {{ color: #666; font-size: 12px; margin-bottom: 12px; }}
    .section {{ margin-top: 12px; }}
    ul {{ margin-top: 6px; }}
  </style>
</head>
<body>
    <h2>{title}</h2>
  <div class=\"meta\">Generated at: {generated_at}</div>

  <p>
        {intro}<br/>
        <a href=\"{url}\">CE_Team_Activity_Critical_topics.pptx</a>
  </p>

{english_section}
{chinese_section}

    <p>{closing}</p>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Send reminder email via Microsoft Graph with selectable templates.")
    parser.add_argument(
        "--recipients",
        default="",
        help="Optional recipients JSON path. When omitted, the script uses the built-in recipient list.",
    )
    parser.add_argument(
        "--template",
        choices=sorted(TEMPLATES.keys()),
        default="weekly",
        help="Reminder template key.",
    )
    parser.add_argument(
        "--subject",
        default="",
        help="Optional email subject override. Uses template default when omitted.",
    )
    parser.add_argument("--graph-auth-mode", choices=["app", "delegated"], default=os.getenv("GRAPH_AUTH_MODE", "delegated"))
    parser.add_argument("--dry-run", action="store_true", help="Print recipients and body length, but do not send.")
    args = parser.parse_args()

    template = TEMPLATES[args.template]
    subject = _clean(args.subject) or str(template.get("subject") or "[Reminder] CE status update")

    recipients_path = None
    if args.recipients:
        recipients_path = Path(args.recipients)
        if not recipients_path.is_absolute():
            recipients_path = Path(SCRIPT_DIR) / recipients_path
        to_list, cc_list = load_recipients(str(recipients_path))
    else:
        to_list, cc_list = list(DEFAULT_TO_LIST), list(DEFAULT_CC_LIST)

    if not to_list:
        if recipients_path is not None:
            raise RuntimeError(f"Recipient list is empty. Check: {recipients_path}")
        raise RuntimeError("Built-in recipient list is empty.")

    body_html = _build_html_body(template)

    if args.dry_run:
        print("[DRY-RUN] Reminder email not sent.")
        print(f"[DRY-RUN] Template: {args.template}")
        if recipients_path is not None:
            print(f"[DRY-RUN] Recipients file: {recipients_path}")
        else:
            print("[DRY-RUN] Recipients source: built-in list")
        print(f"[DRY-RUN] To: {', '.join(to_list)}")
        if cc_list:
            print(f"[DRY-RUN] Cc: {', '.join(cc_list)}")
        print(f"[DRY-RUN] Subject: {subject}")
        print(f"[DRY-RUN] HTML length: {len(body_html)}")
        return 0

    token, sender_upn = _get_token(args.graph_auth_mode)
    send_mail_via_graph(
        token,
        subject,
        body_html,
        to_list,
        cc_list,
        content_type="HTML",
        sender_upn=(sender_upn or None),
    )

    print(f"[OK] Reminder email sent to: {', '.join(to_list)}")
    if cc_list:
        print(f"[OK] Cc: {', '.join(cc_list)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
