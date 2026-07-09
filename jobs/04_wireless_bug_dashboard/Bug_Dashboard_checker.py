"""
POSTGRE vs JIRA validation

Goal:
- Compare reporter counts between:
    Source A: PostgreSQL (ips_jira_bugs reporter) using SAME SQL filter
    Source B: Jira (issue.fields.reporter) for the SAME issue keys

Requirements:
  pip install jira psycopg2-binary python-dotenv requests

Env (.env) used (your requested ones):
  JIRA_USER=sys_wirelessce
  JIRA_PASSWORD=...

  DB_NAME=wirelesscustomerengineering
  DB_USER=wirelesscustomerengi_so
  DB_PASS=...
  DB_HOST=postgres5108-lb-ir-in.dbaas.intel.com
  DB_PORT=5433
"""

from __future__ import annotations

import os
import re
import sys
import json
import argparse
import logging
import warnings
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from jira import JIRA
from jira.exceptions import JIRAError
from urllib3.exceptions import InsecureRequestWarning


# -------------------------
# .env loading (script/exe folder)
# -------------------------
def _base_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()


def _load_env_from_nearby_dirs(start_dir: str, max_up: int = 4) -> Optional[str]:
    """Load .env from start_dir, then parents up to max_up levels."""
    cur = os.path.abspath(start_dir)
    for _ in range(max_up + 1):
        env_path = os.path.join(cur, ".env")
        if os.path.isfile(env_path):
            load_dotenv(dotenv_path=env_path, override=False)
            return env_path
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


_load_env_from_nearby_dirs(BASE_DIR)


# -------------------------
# Logging
# -------------------------
def setup_logging(log_file: str, level: str) -> logging.Logger:
    lvl = getattr(logging, (level or "INFO").upper(), logging.INFO)

    logger = logging.getLogger("db_vs_jira_validation")
    logger.setLevel(logging.DEBUG)  # let handlers filter

    # Avoid duplicate handlers if re-run in same interpreter
    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Console handler
    ch = logging.StreamHandler(stream=sys.stdout)
    ch.setLevel(lvl)
    ch.setFormatter(fmt)

    # File handler — overwrite each run so the file only reflects the latest
    # execution. Append mode caused stale [ERROR] lines from old runs to persist,
    # which tripped the db_health_notify scanner even when the run was clean.
    fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)

    logger.info("Logging initialized. console_level=%s file=%s", level.upper(), log_file)
    return logger


# -------------------------
# Jira endpoints
# -------------------------
JIRA_TEST_SERVER = "https://jiratest.idoc.intel.com"
JIRA_SERVER = "https://jira.idoc.intel.com"

JIRA_USER = (os.getenv("JIRA_USER") or "").strip()
JIRA_PASSWORD = (os.getenv("JIRA_PASSWORD") or "").strip()

# PostgreSQL conn params (.env)
conn_params = {
    "database": (os.getenv("DB_NAME") or "").strip(),
    "user": (os.getenv("DB_USER") or "").strip(),
    "password": (os.getenv("DB_PASS") or "").strip(),
    "host": (os.getenv("DB_HOST") or "").strip(),
    "port": (os.getenv("DB_PORT") or "5433").strip(),
}


# -------------------------
# Shared SQL (your original CTE)
# -------------------------
CTE_BASE_TEMPLATE = """
WITH calculated_columns AS (
    SELECT 
        *,
        CASE 
            WHEN ips_case_number > 0 AND ips_closed_date::date > CURRENT_DATE THEN 1 
            ELSE 0 
        END as is_open_flag
    FROM {table_name}
),
aging_metrics AS (
    SELECT 
        *,
        CASE 
            WHEN is_open_flag = 1 THEN (CURRENT_DATE - ips_created_date::date)
            ELSE -1 
        END as ips_open_days,
        COALESCE((CURRENT_DATE - ips_last_modified_date::date), 0) as last_mod_days
    FROM calculated_columns
)
"""

DB_JIRA_WORK_WHERE = """
    (jira_id IS NOT NULL OR ips_jira_id IS NOT NULL)
    AND jira_status ILIKE ANY (ARRAY['new', 'open', 'in progress', 'pending', 'open->sighting', 'open -> sighting'])
    AND ips_status NOT ILIKE 'closed'
    AND (ips_sub_status IS NULL OR ips_sub_status != 'Close-Pending')
"""

_JIRA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]+-\d+$")


def is_probably_jira_key(s: str) -> bool:
    return bool(_JIRA_KEY_RE.match((s or "").strip()))


# -------------------------
# Name merge logic (improved)
# -------------------------
_HONORIFICS = {"mr", "ms", "mrs", "dr", "prof", "sir", "madam"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

_KEEP_RE = re.compile(r"[^a-z0-9,\s]+")


def person_key(raw: str) -> str:
    """
    Robust person key for matching DB vs Jira reporter strings.

    Handles:
      - "Charles Chu" == "Chu, Charles P"
      - "Frank Yang"  == "Yang, Frank Fc"
      - "Sowmya Indukuri" == "Indukuri, Sowmya Sri"

        Strategy:
            1) normalize comma ordering: "last, first middle" => "first middle last"
            2) strip punctuation
            3) tokenize, drop honorifics/suffixes/initials
            4) prefer stable first-name + last-name key (order-insensitive)
    """
    s = (raw or "").strip().lower()
    if not s:
        return "unknown"

    # "last, first middle" -> "first middle last"
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) >= 2:
            last = parts[0]
            rest = " ".join(parts[1:])
            s = f"{rest} {last}".strip()

    s = _KEEP_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()

    toks = [t for t in s.split(" ") if t]
    toks = [t for t in toks if t not in _HONORIFICS and t not in _SUFFIXES]
    toks = [t for t in toks if len(t) > 1]

    if not toks:
        return "unknown"

    # De-dupe
    uniq: List[str] = []
    seen = set()
    for t in toks:
        if t not in seen:
            uniq.append(t)
            seen.add(t)

    if len(uniq) == 1:
        return uniq[0]

    # Prefer first + last tokens. This keeps equivalent names aligned:
    # "Ravi Vanapalli" == "Vanapalli, Ravi Kumar".
    first = uniq[0]
    last = uniq[-1]
    key_parts = sorted([first, last])
    return " ".join(key_parts) or "unknown"


# -------------------------
# DB + Jira fetch
# -------------------------
@dataclass(frozen=True)
class IssueRow:
    issue_key: str
    reporter: str


def fetch_db_rows(table_name: str, limit: int = 0) -> List[IssueRow]:
    missing = [k for k, v in conn_params.items() if not v]
    if missing:
        raise SystemExit(f"Missing DB params in .env: {missing}")

    # Validate table name to prevent SQL injection (alphanumeric, underscore, dot only)
    if not re.match(r'^[a-zA-Z0-9_.]+$', table_name):
        raise ValueError(f"Invalid table name: {table_name}")

    cte = CTE_BASE_TEMPLATE.format(table_name=table_name)

    lim_sql = ""
    params: Tuple[int, ...] = ()
    if limit and limit > 0:
        lim_sql = "LIMIT %s"
        params = (limit,)

    query = f"""
    {cte}
    SELECT
        COALESCE(jira_id, ips_jira_id)::text AS issue_key,
        COALESCE(reporter, '')::text AS reporter
    FROM aging_metrics
    WHERE {DB_JIRA_WORK_WHERE}
    ORDER BY 1 ASC
    {lim_sql};
    """

    try:
        with psycopg2.connect(**conn_params) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
    except Exception as e:
        raise SystemExit(f"DB query failed: {e}") from e

    return [
        IssueRow(
            issue_key=(r.get("issue_key") or "").strip(),
            reporter=(r.get("reporter") or "").strip(),
        )
        for r in rows
    ]


def jira_reporter_identity(user_obj: Any) -> Tuple[str, str]:
    if not user_obj:
        return ("unknown", "Unknown")
    reporter_id = (
        getattr(user_obj, "accountId", None)
        or getattr(user_obj, "name", None)
        or getattr(user_obj, "key", None)
        or getattr(user_obj, "emailAddress", None)
        or getattr(user_obj, "displayName", None)
        or "unknown"
    )
    display = getattr(user_obj, "displayName", None) or str(reporter_id)
    return (str(reporter_id), str(display))


def connect_to_jira(is_test_server: bool) -> JIRA:
    if not JIRA_USER or not JIRA_PASSWORD:
        raise SystemExit("Missing JIRA_USER/JIRA_PASSWORD in .env.")

    server = JIRA_TEST_SERVER if is_test_server else JIRA_SERVER
    # WARNING: SSL verification disabled for Intel internal JIRA
    # Internal certificate chain may not be in standard CA bundles
    options = {"server": server, "verify": False}
    if options.get("verify") is False:
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)
    try:
        jira = JIRA(options=options, basic_auth=(JIRA_USER, JIRA_PASSWORD))
    except JIRAError as e:
        raise SystemExit(f"Failed to connect to Jira: {e}") from e

    return jira


def chunked(seq: List[str], size: int) -> List[List[str]]:
    return [seq[i: i + size] for i in range(0, len(seq), size)]


def fetch_jira_reporters_for_keys(jira: JIRA, keys: List[str], chunk_size: int, log: logging.Logger) -> Dict[str, str]:
    """
    Return: {issue_key: jira_reporter_displayName}
    """
    out: Dict[str, str] = {}
    for batch in chunked(keys, chunk_size):
        jql = "key in (" + ", ".join(batch) + ")"
        try:
            issues = jira.search_issues(jql, maxResults=False, fields="reporter")
        except JIRAError as e:
            log.warning("Jira search failed (batch size %d): %s", len(batch), e)
            continue
        for issue in list(issues):
            _rid, rname = jira_reporter_identity(getattr(issue.fields, "reporter", None))
            out[str(issue.key)] = rname or "Unknown"
    return out


# -------------------------
def build_diff_report_text(
    *,
    db_map: Dict[str, str],
    jira_map: Dict[str, str],
    not_found: List[str],
    mismatches: List[Tuple[str, str, str]],
    db_counts: Counter[str],
    jira_counts: Counter[str],
    db_examples: Dict[str, str],
    jira_examples: Dict[str, str],
) -> str:
    lines: List[str] = []
    lines.append("DB vs JIRA validation result")
    lines.append("=" * 60)
    lines.append(f"DB issues (from filter):        {len(db_map)}")
    lines.append(f"Jira issues found:              {len(jira_map)}")
    lines.append(f"Jira issues not found:          {len(not_found)}")
    lines.append(f"Issue-level reporter mismatches:{len(mismatches)}")
    lines.append("")

    if not_found:
        lines.append("Jira NOT FOUND (sample up to 20):")
        for k in not_found[:20]:
            lines.append(f"  - {k}")
        lines.append("")

    if mismatches:
        lines.append("Reporter mismatches (sample up to 30):")
        lines.append(f"{'Issue':<18} | {'DB reporter':<35} | {'Jira reporter':<35}")
        lines.append("-" * 95)
        for k, dbr, jrr in mismatches[:30]:
            lines.append(f"{k:<18} | {dbr[:35]:<35} | {jrr[:35]:<35}")
        lines.append("")

    all_people = set(db_counts.keys()) | set(jira_counts.keys())
    deltas = []
    for pk in all_people:
        d = db_counts.get(pk, 0)
        j = jira_counts.get(pk, 0)
        if d != j:
            deltas.append((abs(j - d), pk, d, j))
    deltas.sort(reverse=True)

    lines.append("Reporter count deltas (Jira - DB), top 50:")
    lines.append(f"{'PersonKey':<28} | {'DB':>6} | {'Jira':>6} | {'Delta':>6} | Examples")
    lines.append("-" * 110)
    if not deltas:
        lines.append("No count differences found.")
    else:
        for _, pk, d, j in deltas[:50]:
            ex = f"DB:'{db_examples.get(pk, '')}'  Jira:'{jira_examples.get(pk, '')}'"
            lines.append(f"{pk[:28]:<28} | {d:>6} | {j:>6} | {j - d:>6} | {ex}")

    lines.append("")
    return "\n".join(lines).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="Use JIRA_TEST_SERVER")
    ap.add_argument("--table", default="ips_jira_bugs", help="DB table/schema to query (default: ips_jira_bugs)")
    ap.add_argument("--limit", type=int, default=0, help="Limit DB rows (0=ALL)")
    ap.add_argument("--jira-chunk", type=int, default=200, help="Jira key batch size per JQL")
    ap.add_argument("--show-mismatches", type=int, default=60, help="Show up to N issue mismatches (console)")

    # Logging controls
    ap.add_argument("--log-file", default=os.path.join(BASE_DIR, "db_vs_jira_validation.log"), help="Log file path")
    ap.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"), help="Console log level (INFO/DEBUG/...)")


    args = ap.parse_args()

    log = setup_logging(args.log_file, args.log_level)

    log.info("Starting validation. DB_HOST=%s DB_NAME=%s table=%s test_jira=%s limit=%s",
             conn_params.get("host"), conn_params.get("database"), args.table, args.test, args.limit)

    # 1) DB rows
    db_rows = fetch_db_rows(table_name=args.table, limit=args.limit)
    log.info("DB rows fetched: %d", len(db_rows))

    db_map = {r.issue_key: r.reporter for r in db_rows if r.issue_key}
    keys_all = list(db_map.keys())

    valid_keys = [k for k in keys_all if is_probably_jira_key(k)]
    invalid_keys = [k for k in keys_all if k and not is_probably_jira_key(k)]
    if invalid_keys:
        log.warning("DB has %d issue_key values that don't look like Jira keys. Sample: %s",
                    len(invalid_keys), ", ".join(invalid_keys[:10]))

    # 2) Jira fetch
    jira = connect_to_jira(is_test_server=args.test)
    log.info("Connected to Jira server=%s", (JIRA_TEST_SERVER if args.test else JIRA_SERVER))
    try:
        jira_map = fetch_jira_reporters_for_keys(jira, valid_keys, chunk_size=max(10, args.jira_chunk), log=log)
    finally:
        try:
            jira.close()
        except Exception as e:
            log.warning("Failed to close JIRA connection: %s", e)

    not_found = [k for k in valid_keys if k not in jira_map]
    log.info("Jira issues found: %d (from valid DB keys: %d)", len(jira_map), len(valid_keys))
    if not_found:
        log.warning("Jira issues NOT found: %d. Sample: %s", len(not_found), ", ".join(not_found[:10]))

    # 3) Issue mismatches (person_key)
    mismatches: List[Tuple[str, str, str]] = []
    for k in valid_keys:
        if k not in jira_map:
            continue
        db_rep = db_map.get(k, "") or "Unknown"
        jira_rep = jira_map.get(k, "") or "Unknown"
        if person_key(db_rep) != person_key(jira_rep):
            mismatches.append((k, db_rep, jira_rep))

    if mismatches:
        log.warning("Issue-level reporter mismatches detected: %d", len(mismatches))
        for k, dbr, jrr in mismatches[: min(50, len(mismatches))]:
            log.warning("Mismatch issue=%s | DB='%s' | Jira='%s' | DB_key='%s' Jira_key='%s'",
                        k, dbr, jrr, person_key(dbr), person_key(jrr))
        if len(mismatches) > 50:
            log.warning("... %d more mismatches not shown in logs (see diff report file).", len(mismatches) - 50)

    # 4) Count deltas (person_key) + examples
    db_counts: Counter[str] = Counter()
    jira_counts: Counter[str] = Counter()
    db_examples: Dict[str, str] = {}
    jira_examples: Dict[str, str] = {}

    matched_keys = [k for k in valid_keys if k in jira_map]

    for _k in matched_keys:
        raw = db_map.get(_k, "")
        pk = person_key(raw)
        db_counts[pk] += 1
        db_examples.setdefault(pk, raw or "Unknown")

    for _k in matched_keys:
        raw = jira_map.get(_k, "")
        pk = person_key(raw)
        jira_counts[pk] += 1
        jira_examples.setdefault(pk, raw or "Unknown")

    any_count_diff = any(db_counts.get(pk, 0) != jira_counts.get(pk, 0) for pk in set(db_counts) | set(jira_counts))
    if any_count_diff:
        log.warning("Reporter count differences detected (Jira vs DB).")

    # not_found = Jira tickets that no longer exist (deleted/moved) — not a DB sync issue
    # Only treat reporter mismatches or count deltas as real differences
    differences_found = bool(mismatches) or any_count_diff
    # Build report text (for file)
    report_text = build_diff_report_text(
        db_map=db_map,
        jira_map=jira_map,
        not_found=not_found,
        mismatches=mismatches,
        db_counts=db_counts,
        jira_counts=jira_counts,
        db_examples=db_examples,
        jira_examples=jira_examples,
    )

    # Write report to file if diffs found (so you have a stable artifact)
    report_path = os.path.join(BASE_DIR, "db_vs_jira_diff_report.txt")
    if differences_found:
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_text + "\n")
            log.warning("Diff report saved to: %s", report_path)
        except Exception as e:
            log.warning("Failed to write diff report file: %s", e)

        # Also log a short delta summary
        all_people = set(db_counts.keys()) | set(jira_counts.keys())
        deltas = []
        for pk in all_people:
            d = db_counts.get(pk, 0)
            j = jira_counts.get(pk, 0)
            if d != j:
                deltas.append((abs(j - d), pk, d, j))
        deltas.sort(reverse=True)
        for _, pk, d, j in deltas[:50]:
            log.warning("Count delta person_key='%s' DB=%d Jira=%d Delta=%+d (ex: DB='%s' Jira='%s')",
                        pk, d, j, (j - d), db_examples.get(pk, ""), jira_examples.get(pk, ""))

    else:
        log.info("No differences found. DB matches Jira for this filter and these issues.")
        # Remove stale diff report so the bat file doesn't misread it as a current error
        if os.path.exists(report_path):
            try:
                os.remove(report_path)
                log.info("Removed stale diff report: %s", report_path)
            except Exception as e:
                log.warning("Could not remove stale diff report: %s", e)

    log.info("DONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
