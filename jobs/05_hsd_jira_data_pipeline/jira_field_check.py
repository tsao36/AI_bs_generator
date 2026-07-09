import os
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)

JIRA_SERVER = (os.getenv("JIRA_SERVER") or "https://jira.idoc.intel.com").rstrip("/")
JIRA_USER = (os.getenv("JIRA_USER") or "").strip()
JIRA_PASSWORD = (os.getenv("JIRA_PASSWORD") or "").strip()

if not JIRA_USER or not JIRA_PASSWORD:
    raise RuntimeError("Missing JIRA_USER/JIRA_PASSWORD in .env (match Wireless dashboard setup)")

auth = HTTPBasicAuth(JIRA_USER, JIRA_PASSWORD)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

issue_key = "WIFI-818038"
r = requests.get(
    f"{JIRA_SERVER}/rest/api/2/issue/{issue_key}?expand=names",
    auth=auth,
    timeout=30,
    verify=False,
)
r.raise_for_status()
j = r.json()
fields = j["fields"]
names = j.get("names", {})

# Example: print all custom fields present on this issue
for k, v in fields.items():
    if k.startswith("customfield_") and v is not None:
        print(f"{k} ({names.get(k, 'unknown label')}): {v}")
