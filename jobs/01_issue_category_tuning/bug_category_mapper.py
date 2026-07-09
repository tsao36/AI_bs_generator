"""Utility to classify IPS issues into categories using an LLM.

Steps:
1. Pull IPS issues from PostgreSQL.
2. Ask an LLM to map each issue (`ips_title` + optional description clue) to a curated bug category list.
3. Let the user choose the time window ("issues from the past X days") via a simple menu.
4. Show a table that pairs every issue with the predicted category for review.

Env requirements align with Bug_Dashboard_checker.py (.env in repo root).
Set EXPERTGPT_TOKEN (and optionally EXPERTGPT_URL/EXPERTGPT_MODEL) for the LLM call.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

import httpx
import openai


# -------------------------
# Shared helpers
# -------------------------
def _base_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"), override=False)

def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip() or default


conn_params = {
    "database": (os.getenv("DB_NAME") or "").strip(),
    "user": (os.getenv("DB_USER") or "").strip(),
    "password": (os.getenv("DB_PASS") or "").strip(),
    "host": (os.getenv("DB_HOST") or "").strip(),
    "port": (os.getenv("DB_PORT") or "5433").strip(),
}

DEFAULT_CATEGORIES_FILE = os.path.join(BASE_DIR, "bug_category_config.json")
DEFAULT_PROMPT_FILE = os.path.join(BASE_DIR, "bug_category_system_prompt.json")
DEFAULT_MENU_CHOICES = [1, 3, 7, 14, 30, 90]
DEFAULT_SYSTEM_PROMPT = (
    "You are an IPS bug triage assistant. Assign each bug title to the closest "
    "category from the provided list and respond only with JSON containing "
    "category, confidence (0-1), and reasoning."
)
FALLBACK_CATEGORY = "Need-Triage"
_CATEGORY_ALIASES: Dict[str, str] = {
    "need-triage": "Need-Triage",
    "needs-triage": "Need-Triage",
    "unknown": "Need-Triage",
    "not-wireless": "Need-Triage",
    "not wireless": "Need-Triage",
    "not wifi issue": "Need-Triage",
    "not bt issue": "Need-Triage",
    "icps": "ICPS/Killer",
    "killer": "ICPS/Killer",
    "icps/killer": "ICPS/Killer",
}
_TITLE_KEY = "ips_title"
_DESCRIPTION_KEY = "ips_description"
DEFAULT_TABLE = env_str("DB_TABLE", "vw_issues")
DEFAULT_MODEL = ""
DEFAULT_FALLBACK_MODEL = ""
DEFAULT_API_KEY = env_str("EXPERTGPT_TOKEN", "")
DEFAULT_BASE_URL = env_str("EXPERTGPT_URL", "https://expertgpt.intel.com")
EXPERTGPT_HOST = "https://expertgpt.intel.com"
EXPERTGPT_CA_BUNDLE = env_str("EXPERTGPT_CA_BUNDLE", "")
EXPERTGPT_INSECURE = env_str("EXPERTGPT_INSECURE", "").lower() in {"1", "true", "yes"}

LOCAL_LLM_BASE_URL = env_str("LOCAL_LLM_URL", "http://127.0.0.1:11434/v1")
LOCAL_LLM_MODEL = env_str("LOCAL_LLM_MODEL", "llama3.1:8b-instruct-q4_K_M")
LOCAL_LLM_API_KEY = env_str("LOCAL_LLM_API_KEY", "ollama")
LOCAL_LLM_ENABLED = env_str("LOCAL_LLM_ENABLED", "1").lower() not in {"0", "false", "no"}

BUG_PROJECT_TO_CUSTOM = {
    "wifi": "WiFi",
    "bt": "Bluetooth",
    "cie": "ICPS/Killer",
    "wot": "Tools",
    "dbgt": "WCS Validation tool",
}

TECHNOLOGY_NORMALIZATION = {
    "wifi": "wifi",
    "wi-fi": "wifi",
    "bluetooth": "bt",
    "bt": "bt",
    "icps/killer": "software",
    "icps": "software",
    "killer": "software",
    "tools": "tools",
    "wcs validation tool": "tools",
}

CASE_NUMBER_COLUMNS = [
    "ips_case_number",
    "ips_case_id",
    "ips_id",
    "case_number",
]

# Rule-engine state populated from category config
RULE_CUES: Dict[str, List[str]] = {}
RULE_OVERRIDES: List[Dict[str, Any]] = []
RULE_PRECEDENCE: List[str] = []


@dataclass
class IssueRecord:
    issue_key: str
    ips_case_number: str
    title: str
    description: str
    jira_title: str
    created_date: str
    bug_project: str
    bug_category_custom: str


@dataclass
class ClassifiedIssue(IssueRecord):
    category: str
    confidence: float
    reasoning: str
    raw_response: str
    technology_display: str
    technology_key: str


@dataclass
class LLMConnection:
    client: openai.OpenAI
    http_client: Optional[httpx.Client] = None

    def close(self) -> None:
        if self.http_client is not None:
            try:
                self.http_client.close()
            except Exception:
                pass


def _normalize_category_list(values: List[str]) -> List[str]:
    seen = set()
    normalized: List[str] = []
    for value in values:
        text = _canonicalize_category(str(value or "").strip())
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    if FALLBACK_CATEGORY.lower() not in seen:
        normalized.append(FALLBACK_CATEGORY)
    return normalized


def categories_for_project(project: str, mapping: Dict[str, List[str]], default_categories: List[str]) -> List[str]:
    key = (project or "").strip().lower()
    return mapping.get(key, default_categories)


def _canonicalize_category(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _CATEGORY_ALIASES.get(text.lower(), text)


def _progress_tracker(total: int, label: str = "Processing"):
    total = max(0, total)
    if total == 0:
        return lambda current: None

    def _report(current: int) -> None:
        current = min(max(0, current), total)
        pct = (current / total) * 100 if total else 0
        sys.stdout.write(f"\r[{label}] {current}/{total} ({pct:5.1f}%)")
        sys.stdout.flush()
        if current >= total:
            sys.stdout.write("\n")

    return _report


def _format_elapsed(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.2f} s"
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {rem:.1f}s"
    hours, rem = divmod(minutes, 60)
    return f"{int(hours)}h {int(rem)}m"


def record_llm_elapsed(total_seconds: float, calls: int, log: logging.Logger) -> None:
    if calls <= 0:
        log.info("LLM total time: 0 calls.")
        return
    avg = total_seconds / calls
    log.info(
        "LLM total time: %s across %d call(s) (avg %s per call).",
        _format_elapsed(total_seconds),
        calls,
        _format_elapsed(avg),
    )


def _strip_bracket_prefix(text: str) -> str:
    """Return substring after the final closing bracket, if any."""
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    idx = cleaned.rfind("]")
    if idx == -1:
        return cleaned
    suffix = cleaned[idx + 1 :].strip()
    return suffix or cleaned


def _clean_text_value(value: Any) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    if cleaned.upper() == "NA":
        return ""
    return cleaned


def setup_logging(level: str) -> logging.Logger:
    logger = logging.getLogger("bug_category_mapper")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger

    lvl = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handler.setLevel(lvl)
    logger.addHandler(handler)
    return logger


def enforce_expertgpt_base_url(_: str, log: logging.Logger) -> str:
    normalized = EXPERTGPT_HOST.rstrip("/") + "/v1"
    log.info("Using ExpertGPT endpoint %s", normalized)
    return normalized


def validate_table_name(table_name: str) -> None:
    if not re.match(r"^[a-zA-Z0-9_.]+$", table_name):
        raise ValueError(f"Invalid table name: {table_name}")


def _split_table_identifier(table_name: str) -> Tuple[str, str]:
    if "." in table_name:
        schema, table = table_name.rsplit(".", 1)
    else:
        schema, table = "public", table_name
    schema = (schema or "public").strip().lower()
    table = (table or "").strip().lower()
    return schema or "public", table


def _column_exists(conn: Any, schema: str, table: str, column: str) -> bool:
    check_sql = """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
          AND column_name = %s
        LIMIT 1;
    """
    with conn.cursor() as cur:
        cur.execute(check_sql, (schema, table, column))
        return cur.fetchone() is not None


def resolve_technology_context(issue: IssueRecord) -> Tuple[str, str]:
    """Return (display_label, lookup_key) for technology-specific category mapping."""
    project_raw = (issue.bug_project or "").strip()
    custom = (issue.bug_category_custom or "").strip()
    if not custom:
        mapped = BUG_PROJECT_TO_CUSTOM.get(project_raw.lower(), project_raw)
        custom = mapped.strip() if isinstance(mapped, str) else project_raw
    display = custom or project_raw or "(default)"
    lookup_key = TECHNOLOGY_NORMALIZATION.get(display.lower(), display.lower())
    if not lookup_key:
        lookup_key = ""
    return display, lookup_key


def _resolve_created_date_column(conn: Any, table_name: str, log: logging.Logger) -> str:
    schema, table = _split_table_identifier(table_name)
    candidates = ["bug_created_date", "ips_created_date"]
    for column in candidates:
        if _column_exists(conn, schema, table, column):
            if column != "bug_created_date":
                log.warning(
                    "Column bug_created_date not found on %s; falling back to %s.",
                    table_name,
                    column,
                )
            return column
    raise SystemExit(
        f"None of the expected created-date columns ({', '.join(candidates)}) exist on {table_name}."
    )


def _resolve_case_number_expression(conn: Any, table_name: str, log: logging.Logger) -> Tuple[str, List[str]]:
    schema, table = _split_table_identifier(table_name)
    available = [col for col in CASE_NUMBER_COLUMNS if _column_exists(conn, schema, table, col)]
    if not available:
        log.warning(
            "No IPS case number columns found on %s; expected one of %s.",
            table_name,
            ", ".join(CASE_NUMBER_COLUMNS),
        )
        return "'' AS ips_case_number", []

    parts = [f"NULLIF(TRIM({col}::text), '')" for col in available]
    expr = "COALESCE(" + ", ".join(parts) + ", '') AS ips_case_number"
    return expr, available


def _resolve_title_expressions(conn: Any, table_name: str) -> tuple[str, str, str]:
    """Return SELECT expressions for chosen title (prefer JIRA) and raw jira_title/ips_title."""
    schema, table = _split_table_identifier(table_name)
    has_jira_title = _column_exists(conn, schema, table, "jira_title")
    jira_expr = "NULLIF(NULLIF(TRIM(jira_title), ''), 'NA')" if has_jira_title else "NULL"
    title_expr = (
        "COALESCE("
        f"{jira_expr},"
        "NULLIF(NULLIF(TRIM({title_col}), ''), 'NA'),"
        "'(no title)'"
        ")::text AS title"
    ).format(title_col=_TITLE_KEY)
    jira_title_select = (
        "COALESCE(NULLIF(TRIM(jira_title)::text, 'NA'), '') AS jira_title"
        if has_jira_title
        else "'' AS jira_title"
    )
    ips_title_select = f"COALESCE(NULLIF(TRIM({_TITLE_KEY})::text, 'NA'), '') AS ips_title_raw"
    return title_expr, jira_title_select, ips_title_select


def _resolve_description_expression(conn: Any, table_name: str) -> str:
    schema, table = _split_table_identifier(table_name)
    for col in [_DESCRIPTION_KEY, "jira_description", "description", "ips_desc"]:
        if _column_exists(conn, schema, table, col):
            return f"COALESCE(NULLIF(TRIM({col}::text), 'NA'), '') AS ips_description"
    return "'' AS ips_description"


def _resolve_issue_key_expression(conn: Any, table_name: str, log: logging.Logger) -> str:
    """Return a COALESCE expression for issue key using available identifier columns."""
    schema, table = _split_table_identifier(table_name)
    candidates = ["jira_id", "ips_jira_id"] + CASE_NUMBER_COLUMNS
    available: List[str] = [c for c in candidates if _column_exists(conn, schema, table, c)]
    if not available:
        raise SystemExit(
            "None of the expected identifier columns exist on the source table/view; cannot build issue_key."
        )

    if "jira_id" not in available and "ips_jira_id" not in available:
        log.warning(
            "No jira_id/ips_jira_id columns on %s; using IPS case columns for issue_key.",
            table_name,
        )
    expr_parts = [f"NULLIF(NULLIF(TRIM({col}::text), ''), 'NA')" for col in available]
    return "COALESCE(" + ", ".join(expr_parts) + ", '') AS issue_key"


def _default_matrix_payload() -> Dict[str, Any]:
    entries = [
        ("Audio", "BT"),
        ("BIOS", "BT"),
        ("BSOD", "BT"),
        ("BSOD", "WiFi"),
        ("Connectivity", "BT"),
        ("Connectivity", "WiFi"),
        ("HLK", "BT"),
        ("HLK", "WiFi"),
        ("ICPS/Killer", "Software"),
        ("IOP", "BT"),
        ("MSFT", "BT"),
        ("P2P", "WiFi"),
        ("OEM Tools", "Tools"),
        ("Performance", "BT"),
        ("Performance", "WiFi"),
        ("Power Consumption", "BT"),
        ("Power Consumption", "WiFi"),
        ("Power on sequence", "Product"),
        ("Roaming", "WiFi"),
        ("Sensing", "WiFi"),
        ("System Hang", "WiFi"),
        ("UEFI", "BT"),
        ("UEFI", "WiFi"),
        ("WAPI", "WiFi"),
        ("WowLAN", "WiFi"),
        ("YB/Lost", "BT"),
        ("YB/Lost", "WiFi"),
    ]
    return {
        "default_categories": [
            "Audio",
            "BIOS",
            "BSOD",
            "Connectivity",
            "HLK",
            "ICPS/Killer",
            "IOP",
            "MSFT",
            "P2P",
            "OEM Tools",
            "Performance",
            "Power Consumption",
            "Power on sequence",
            "Roaming",
            "Sensing",
            "System Hang",
            "UEFI",
            "WAPI",
            "WowLAN",
            "YB/Lost",
            FALLBACK_CATEGORY,
        ],
        "category_matrix": [
            {"issue_type": issue, "technology": tech}
            for issue, tech in entries
        ],
    }


def _load_system_prompt(prompt_path: str, log: logging.Logger, fallback: Any = None) -> str:
    cfg_path = prompt_path or DEFAULT_PROMPT_FILE
    if not os.path.isabs(cfg_path):
        cfg_path = os.path.join(BASE_DIR, cfg_path)
    data: Any = None
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    elif fallback is not None:
        data = fallback

    if isinstance(data, dict):
        data = data.get("system_prompt")

    if isinstance(data, list):
        lines = [str(entry or "").strip() for entry in data]
        prompt = "\n".join(line for line in lines if line)
    else:
        prompt = str(data or "").strip()

    if not prompt:
        prompt = DEFAULT_SYSTEM_PROMPT
        log.warning("System prompt missing; using default prompt.")
    return prompt


def _build_system_prompt_from_config(data: Any, log: logging.Logger) -> str:
    if not isinstance(data, dict):
        return ""

    precedence = data.get("precedence") or []
    cues = data.get("category_cues") or {}
    overrides = data.get("hard_overrides") or []

    if not precedence or not isinstance(precedence, list):
        return ""
    if not cues or not isinstance(cues, dict):
        return ""

    def _clean_lines(lines: list[str]) -> list[str]:
        return [str(x or "").strip() for x in lines if str(x or "").strip()]

    lines: list[str] = [
        "You are an IPS bug triage assistant.",
        "Given one issue title, classify it deterministically into one default category.",
        "",
        "GLOBAL PRECEDENCE (highest wins):",
    ]

    for idx, cat in enumerate(precedence, start=1):
        cat_clean = str(cat or "").strip()
        if cat_clean:
            lines.append(f"{idx}. {cat_clean}")

    lines += [
        "",
        "PROCESS:",
        "1) Normalize title to lowercase.",
        "2) Score each category by counting matching cues.",
        "3) Apply precedence if multiple categories match.",
        "",
    ]

    # Hard overrides (optional)
    overrides = [o for o in overrides if isinstance(o, dict)]
    if overrides:
        lines.append("HARD OVERRIDES (scoped):")
        for ovr in overrides:
            phrase = str(ovr.get("phrase") or "").strip()
            category = str(ovr.get("category") or "").strip()
            unless = [str(x or "").strip() for x in (ovr.get("unless") or []) if str(x or "").strip()]
            if not phrase or not category:
                continue
            if unless:
                lines.append(f"- '{phrase}' -> {category} ONLY if none of {', '.join(unless)} cues match.")
            else:
                lines.append(f"- '{phrase}' -> {category}.")
        lines.append("")

    # Cues per category
    lines.append("CATEGORY CUES:")
    lines.append("")
    for cat, cue_list in cues.items():
        cat_clean = str(cat or "").strip()
        if not cat_clean:
            continue
        cue_lines = _clean_lines(cue_list if isinstance(cue_list, list) else [])
        lines.append(f"{cat_clean}:")
        if cue_lines:
            lines.append(", ".join(cue_lines))
        lines.append("")

    lines.append("DEFAULT:")
    lines.append(f"- If no category wins -> {FALLBACK_CATEGORY}")
    lines.append(f"- Never return {FALLBACK_CATEGORY} if any cue or override matched.")
    lines.append("")
    lines.append("OUTPUT:")
    lines.append("- JSON only")
    lines.append("- Keys: category, confidence (0-1), reasoning")

    prompt = "\n".join(_clean_lines(lines))
    if not prompt:
        log.warning("Failed to build system prompt from config; falling back to defaults.")
    return prompt


def ensure_categories(path: str, log: logging.Logger) -> Tuple[Dict[str, List[str]], List[str], str, str]:
    cfg_path = path or DEFAULT_CATEGORIES_FILE
    if not os.path.isabs(cfg_path):
        cfg_path = os.path.join(BASE_DIR, cfg_path)

    if not os.path.exists(cfg_path):
        default_payload = _default_matrix_payload()
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(default_payload, fh, indent=2)
        log.info("Created default category config at %s", cfg_path)

    with open(cfg_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    sys_prompt = _build_system_prompt_from_config(data, log)

    # Capture rule config for deterministic pass
    global RULE_CUES, RULE_OVERRIDES, RULE_PRECEDENCE
    RULE_CUES = data.get("category_cues", {}) if isinstance(data.get("category_cues"), dict) else {}
    RULE_OVERRIDES = data.get("hard_overrides", []) if isinstance(data.get("hard_overrides"), list) else []
    RULE_PRECEDENCE = data.get("precedence", []) if isinstance(data.get("precedence"), list) else []
    if not sys_prompt:
        sys_prompt = _load_system_prompt(DEFAULT_PROMPT_FILE, log, fallback=data)

    default_list: List[str] = data.get("default_categories", []) if isinstance(data.get("default_categories"), list) else []

    categories_by_project: Dict[str, List[str]] = {}

    matrix = data.get("category_matrix")
    if isinstance(matrix, list):
        for entry in matrix:
            if not isinstance(entry, dict):
                continue
            issue = _canonicalize_category(str(entry.get("issue_type") or entry.get("category") or "").strip())
            tech = str(entry.get("technology") or entry.get("project") or "").strip()
            if not issue or not tech:
                continue
            key = tech.lower()
            categories_by_project.setdefault(key, [])
            categories_by_project[key].append(issue)
        for key, raw_list in categories_by_project.items():
            categories_by_project[key] = _normalize_category_list(raw_list)
        if not default_list:
            seen = set()
            derived: List[str] = []
            for lst in categories_by_project.values():
                for val in lst:
                    low = _canonicalize_category(val).lower()
                    if low == FALLBACK_CATEGORY.lower() or low in seen:
                        continue
                    seen.add(low)
                    derived.append(_canonicalize_category(val))
            default_list = derived

    elif isinstance(data.get("technologies"), dict):
        for name, raw_list in data["technologies"].items():
            if not isinstance(raw_list, list):
                continue
            normalized = _normalize_category_list(raw_list)
            if not normalized:
                continue
            key = str(name or "").strip().lower()
            if key:
                categories_by_project[key] = normalized
        if not default_list:
            default_list = data.get("categories", []) or []

    elif isinstance(data.get("categories"), list):
        default_list = data.get("categories", [])

    if not default_list:
        default_list = [FALLBACK_CATEGORY]

    default_categories = _normalize_category_list(default_list)
    if not categories_by_project:
        log.warning("No technology-specific category lists found; falling back to default list.")

    return categories_by_project, default_categories, sys_prompt, cfg_path


def prompt_days_interactively() -> int:
    print("Select how far back to pull issues:")
    for idx, days in enumerate(DEFAULT_MENU_CHOICES, start=1):
        print(f"  {idx}) Last {days} day(s)")
    print(f"  {len(DEFAULT_MENU_CHOICES) + 1}) Custom value")

    while True:
        choice = input("Enter selection: ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(DEFAULT_MENU_CHOICES):
                return DEFAULT_MENU_CHOICES[idx - 1]
            if idx == len(DEFAULT_MENU_CHOICES) + 1:
                custom = input("Number of days (positive integer): ").strip()
                if custom.isdigit() and int(custom) > 0:
                    return int(custom)
                print("Invalid custom value. Try again.")
                continue
        print("Invalid selection. Try again.")


def fetch_issues(table_name: str, days_back: int, limit: int, log: logging.Logger) -> List[IssueRecord]:
    missing = [k for k, v in conn_params.items() if not v]
    if missing:
        raise SystemExit(f"Missing DB params in .env: {missing}")

    validate_table_name(table_name)

    params: List[Any] = [days_back]
    limit_clause = ""
    if limit and limit > 0:
        limit_clause = "LIMIT %s"
        params.append(limit)

    with psycopg2.connect(**conn_params) as conn:
        created_column = _resolve_created_date_column(conn, table_name, log)
        issue_key_expr = _resolve_issue_key_expression(conn, table_name, log)
        schema, table = _split_table_identifier(table_name)
        has_custom = _column_exists(conn, schema, table, "bug_category_custom")
        case_expr, _ = _resolve_case_number_expression(conn, table_name, log)
        if not has_custom:
            log.warning(
                "Column bug_category_custom not found on %s; technology context will rely on bug_project mapping only.",
                table_name,
            )
        custom_select = (
            "COALESCE(bug_category_custom::text, '') AS bug_category_custom"
            if has_custom
            else "'' AS bug_category_custom"
        )
        title_select, jira_title_select, ips_title_select = _resolve_title_expressions(conn, table_name)
        description_select = _resolve_description_expression(conn, table_name)
        query = f"""
            SELECT
                {issue_key_expr},
                {case_expr},
                {title_select},
                {jira_title_select},
                {ips_title_select},
            {description_select},
                COALESCE({created_column}::date::text, '') AS created_date,
                COALESCE(bug_project, '')::text AS bug_project,
                {custom_select}
            FROM {table_name}
                        WHERE (
                                {created_column}::date >= CURRENT_DATE - %s * INTERVAL '1 day'
                                AND {created_column}::date <= CURRENT_DATE
                        )
                             OR {created_column} IS NULL
            ORDER BY {created_column} DESC
            {limit_clause};
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    log.info("Fetched %d issues from %s (last %d day(s)).", len(rows), table_name, days_back)

    results: List[IssueRecord] = []
    for row in rows:
        if not row.get("issue_key"):
            continue
        raw_title = _strip_bracket_prefix(_clean_text_value(row.get("title"))).strip()
        if raw_title == "(no title)":
            raw_title = ""
        raw_ips_title = _strip_bracket_prefix(_clean_text_value(row.get("ips_title_raw"))).strip()
        title = raw_title or raw_ips_title or ""
        if not title:
            fallback = (
                _clean_text_value(row.get("jira_title"))
                or _clean_text_value(row.get("ips_case_number"))
                or _clean_text_value(row.get("issue_key"))
            )
            title = fallback or "(no title)"
        clean_jira_title = _strip_bracket_prefix(_clean_text_value(row.get("jira_title"))).strip()
        clean_description = _clean_text_value(row.get("ips_description")).strip()
        results.append(
            IssueRecord(
                issue_key=(row.get("issue_key") or "").strip(),
                ips_case_number=(row.get("ips_case_number") or "").strip(),
                title=title,
            description=clean_description,
                jira_title=clean_jira_title,
                created_date=(row.get("created_date") or "").strip(),
                bug_project=(row.get("bug_project") or "").strip(),
                bug_category_custom=(row.get("bug_category_custom") or "").strip(),
            )
        )
    return results


def fetch_issues_by_case_numbers(
    table_name: str,
    case_numbers: List[str],
    limit: int,
    log: logging.Logger,
) -> List[IssueRecord]:
    missing = [k for k, v in conn_params.items() if not v]
    if missing:
        raise SystemExit(f"Missing DB params in .env: {missing}")

    validate_table_name(table_name)

    cleaned = [str(val).strip() for val in case_numbers if str(val).strip()]
    if not cleaned:
        return []

    normalized = {
        (val.lstrip("0") or "0")
        for val in cleaned
        if val.isdigit()
    }
    normalized_list = sorted(normalized)

    params: List[Any] = [cleaned]
    if normalized_list:
        params.append(normalized_list)
    limit_clause = ""
    if limit and limit > 0:
        limit_clause = "LIMIT %s"
        params.append(limit)

    with psycopg2.connect(**conn_params) as conn:
        created_column = _resolve_created_date_column(conn, table_name, log)
        issue_key_expr = _resolve_issue_key_expression(conn, table_name, log)
        schema, table = _split_table_identifier(table_name)
        has_custom = _column_exists(conn, schema, table, "bug_category_custom")
        case_expr, case_columns = _resolve_case_number_expression(conn, table_name, log)
        if not has_custom:
            log.warning(
                "Column bug_category_custom not found on %s; technology context will rely on bug_project mapping only.",
                table_name,
            )
        custom_select = (
            "COALESCE(bug_category_custom::text, '') AS bug_category_custom"
            if has_custom
            else "'' AS bug_category_custom"
        )
        title_select, jira_title_select, ips_title_select = _resolve_title_expressions(conn, table_name)
        description_select = _resolve_description_expression(conn, table_name)
        predicates: List[str] = []
        for col in case_columns:
            base = f"COALESCE({col}::text, '')"
            predicates.append(f"{base} = ANY(%s)")
            if normalized_list:
                predicates.append(f"ltrim({base}, '0') = ANY(%s)")
        where_clause = " OR ".join(predicates) if predicates else "FALSE"
        query = f"""
            SELECT
                {issue_key_expr},
                {case_expr},
                {title_select},
                {jira_title_select},
                {ips_title_select},
                {description_select},
                COALESCE({created_column}::date::text, '') AS created_date,
                COALESCE(bug_project, '')::text AS bug_project,
                {custom_select}
            FROM {table_name}
                        WHERE {where_clause}
            ORDER BY {created_column} DESC
            {limit_clause};
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    log.info(
        "Fetched %d issue(s) from %s for %d IPS case number(s).",
        len(rows),
        table_name,
        len(cleaned),
    )

    results: List[IssueRecord] = []
    for row in rows:
        raw_title = _strip_bracket_prefix(_clean_text_value(row.get("title"))).strip()
        if raw_title == "(no title)":
            raw_title = ""
        raw_ips_title = _strip_bracket_prefix(_clean_text_value(row.get("ips_title_raw"))).strip()
        title = raw_title or raw_ips_title or ""
        if not title:
            fallback = (
                _clean_text_value(row.get("jira_title"))
                or _clean_text_value(row.get("ips_case_number"))
                or _clean_text_value(row.get("issue_key"))
            )
            title = fallback or "(no title)"
        clean_jira_title = _strip_bracket_prefix(_clean_text_value(row.get("jira_title"))).strip()
        clean_description = _clean_text_value(row.get("ips_description")).strip()
        results.append(
            IssueRecord(
                issue_key=(row.get("issue_key") or "").strip(),
                ips_case_number=(row.get("ips_case_number") or "").strip(),
                title=title,
            description=clean_description,
                jira_title=clean_jira_title,
                created_date=(row.get("created_date") or "").strip(),
                bug_project=(row.get("bug_project") or "").strip(),
                bug_category_custom=(row.get("bug_category_custom") or "").strip(),
            )
        )
    return results


def ensure_openai_client(
    api_key: str,
    base_url: str,
    *,
    require_api_key: bool = True,
    allow_insecure: bool = False,
) -> LLMConnection:
    if not hasattr(openai, "OpenAI"):
        raise SystemExit("The openai package is not installed. Add it to requirements and pip install.")
    if require_api_key and not api_key:
        raise SystemExit("EXPERTGPT_TOKEN is missing. Set it in .env or pass --api-key.")

    token = api_key or "local-placeholder-token"
    client_kwargs: Dict[str, Any] = {"api_key": token}
    http_client: Optional[httpx.Client] = None
    if base_url:
        client_kwargs["base_url"] = base_url
        verify: Any = True
        logger = logging.getLogger("bug_category_mapper")
        if allow_insecure:
            verify = False
        elif EXPERTGPT_INSECURE:
            verify = False
            logger.warning("EXPERTGPT_INSECURE is set; TLS verification is disabled.")
        elif EXPERTGPT_CA_BUNDLE:
            if not os.path.exists(EXPERTGPT_CA_BUNDLE):
                raise SystemExit(
                    f"EXPERTGPT_CA_BUNDLE not found: {EXPERTGPT_CA_BUNDLE}"
                )
            verify = EXPERTGPT_CA_BUNDLE
        http_client = httpx.Client(proxy=None, verify=verify, trust_env=False)
        client_kwargs["http_client"] = http_client

    client = openai.OpenAI(**client_kwargs)
    return LLMConnection(client=client, http_client=http_client)


def try_local_llm(base_url: str, model: str, log: logging.Logger) -> Tuple[Optional[LLMConnection], Optional[str]]:
    if not base_url or not model:
        return None, None

    api_key = LOCAL_LLM_API_KEY or "ollama"
    log.info("Attempting to use local LLM at %s (model %s).", base_url, model)

    conn: Optional[LLMConnection] = None
    try:
        conn = ensure_openai_client(
            api_key=api_key,
            base_url=base_url,
            require_api_key=False,
            allow_insecure=True,
        )
        try:
            model_list = conn.client.models.list()
            ids = [
                m.id for m in getattr(model_list, "data", []) if getattr(m, "id", None)
            ]
            if ids and model not in ids:
                log.warning(
                    "Local LLM reachable but model %s not in advertised list (available: %s).",
                    model,
                    ", ".join(ids),
                )
        except Exception as exc:  # pragma: no cover - diagnostic only
            api_conn_error = getattr(openai, "APIConnectionError", None)
            if api_conn_error and isinstance(exc, api_conn_error):
                raise RuntimeError(f"Unable to reach local LLM at {base_url}") from exc
            log.debug("Local LLM /models probe failed: %s", exc)

        log.info("Using local LLM backend (see test_local_llm.py).")
        return conn, model
    except Exception as exc:
        if conn is not None:
            conn.close()
        log.warning("Local LLM unavailable (%s); falling back to ExpertGPT.", exc)
        return None, None


def resolve_model_name(
    client: openai.OpenAI,
    preferred: str,
    fallback: str,
    log: logging.Logger,
) -> str:
    try:
        model_list = client.models.list()
        ids = [m.id for m in getattr(model_list, "data", []) if getattr(m, "id", None)]
    except Exception as exc:
        log.warning("Unable to list models: %s", exc)
        return preferred
    if preferred in ids:
        return preferred
    if fallback in ids:
        log.warning("Preferred model %s not found; using %s.", preferred, fallback)
        return fallback
    if ids:
        log.warning("Model %s not found; using %s.", preferred, ids[0])
        return ids[0]
    return preferred


def safe_parse_json(payload: str) -> Dict[str, Any]:
    payload = payload.strip()
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(payload[start: end + 1])
            except json.JSONDecodeError:
                return {}
        return {}


def classify_title(
    client: openai.OpenAI,
    model: str,
    fallback_model: str,
    system_prompt: str,
    temperature: float,
    title: str,
    description: str,
    categories: List[str],
    technology_label: str,
    config_label: str,
    log: logging.Logger,
) -> Tuple[str, float, str, str]:
    if not categories:
        categories = [FALLBACK_CATEGORY]
    user_prompt = _build_user_prompt(
        title=title,
        description=description,
        categories=categories,
        technology_label=technology_label,
        config_label=config_label,
    )

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except openai.NotFoundError as exc:
        exc_text = str(exc)
        should_retry = "DeploymentNotFound" in exc_text and fallback_model and fallback_model != model
        if not should_retry:
            log.error("LLM request failed: %s", exc_text)
            return FALLBACK_CATEGORY, 0.0, f"LLM error: {exc_text}", exc_text
        log.warning("Model %s not found; retrying with %s.", model, fallback_model)
        try:
            response = client.chat.completions.create(
                model=fallback_model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except openai.OpenAIError as exc2:
            exc_text = str(exc2)
            log.error("LLM request failed after retry: %s", exc_text)
            return FALLBACK_CATEGORY, 0.0, f"LLM error: {exc_text}", exc_text
    except openai.OpenAIError as exc:
        exc_text = str(exc)
        log.error("LLM request failed: %s", exc_text)
        return FALLBACK_CATEGORY, 0.0, f"LLM error: {exc_text}", exc_text
    content = response.choices[0].message.content or ""

    parsed = safe_parse_json(content)
    category = _canonicalize_category(str(parsed.get("category", FALLBACK_CATEGORY)).strip())
    confidence = float(parsed.get("confidence", 0.0)) if isinstance(parsed.get("confidence"), (int, float)) else 0.0
    reasoning = str(parsed.get("reasoning", ""))

    normalized_allowed = {_canonicalize_category(cat) for cat in categories}
    if category not in normalized_allowed:
        category = FALLBACK_CATEGORY

    return category or FALLBACK_CATEGORY, max(0.0, min(confidence, 1.0)), reasoning, content


def _apply_rule_classification(title: str, description: str, option_list: List[str]) -> Optional[Tuple[str, str]]:
    """Return (category, reason) if a deterministic rule matches, else None."""
    if not title:
        return None
    text = "\n".join(part for part in [title, description] if part).lower()

    # Hard overrides: first match wins
    for ovr in RULE_OVERRIDES:
        if not isinstance(ovr, dict):
            continue
        phrase = str(ovr.get("phrase") or "").strip().lower()
        category = _canonicalize_category(str(ovr.get("category") or "").strip())
        unless = [str(x or "").strip().lower() for x in (ovr.get("unless") or []) if str(x or "").strip()]
        if not phrase or not category:
            continue
        if phrase in text:
            if any(u and u in text for u in unless):
                continue
            if option_list and category not in {_canonicalize_category(x) for x in option_list}:
                continue
            return category, f"Hard override matched phrase '{phrase}'"

    # Cue scoring
    if not RULE_CUES:
        return None
    matches: Dict[str, int] = {}
    for cat, cues in RULE_CUES.items():
        cat_key = _canonicalize_category(str(cat or "").strip())
        if not cat_key:
            continue
        for cue in cues or []:
            cue_norm = str(cue or "").strip().lower()
            if cue_norm and cue_norm in text:
                matches[cat_key] = matches.get(cat_key, 0) + 1

    if not matches:
        return None

    # Apply precedence ordering first, then max score
    best_cat = None
    best_score = -1
    precedence_order = {c: idx for idx, c in enumerate(RULE_PRECEDENCE)}
    for cat, score in matches.items():
        prec = precedence_order.get(cat, float("inf"))
        candidate = (prec, -score, cat)
        current = (precedence_order.get(best_cat, float("inf")) if best_cat else float("inf"), -best_score, best_cat)
        if best_cat is None or candidate < current:
            best_cat = cat
            best_score = score

    if best_cat is None:
        return None

    return best_cat, f"Rule-based match via cues (score {best_score})"


def classify_issues(
    issues: List[IssueRecord],
    client: openai.OpenAI,
    model: str,
    fallback_model: str,
    system_prompt: str,
    temperature: float,
    categories_by_project: Dict[str, List[str]],
    default_categories: List[str],
    config_label: str,
    log: logging.Logger,
) -> List[ClassifiedIssue]:
    cache: Dict[Tuple[str, str, str, Tuple[str, ...]], Tuple[str, float, str, str]] = {}
    enriched: List[ClassifiedIssue] = []
    llm_elapsed_total = 0.0
    llm_calls = 0
    progress = _progress_tracker(len(issues), "Classifying issues")
    started = time.perf_counter()

    for issue in issues:
        tech_display, tech_key = resolve_technology_context(issue)
        option_list = categories_for_project(tech_key, categories_by_project, default_categories)
        rule_hit = _apply_rule_classification(issue.title, issue.description, option_list)
        forced_category: Optional[str] = None
        if (tech_display or "").strip().lower() == "icps/killer":
            forced_category = "ICPS/Killer"
            if forced_category not in {_canonicalize_category(x) for x in option_list}:
                option_list = option_list + [forced_category]

        if forced_category:
            category = forced_category
            confidence = 1.0
            reasoning = "Technology is ICPS/Killer; auto-mapped to ICPS/Killer."
            raw = "auto"
        elif rule_hit:
            category, reasoning = rule_hit
            if option_list and category not in {_canonicalize_category(x) for x in option_list}:
                option_list = option_list + [category]
            confidence = 1.0
            raw = "rule"
        else:
            cache_key = (issue.title, issue.description, tech_key or "(default)", tuple(option_list))
            if cache_key in cache:
                category, confidence, reasoning, raw = cache[cache_key]
            else:
                llm_start = time.perf_counter()
                category, confidence, reasoning, raw = classify_title(
                    client=client,
                    model=model,
                    fallback_model=fallback_model,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    title=issue.title,
                    description=issue.description,
                    categories=option_list,
                    technology_label=tech_display,
                    config_label=config_label,
                    log=log,
                )
                llm_elapsed_total += time.perf_counter() - llm_start
                llm_calls += 1
                cache[cache_key] = (category, confidence, reasoning, raw)
        enriched.append(
            ClassifiedIssue(
                issue_key=issue.issue_key,
                ips_case_number=issue.ips_case_number,
                title=issue.title,
                jira_title=issue.jira_title,
                created_date=issue.created_date,
                bug_project=issue.bug_project,
                bug_category_custom=issue.bug_category_custom,
                category=category,
                confidence=confidence,
                reasoning=reasoning,
                raw_response=raw,
                technology_display=tech_display or "",
                technology_key=tech_key or "",
            )
        )
        log.debug(
            "Issue %s (IPS %s) [%s] classified as %s (confidence %.2f)",
            issue.issue_key or "(missing key)",
            issue.ips_case_number or "(missing case)",
            tech_display or issue.bug_project or "(unassigned)",
            category,
            confidence,
        )
        progress(len(enriched))

    elapsed = time.perf_counter() - started
    log.info(
        "Classified %d issue(s) in %s.",
        len(enriched),
        _format_elapsed(elapsed),
    )
    record_llm_elapsed(llm_elapsed_total, llm_calls, log)
    return enriched


def _build_user_prompt(
    *,
    title: str,
    description: str,
    categories: List[str],
    technology_label: str,
    config_label: str,
) -> str:
    category_block = "\n".join(f"- {cat}" for cat in categories)
    tech_str = technology_label or "Unspecified"
    return (
        "Predict the correct Issue Type defined in bug_category_config.json.\n"
        f"Technology context: {tech_str}\n"
        f"Config file: {config_label}\n"
        f"Title: {title}\n\n"
        f"Description: {description or '(empty)'}\n\n"
        "Only respond with JSON in the form {\"category\": <string>, \"confidence\": <0-1>, \"reasoning\": <string>}\n"
        f"Allowed Issue Types for this technology:\n{category_block}"
    )


def export_finetune_jsonl(
    records: List[ClassifiedIssue],
    path: str,
    system_prompt: str,
    categories_by_project: Dict[str, List[str]],
    default_categories: List[str],
    config_label: str,
    log: logging.Logger,
) -> None:
    if not path:
        return

    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            option_list = categories_for_project(
                rec.technology_key,
                categories_by_project,
                default_categories,
            )
            user_prompt = _build_user_prompt(
                title=rec.title,
                description=rec.description,
                categories=option_list,
                technology_label=rec.technology_display,
                config_label=config_label,
            )
            assistant_payload = {
                "category": rec.category,
                "confidence": rec.confidence,
                "reasoning": rec.reasoning,
            }
            record = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": json.dumps(assistant_payload)},
                ],
                "review": {
                    "issue_key": rec.issue_key,
                    "ips_case_number": rec.ips_case_number,
                    "created_date": rec.created_date,
                    "technology": rec.technology_display,
                    "title": rec.title,
                    "description": rec.description,
                    "allowed_categories": ", ".join(option_list),
                    "predicted_category": rec.category,
                    "confidence": rec.confidence,
                    "reasoning": rec.reasoning,
                },
                "metadata": {
                    "issue_key": rec.issue_key,
                    "ips_case_number": rec.ips_case_number,
                    "created_date": rec.created_date,
                    "bug_project": rec.bug_project,
                    "bug_category_custom": rec.bug_category_custom,
                    "technology": rec.technology_display,
                },
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    log.info("Saved fine-tuning JSONL to %s", path)


def export_review_csv(
    records: List[ClassifiedIssue],
    path: str,
    categories_by_project: Dict[str, List[str]],
    default_categories: List[str],
    log: logging.Logger,
) -> None:
    if not path:
        return

    rows = []
    for rec in records:
        option_list = categories_for_project(
            rec.technology_key,
            categories_by_project,
            default_categories,
        )
        rows.append(
            {
                "issue_key": rec.issue_key,
                "ips_case_number": rec.ips_case_number,
                "created_date": rec.created_date,
                "technology": rec.technology_display,
                "title": rec.title,
                "description": rec.description,
                "allowed_categories": ", ".join(option_list),
                "predicted_category": rec.category,
                "confidence": rec.confidence,
                "reasoning": rec.reasoning,
            }
        )

    pd.DataFrame(rows).to_csv(path, index=False)
    log.info("Saved review CSV to %s", path)


def to_dataframe(records: List[ClassifiedIssue]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "issue_key": rec.issue_key,
                "ips_case_number": rec.ips_case_number,
                "jira_title": rec.jira_title,
                "created_date": rec.created_date,
                "bug_project": rec.bug_project or "",
                "bug_category_custom": rec.bug_category_custom or "",
                "technology": rec.technology_display or "",
                "technology_key": rec.technology_key or "",
                "ips_title": rec.title,
                "ips_description": rec.description,
                "predicted_category": rec.category,
                "confidence": round(rec.confidence, 3),
            }
            for rec in records
        ]
    )


def export_results(records: List[ClassifiedIssue], path: str, log: logging.Logger) -> None:
    if not path:
        return
    out_df = pd.DataFrame(
        [
            {
                "issue_key": rec.issue_key,
                "ips_case_number": rec.ips_case_number,
                "jira_title": rec.jira_title,
                "created_date": rec.created_date,
                "bug_project": rec.bug_project,
                "bug_category_custom": rec.bug_category_custom,
                "technology": rec.technology_display,
                "technology_key": rec.technology_key,
                "ips_title": rec.title,
                "ips_description": rec.description,
                "predicted_category": rec.category,
                "confidence": rec.confidence,
                "reasoning": rec.reasoning,
                "raw_response": rec.raw_response,
            }
            for rec in records
        ]
    )
    dedup_cols = ["issue_key", "ips_case_number"]
    # Prefer rows that have a non-empty ips_title before deduping.
    out_df["_has_title"] = out_df["ips_title"].fillna("").str.strip().ne("")
    out_df = out_df.sort_values(by=["_has_title", "created_date"], ascending=[False, False])

    before = len(out_df)
    out_df = out_df.drop_duplicates(subset=dedup_cols, keep="first")
    out_df = out_df.drop(columns=["_has_title"])
    dropped = before - len(out_df)
    if dropped:
        log.info(
            "Deduped export results: removed %d duplicate rows based on %s (keeping rows with titles first)",
            dropped,
            ", ".join(dedup_cols),
        )
    out_df.to_csv(path, index=False)
    log.info("Saved detailed classification output to %s", path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify IPS bug titles using an LLM.")
    parser.add_argument("--table", default=DEFAULT_TABLE, help="Source table or view to read from (default respects DB_TABLE env or vw_issues).")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of rows to classify (0 = all).")
    parser.add_argument("--days", type=int, default=None, help="Skip menu and set the lookback window explicitly.")
    parser.add_argument(
        "--ips-ids",
        default="",
        help="Comma/space-separated IPS case numbers to classify (bypasses --days prompt).",
    )
    parser.add_argument("--categories-file", default=DEFAULT_CATEGORIES_FILE, help="JSON file listing allowed categories.")
    parser.add_argument("--system-prompt", default="", help="Override system prompt for the LLM.")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="LLM API key (needed for ExpertGPT fallback).")
    parser.add_argument(
        "--local-url",
        default="",
        help="Override local LLM base URL (default http://127.0.0.1:11434/v1).",
    )
    parser.add_argument(
        "--local-model",
        default="",
        help="Override local LLM model name (default aligns with test_local_llm.py).",
    )
    parser.add_argument(
        "--llm",
        choices=["expertgpt", "local", "auto"],
        default="auto",
        help="Choose LLM backend: expertgpt, local, or auto (default).",
    )
    parser.add_argument(
        "--no-local-llm",
        action="store_true",
        help="Skip local LLM detection and call ExpertGPT directly.",
    )
    parser.add_argument("--temperature", type=float, default=0.0, help="LLM temperature.")
    parser.add_argument("--export-csv", default="", help="Optional path to store detailed results.")
    parser.add_argument(
        "--export-finetune-jsonl",
        default="",
        help="Optional path to store fine-tuning JSONL output (messages + metadata).",
    )
    parser.add_argument(
        "--export-review-csv",
        default="",
        help="Optional path to store a minimal review CSV for human validation.",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (INFO/DEBUG/...).")
    return parser.parse_args()


def _parse_ids(raw: str) -> List[str]:
    if not raw:
        return []
    tokens = re.split(r"[\s,;]+", raw.strip())
    return [tok for tok in tokens if tok]


def main() -> int:
    args = parse_args()
    log = setup_logging(args.log_level)

    ips_ids = _parse_ids(args.ips_ids)
    days_back = None
    if not ips_ids:
        days_back = args.days if args.days and args.days > 0 else prompt_days_interactively()

    categories_by_project, default_categories, cfg_prompt, cfg_path = ensure_categories(args.categories_file, log)
    system_prompt = args.system_prompt.strip() or cfg_prompt or DEFAULT_SYSTEM_PROMPT

    if ips_ids:
        issues = fetch_issues_by_case_numbers(args.table, ips_ids, args.limit, log)
    else:
        issues = fetch_issues(args.table, days_back, args.limit, log)
    if not issues:
        log.info("No issues returned for the selected window.")
        return 0

    local_url = (args.local_url or "").strip() or LOCAL_LLM_BASE_URL
    local_model = (args.local_model or "").strip() or LOCAL_LLM_MODEL

    llm_conn: Optional[LLMConnection] = None
    model_name = ""
    fallback_model = ""

    if args.llm == "local":
        if args.no_local_llm:
            raise SystemExit("--no-local-llm conflicts with --llm local.")
        llm_conn, model_name = try_local_llm(local_url, local_model, log)
        if llm_conn is None:
            raise SystemExit("Local LLM not available.")
        log.info("Using local LLM model: %s", model_name)
    elif args.llm == "expertgpt":
        base_url = enforce_expertgpt_base_url("", log)
        model_name = env_str("EXPERTGPT_MODEL", env_str("MODEL", "")).strip()
        if not model_name:
            raise SystemExit("Missing EXPERTGPT_MODEL (or MODEL) in .env.")
        fallback_model = model_name
        log.info("Using ExpertGPT model: %s", model_name)
        llm_conn = ensure_openai_client(args.api_key, base_url)
    else:
        if args.no_local_llm:
            log.info("Skipping local LLM probe (--no-local-llm specified).")
        elif not LOCAL_LLM_ENABLED:
            log.debug("LOCAL_LLM_ENABLED is false; skipping local backend preference.")
        else:
            llm_conn, model_name = try_local_llm(local_url, local_model, log)
            if llm_conn is not None:
                fallback_model = model_name or ""

        if llm_conn is None:
            base_url = enforce_expertgpt_base_url("", log)
            model_name = env_str("EXPERTGPT_MODEL", env_str("MODEL", "")).strip()
            if not model_name:
                raise SystemExit("Missing EXPERTGPT_MODEL (or MODEL) in .env.")
            fallback_model = model_name
            log.info("Using ExpertGPT model: %s", model_name)
            llm_conn = ensure_openai_client(args.api_key, base_url)
        else:
            log.info("Using local LLM model: %s", model_name)
    try:
        records = classify_issues(
            issues=issues,
            client=llm_conn.client,
            model=model_name,
            fallback_model=fallback_model,
            system_prompt=system_prompt,
            temperature=args.temperature,
            categories_by_project=categories_by_project,
            default_categories=default_categories,
            config_label=os.path.basename(cfg_path),
            log=log,
        )
    finally:
        if llm_conn is not None:
            llm_conn.close()

    print("\nClassification summary:")
    for rec in records:
        title = rec.title or "(no title)"
        project = rec.bug_project or ""
        custom = rec.bug_category_custom or ""
        tech = rec.technology_display or ""
        created = rec.created_date or ""
        category = rec.category or ""
        confidence = f"{rec.confidence:.3f}" if rec.confidence is not None else ""
        issue_key = rec.issue_key or ""
        print(f"Title: {title}")
        print(
            f"  Issue: {issue_key} | Case#: {rec.ips_case_number or ''} | Date: {created} | "
            f"Project: {project} | Custom: {custom} | "
            f"Tech: {tech} | "
            f"Category: {category} | Confidence: {confidence}"
        )

    export_results(records, args.export_csv, log)
    export_finetune_jsonl(
        records,
        args.export_finetune_jsonl,
        system_prompt,
        categories_by_project,
        default_categories,
        os.path.basename(cfg_path),
        log,
    )
    export_review_csv(
        records,
        args.export_review_csv,
        categories_by_project,
        default_categories,
        log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
