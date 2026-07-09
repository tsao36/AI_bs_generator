"""Read-only database chatbot backed by ExpertGPT or a local LLM.

- Uses PostgreSQL as the knowledge source.
- Only allows SELECT queries (no modifications).
- Supports local LLM (e.g., Ollama) with ExpertGPT fallback.

Env (.env in repo root):
    EXPERTGPT_TOKEN, EXPERTGPT_URL, EXPERTGPT_MODEL
    LOCAL_LLM_URL, LOCAL_LLM_MODEL, LOCAL_LLM_API_KEY, LOCAL_LLM_ENABLED
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
from typing import Any, Dict, List, Optional, Tuple

import httpx
import openai
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# align with Wireless_bug_dashboard.py DB config
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "APIs"))
import Sherlock  # type: ignore


# -------------------------
# Config & logging
# -------------------------

def _base_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"), override=False)


def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip() or default


def _get_db_params() -> Dict[str, Any]:
    return {
        "database": Sherlock.PostgresCustomerEngineeringDb.database,
        "user": Sherlock.PostgresCustomerEngineeringDb.user,
        "password": Sherlock.PostgresCustomerEngineeringDb.password,
        "host": Sherlock.PostgresCustomerEngineeringDb.host,
        "port": Sherlock.PostgresCustomerEngineeringDb.port,
    }

EXPERTGPT_TOKEN = env_str("EXPERTGPT_TOKEN")
EXPERTGPT_URL = env_str("EXPERTGPT_URL", "https://expertgpt.intel.com/v1")
EXPERTGPT_MODEL = env_str("EXPERTGPT_MODEL", env_str("MODEL", "gpt-4.1"))
EXPERTGPT_CA_BUNDLE = env_str("EXPERTGPT_CA_BUNDLE", "")
EXPERTGPT_INSECURE = env_str("EXPERTGPT_INSECURE", "").lower() in {"1", "true", "yes"}

LOCAL_LLM_BASE_URL = env_str("LOCAL_LLM_URL", "http://127.0.0.1:11434/v1")
LOCAL_LLM_MODEL = env_str("LOCAL_LLM_MODEL", "llama3.1:8b-instruct-q4_K_M")
LOCAL_LLM_API_KEY = env_str("LOCAL_LLM_API_KEY", "ollama")
LOCAL_LLM_ENABLED = env_str("LOCAL_LLM_ENABLED", "1").lower() not in {"0", "false", "no"}


SQL_SYSTEM_PROMPT = """
You are a read-only database assistant.
Rules:
- Generate a single SQL SELECT query that answers the question.
- Never use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, REVOKE, or COPY.
- Use only the provided schema.
- Prefer explicit column lists (avoid SELECT * when possible).
- Treat 'NA', empty strings, and NULL as missing data; exclude them in rankings and counts when appropriate.
- If the answer is not in the schema, say you cannot answer.
Output format:
Return JSON with keys: sql, explanation, need_more_data (true/false), next_question (optional).
Do NOT include markdown fences.
""".strip()

PLAN_SYSTEM_PROMPT = """
You are a read-only database planning assistant.
Goal: create a small plan that answers the user's question using the provided schema.
Rules:
- Return 1-3 SQL SELECT queries max.
- Never use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, REVOKE, or COPY.
- Use only the provided schema.
- Prefer explicit column lists (avoid SELECT * when possible).
- Treat 'NA', empty strings, and NULL as missing data; exclude them in rankings and counts when appropriate.
- If the answer is not in the schema, return an empty queries list and explain why.
Output format (JSON):
{
    "queries": [
        {"sql": "SELECT ...", "purpose": "why this query"}
    ],
    "explanation": "short plan summary"
}
Do NOT include markdown fences.
""".strip()

ANSWER_SYSTEM_PROMPT = """
You are a data analyst assistant.
Rules:
- Use only the provided query results to answer.
- If the data is insufficient, say so.
- Be concise and factual.
- Treat 'NA', empty strings, and NULL as missing data when interpreting results.
""".strip()


def setup_logging(level: str) -> logging.Logger:
    logger = logging.getLogger("db_chatbot")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger
    lvl = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handler.setLevel(lvl)
    logger.addHandler(handler)
    return logger


# -------------------------
# LLM helpers
# -------------------------

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


def ensure_openai_client(api_key: str, base_url: str) -> LLMConnection:
    if not hasattr(openai, "OpenAI"):
        raise SystemExit("The openai package is not installed.")
    if not api_key:
        raise SystemExit("Missing API key for LLM.")
    verify: Any = True
    if EXPERTGPT_INSECURE:
        verify = False
    elif EXPERTGPT_CA_BUNDLE:
        if not os.path.exists(EXPERTGPT_CA_BUNDLE):
            raise SystemExit(f"EXPERTGPT_CA_BUNDLE not found: {EXPERTGPT_CA_BUNDLE}")
        verify = EXPERTGPT_CA_BUNDLE
    http_client = httpx.Client(proxy=None, verify=verify, trust_env=False)
    client = openai.OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    return LLMConnection(client=client, http_client=http_client)


def try_local_llm(log: logging.Logger) -> Tuple[Optional[LLMConnection], str]:
    if not LOCAL_LLM_ENABLED:
        return None, ""
    if not LOCAL_LLM_BASE_URL or not LOCAL_LLM_MODEL:
        return None, ""
    try:
        conn = ensure_openai_client(LOCAL_LLM_API_KEY, LOCAL_LLM_BASE_URL)
        return conn, LOCAL_LLM_MODEL
    except Exception as exc:
        log.warning("Local LLM unavailable: %s", exc)
        return None, ""


# -------------------------
# DB helpers
# -------------------------

SELECT_ONLY_RE = re.compile(r"^\s*select\b", re.IGNORECASE | re.DOTALL)
FORBIDDEN_SQL_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy)\b",
    re.IGNORECASE,
)
TABLE_REF_RE = re.compile(r"\b(from|join)\s+([a-zA-Z0-9_\.\"]+)", re.IGNORECASE)


def validate_select_only(sql: str, allowed_tables: Optional[List[str]] = None) -> None:
    if not SELECT_ONLY_RE.search(sql):
        raise ValueError("Only SELECT statements are allowed.")
    if FORBIDDEN_SQL_RE.search(sql):
        raise ValueError("Statement contains forbidden keywords.")
    if allowed_tables:
        allowed = {t.lower() for t in allowed_tables}
        for match in TABLE_REF_RE.finditer(sql):
            kw = match.group(1).lower()
            raw = match.group(2)
            if kw == "from":
                window = sql[max(0, match.start() - 25): match.start()].lower()
                if any(token in window for token in ("extract(", "date_part(", "substring(", "trim(")):
                    continue
            cleaned = raw.strip('"')
            table = cleaned.split(".")[-1].lower()
            if table not in allowed:
                raise ValueError(f"Table '{table}' is not allowed.")


def open_readonly_conn():
    db_params = _get_db_params()
    missing = [k for k, v in db_params.items() if not v]
    if missing:
        raise SystemExit(f"Missing DB params in Sherlock config: {missing}")
    conn = psycopg2.connect(**db_params)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def load_schema(tables: List[str], log: logging.Logger) -> Dict[str, List[str]]:
    schema: Dict[str, List[str]] = {}
    with open_readonly_conn() as conn:
        with conn.cursor() as cur:
            if tables:
                table_filter = list(tables)
                cur.execute(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = ANY(%s)
                    ORDER BY table_name, ordinal_position
                    """,
                    (table_filter,),
                )
            else:
                cur.execute(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    ORDER BY table_name, ordinal_position
                    """
                )
            rows = cur.fetchall()
    for table_name, column_name in rows:
        schema.setdefault(table_name, []).append(column_name)
    if not schema:
        log.warning("No tables discovered with the provided filter.")
    return schema


def format_schema(schema: Dict[str, List[str]]) -> str:
    lines = []
    for table, cols in schema.items():
        lines.append(f"{table}({', '.join(cols)})")
    return "\n".join(lines)


def run_query(sql: str, allowed_tables: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    validate_select_only(sql, allowed_tables)
    with open_readonly_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    return rows


# -------------------------
# Chat loop
# -------------------------

def build_user_prompt(question: str, schema_text: str, max_rows: int) -> str:
    return (
        "Database schema:\n"
        f"{schema_text}\n\n"
        f"User question: {question}\n"
    )


def _extract_sql_fallback(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"```sql\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"\bselect\b[\s\S]+", text, re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _strip_limit(sql: str) -> str:
    if not sql:
        return sql
    return re.sub(r"\s+LIMIT\s+\d+\s*;?\s*$", "", sql, flags=re.IGNORECASE)


def _resolve_reporter_column(schema: Dict[str, List[str]], table: str) -> str:
    cols = schema.get(table, [])
    if "reporter" in cols:
        return "reporter"
    if "jira_reporter_name" in cols:
        return "jira_reporter_name"
    return "reporter"


def _heuristic_sql(question: str, schema: Dict[str, List[str]], tables: List[str]) -> Tuple[Optional[str], str]:
    text = (question or "").lower()
    table = tables[0] if tables else "vw_issues"

    if "how many" in text and "issue" in text:
        return f"SELECT COUNT(*) AS issue_count FROM {table}", "Auto query for total issue count."

    if "reporter" in text and any(key in text for key in ("most", "highest", "top")):
        col = _resolve_reporter_column(schema, table)
        sql = (
            f"SELECT {col} AS reporter, COUNT(*) AS issue_count "
            f"FROM {table} "
            f"WHERE UPPER(COALESCE(NULLIF(TRIM({col}), ''), 'NA')) <> 'NA' "
            f"GROUP BY {col} "
            f"ORDER BY issue_count DESC "
            f"LIMIT 1"
        )
        return sql, "Auto query for top reporter (excluding NA)."

    if "which year" in text and "most" in text and "issue" in text:
        sql = (
            f"SELECT EXTRACT(YEAR FROM ips_created_date) AS created_year, COUNT(*) AS issue_count "
            f"FROM {table} "
            f"GROUP BY created_year "
            f"ORDER BY issue_count DESC "
            f"LIMIT 1"
        )
        return sql, "Auto query for year with most issues."

    return None, ""


def ask_llm_for_sql(
    client: openai.OpenAI,
    model: str,
    question: str,
    schema_text: str,
    max_rows: int,
) -> Dict[str, Any]:
    user_prompt = build_user_prompt(question, schema_text, max_rows)
    response = client.chat.completions.create(
        model=model,
        temperature=0.0,
        messages=[
            {"role": "system", "content": SQL_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # attempt to salvage JSON
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])
        sql = _extract_sql_fallback(content)
        return {"sql": sql, "explanation": content}


def ask_llm_for_plan(
    client: openai.OpenAI,
    model: str,
    question: str,
    schema_text: str,
    max_rows: int,
) -> Dict[str, Any]:
    user_prompt = build_user_prompt(question, schema_text, max_rows)
    response = client.chat.completions.create(
        model=model,
        temperature=0.0,
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])
        return {"queries": [], "explanation": content}


def ask_llm_for_answer(
    client: openai.OpenAI,
    model: str,
    question: str,
    results: List[Dict[str, Any]],
) -> str:
    response = client.chat.completions.create(
        model=model,
        temperature=0.0,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    f"Query results (JSON):\n{json.dumps(results, ensure_ascii=False, default=str)}"
                ),
            },
        ],
    )
    return (response.choices[0].message.content or "").strip()


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


def _with_fallback(primary, fallback, log: logging.Logger):
    try:
        return primary()
    except Exception as exc:
        log.warning("Primary LLM call failed: %s", exc)
        if fallback is None:
            raise
        return fallback()


def _schema_answer_for_question(
    question: str,
    schema: Dict[str, List[str]],
    default_tables: List[str],
) -> Optional[str]:
    text = (question or "").lower()
    if "column" not in text:
        return None

    col_match = re.search(r"column\s+['\"]?([a-zA-Z0-9_]+)['\"]?", text)
    if not col_match:
        return None
    column = col_match.group(1)

    table = None
    for table_name in schema.keys():
        if table_name.lower() in text:
            table = table_name
            break
    if table is None and len(default_tables) == 1:
        table = default_tables[0]

    if table is None:
        return "Please specify the table name for the column check."

    cols = schema.get(table, [])
    exists = column in cols
    if exists:
        return f"Yes. Column '{column}' exists in table '{table}'."
    return f"No. Column '{column}' was not found in table '{table}'."


def _is_sample_row_request(question: str) -> bool:
    text = (question or "").lower()
    return any(
        key in text
        for key in (
            "print 1 row",
            "print one row",
            "show 1 row",
            "show one row",
            "sample row",
            "example row",
            "one row of data",
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only database chatbot")
    parser.add_argument(
        "--tables",
        default="vw_issues",
        help="Comma-separated table allowlist (default: vw_issues).",
    )
    parser.add_argument("--max-rows", type=int, default=50, help="Maximum rows to return.")
    parser.add_argument("--max-steps", type=int, default=3, help="Max multi-step queries per question.")
    parser.add_argument(
        "--strategy",
        choices=["plan", "single"],
        default="plan",
        help="Query strategy: plan (multi-query) or single.",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Run a simple health check against the semantic view and exit.",
    )
    parser.add_argument("--no-local-llm", action="store_true", help="Skip local LLM and use ExpertGPT.")
    parser.add_argument(
        "--llm",
        choices=["expertgpt", "local", "auto"],
        default="auto",
        help="Choose LLM backend: expertgpt, local, or auto (default).",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log = setup_logging(args.log_level)

    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    schema = load_schema(tables, log)
    schema_text = format_schema(schema)

    if args.health:
        table = tables[0] if tables else "vw_issues"
        try:
            row = run_query(f"SELECT * FROM {table} LIMIT 1", tables)
            count = run_query(f"SELECT COUNT(*) AS total FROM {table}", tables)
        except Exception as exc:
            raise SystemExit(f"Health check failed: {exc}")
        print("Health check OK")
        print(f"Table: {table}")
        print(f"Total rows: {count[0]['total'] if count else 'NA'}")
        print("Sample row keys:")
        print(", ".join(row[0].keys()) if row else "No rows returned.")
        return 0

    primary_conn: Optional[LLMConnection] = None
    primary_model = ""
    fallback_conn: Optional[LLMConnection] = None
    fallback_model = ""

    if args.llm == "expertgpt":
        if not EXPERTGPT_TOKEN or not EXPERTGPT_MODEL:
            raise SystemExit("ExpertGPT is not configured in .env.")
        primary_conn = ensure_openai_client(EXPERTGPT_TOKEN, EXPERTGPT_URL)
        primary_model = EXPERTGPT_MODEL
        log.info("Using ExpertGPT model: %s", primary_model)
    elif args.llm == "local":
        if args.no_local_llm:
            raise SystemExit("--no-local-llm conflicts with --llm local.")
        primary_conn, primary_model = try_local_llm(log)
        if primary_conn is None:
            raise SystemExit("Local LLM is not available.")
        log.info("Using local LLM model: %s", primary_model)
    else:
        if EXPERTGPT_TOKEN and EXPERTGPT_MODEL:
            primary_conn = ensure_openai_client(EXPERTGPT_TOKEN, EXPERTGPT_URL)
            primary_model = EXPERTGPT_MODEL
            log.info("Using ExpertGPT model: %s", primary_model)
        else:
            log.warning("ExpertGPT not configured; attempting local LLM.")

        if not args.no_local_llm:
            fallback_conn, fallback_model = try_local_llm(log)
            if fallback_conn is not None:
                log.info("Local LLM available for fallback: %s", fallback_model)

    if primary_conn is None:
        if fallback_conn is None:
            raise SystemExit("No LLM available (ExpertGPT and local LLM both unavailable).")
        primary_conn, primary_model = fallback_conn, fallback_model
        fallback_conn, fallback_model = None, ""

    try:
        while True:
            question = input("\nQuestion (or 'exit'): ").strip()
            if not question:
                continue
            if question.lower() in {"exit", "quit"}:
                break

            if _is_sample_row_request(question):
                try:
                    rows = run_query("SELECT * FROM ips_jira_bugs LIMIT 1", tables)
                except Exception as exc:
                    print(f"\nQuery rejected or failed: {exc}")
                    continue
                print("\nAnswer:")
                if rows:
                    print(json.dumps(rows[0], indent=2, default=str))
                else:
                    print("No rows returned.")
                continue

            schema_answer = _schema_answer_for_question(question, schema, tables)
            if schema_answer:
                print("\nAnswer:")
                print(schema_answer)
                continue

            auto_sql, auto_reason = _heuristic_sql(question, schema, tables)
            if auto_sql:
                try:
                    rows = run_query(auto_sql, tables or None)
                except Exception as exc:
                    print(f"\nQuery rejected or failed: {exc}")
                    print(f"SQL: {auto_sql}")
                    continue
                answer = ask_llm_for_answer(primary_conn.client, primary_model, question, [{"sql": auto_sql, "rows": rows}])
                print("\nAnswer:")
                print(answer or "No answer returned.")
                print("\nSQL reasoning:")
                print(auto_reason)
                print("\nExecuted SQL:")
                print(auto_sql)
                continue

            llm_start = time.perf_counter()
            all_results: List[Dict[str, Any]] = []
            last_explanation = ""
            last_sql = ""
            follow_question = question

            if args.strategy == "plan":
                plan_payload = _with_fallback(
                    lambda: ask_llm_for_plan(primary_conn.client, primary_model, follow_question, schema_text, args.max_rows),
                    (
                        None
                        if fallback_conn is None
                        else lambda: ask_llm_for_plan(
                            fallback_conn.client,
                            fallback_model,
                            follow_question,
                            schema_text,
                            args.max_rows,
                        )
                    ),
                    log,
                )
                queries = plan_payload.get("queries") or []
                last_explanation = (plan_payload.get("explanation") or "").strip()
                for item in queries[: max(1, args.max_steps)]:
                    sql = _strip_limit(str(item.get("sql") or "").strip())
                    if not sql:
                        continue
                    try:
                        rows = run_query(sql, tables or None)
                    except Exception as exc:
                        print(f"\nQuery rejected or failed: {exc}")
                        print(f"SQL: {sql}")
                        break
                    all_results.append({"sql": sql, "rows": rows, "purpose": item.get("purpose", "")})
                    last_sql = sql
            else:
                for _ in range(max(1, args.max_steps)):
                    payload = _with_fallback(
                        lambda: ask_llm_for_sql(primary_conn.client, primary_model, follow_question, schema_text, args.max_rows),
                        (
                            None
                            if fallback_conn is None
                            else lambda: ask_llm_for_sql(
                                fallback_conn.client,
                                fallback_model,
                                follow_question,
                                schema_text,
                                args.max_rows,
                            )
                        ),
                        log,
                    )
                    sql = _strip_limit((payload.get("sql") or "").strip())
                    explanation = (payload.get("explanation") or "").strip()
                    need_more = bool(payload.get("need_more_data"))
                    next_question = (payload.get("next_question") or "").strip()

                    if not sql:
                        print("\nNo SQL returned. Response:")
                        print(explanation or payload)
                        break

                    try:
                        rows = run_query(sql, tables or None)
                    except Exception as exc:
                        print(f"\nQuery rejected or failed: {exc}")
                        print(f"SQL: {sql}")
                        break

                    all_results.append({"sql": sql, "rows": rows})
                    last_explanation = explanation
                    last_sql = sql

                    if not need_more or not next_question:
                        break
                    follow_question = next_question

            if not all_results:
                continue

            answer = _with_fallback(
                lambda: ask_llm_for_answer(primary_conn.client, primary_model, question, all_results),
                (
                    None
                    if fallback_conn is None
                    else lambda: ask_llm_for_answer(
                        fallback_conn.client,
                        fallback_model,
                        question,
                        all_results,
                    )
                ),
                log,
            )
            llm_elapsed = time.perf_counter() - llm_start

            print("\nAnswer:")
            print(answer or "No answer returned.")
            print(f"\nLLM elapsed: {_format_elapsed(llm_elapsed)}")
            if last_explanation:
                print("\nSQL reasoning:")
                print(last_explanation)
            if last_sql:
                print("\nExecuted SQL:")
                print(last_sql)
    finally:
        if primary_conn is not None:
            primary_conn.close()
        if fallback_conn is not None:
            fallback_conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
