"""
Pull Jira issue descriptions and log the first three words.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
from typing import Iterable, List, Tuple

import requests
import urllib3
from dotenv import load_dotenv

# Avoid noisy SSL warnings when using internal Jira
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


def read_credentials() -> Tuple[str, str, str]:
    server = (os.getenv("JIRA_SERVER") or "https://jira.idoc.intel.com").rstrip("/")
    user = (os.getenv("JIRA_USER") or "").strip()
    password = (os.getenv("JIRA_PASSWORD") or "").strip()
    if not user or not password:
        raise SystemExit("Missing JIRA_USER/JIRA_PASSWORD in .env")
    return server, user, password


def first_three_words(text: str) -> str:
    words = text.strip().split()
    return " ".join(words[:3]) if words else ""


def fetch_issues(server: str, auth: Tuple[str, str], jql: str, max_results: int) -> List[dict]:
    issues: List[dict] = []
    start_at = 0
    page_size = 100
    while len(issues) < max_results:
        fetch_size = min(page_size, max_results - len(issues))
        params = {
            "jql": jql,
            "fields": "summary,description",
            "startAt": start_at,
            "maxResults": fetch_size,
        }
        resp = requests.get(
            f"{server}/rest/api/2/search",
            params=params,
            auth=auth,
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        batch = payload.get("issues", [])
        issues.extend(batch)
        if len(batch) < fetch_size:
            break
        start_at += len(batch)
    return issues


def write_log(log_path: Path, jql: str, issues: Iterable[dict]) -> int:
    timestamp = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    count = 0
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"Run {timestamp} JQL: {jql}\n")
        for issue in issues:
            description = (issue.get("fields") or {}).get("description") or ""
            snippet = first_three_words(description) or "(no description)"
            fh.write(f"{issue.get('key')}: {snippet}\n")
            count += 1
        fh.write("\n")
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Log first three words of Jira descriptions")
    parser.add_argument(
        "--jql",
        default="project = WIRELESS ORDER BY updated DESC",
        help="JQL query to select issues (default pulls WIRELESS project)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=200,
        help="Maximum number of issues to fetch",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=BASE_DIR / "jira_description_first3.log",
        help="Log file path (defaults beside the script)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server, user, password = read_credentials()
    issues = fetch_issues(server, (user, password), args.jql, args.max)
    written = write_log(args.log, args.jql, issues)
    print(f"Wrote {written} issues to {args.log}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as exc:
        raise SystemExit(f"Jira request failed: {exc}") from exc
