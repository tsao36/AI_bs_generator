from __future__ import annotations

import argparse
import base64
import os
import sys
from typing import Dict

import requests
from dotenv import load_dotenv


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip() or default


def _build_headers(mode: str, cookie: str) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "User-Agent": "ips-sso-read-probe/1.0",
        "Accept": "application/json, text/plain, */*",
    }

    if mode == "cookie":
        if not cookie:
            raise ValueError("Cookie mode requires --cookie or IPS_SSO_COOKIE in .env")
        headers["Cookie"] = cookie
        return headers

    if mode == "basic":
        user = _env_str("HSD_USERNAME", "")
        token = _env_str("HSD_API_TOKEN", "")
        if not user or not token:
            raise ValueError("Basic mode requires HSD_USERNAME and HSD_API_TOKEN in .env")
        raw = f"{user}:{token}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        return headers

    return headers


def _probe(url: str, headers: Dict[str, str], verify: object = True) -> int:
    try:
        resp = requests.get(url, headers=headers, timeout=30, verify=verify)
        body = (resp.text or "")[:400].replace("\r", " ").replace("\n", " ")
        print(f"PROBE {url}")
        print(f"HTTP {resp.status_code}")
        print(f"BODY_PREVIEW {body}")
        return int(resp.status_code)
    except Exception as exc:
        print(f"PROBE {url}")
        print(f"ERROR {exc}")
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only IPS/HSD SSO probe (no write operations).")
    parser.add_argument("--issue-id", default="14021278492", help="Sample issue/article id for read probe.")
    parser.add_argument(
        "--mode",
        choices=["cookie", "basic", "none"],
        default="cookie",
        help="Auth mode for probing. cookie=SSO cookie, basic=HSD user/token, none=no auth.",
    )
    parser.add_argument(
        "--cookie",
        default=_env_str("IPS_SSO_COOKIE", ""),
        help="Raw Cookie header value copied from logged-in browser session.",
    )
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification.")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    try:
        headers = _build_headers(args.mode, args.cookie)
    except ValueError as exc:
        print(f"CONFIG_ERROR {exc}")
        return 2

    verify: object = True
    if args.insecure:
        verify = False
    else:
        cert_path = _env_str("HSD_CA_CERT", "")
        if cert_path and os.path.exists(cert_path):
            verify = cert_path
    issue_id = str(args.issue_id).strip()

    api_url = (
        f"https://hsdes-api.intel.com/rest/auth/article/{issue_id}/"
        "history?fields=id%2Ctitle%2Cupdated_date%2Cowner%2Cstatus%2Cpriority"
    )
    web_url = f"https://hsdes.intel.com/appstore/article/#/{issue_id}"

    print("READ_ONLY_PROBE_START")
    print(f"MODE {args.mode}")
    code_api = _probe(api_url, headers, verify=verify)
    code_web = _probe(web_url, headers, verify=verify)

    ok_api = 200 <= code_api < 300
    ok_web = 200 <= code_web < 300
    if ok_api or ok_web:
        print("RESULT PASS")
        return 0

    print("RESULT FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
