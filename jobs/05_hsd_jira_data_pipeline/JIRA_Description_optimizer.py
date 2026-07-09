"""
WiFi project JIRA description optimizer (LLM-based)

Variation:
- Instead of only rewriting "Steps to reproduce" for PMs,
  this variant evaluates the existing description (everything before "account_name:")
  for how well it helps a DEVELOPER debug the issue, and reformats it into a
  canonical, uniform structure for all issues.

NOTE (modified):
- Removed the original WiFi-project batch/single-issue menu option from __main__.
- Kept ONLY the newly added capability to process "customer-found sightings"
  created in the last X days (created >= -Xd).
- At the end of processing:
    * print the Jira sighting keys (numbers)
    * print the reporter names (unique + per-issue mapping)
- Email notifications REMOVED.
"""

# -------------------------
# Imports and configuration
# -------------------------
import os
import re
import json
import urllib3
import logging
import difflib
import argparse  # <-- ADD

import json
import re
from typing import Any, Dict

def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()

def _extract_first_json_object(s: str) -> str:
    s = _strip_code_fences(s)
    start = s.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM response")
    
    # Find matching closing brace by counting nesting level
    depth = 0
    in_string = False
    escape_next = False
    
    for i in range(start, len(s)):
        char = s[i]
        
        if escape_next:
            escape_next = False
            continue
            
        if char == '\\':
            escape_next = True
            continue
            
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
            
        if not in_string:
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
    
    raise ValueError("No complete JSON object found in LLM response")

def _repair_invalid_json_escapes(s: str) -> str:
    # Replace invalid escapes like "\U" "\P" "\ " with "\\U" etc.
    # but keep valid JSON escapes: \" \\ \/ \b \f \n \r \t \uXXXX
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", s)

def safe_json_loads_llm(raw_text: str) -> Dict[str, Any]:
    candidate = _extract_first_json_object(raw_text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = _repair_invalid_json_escapes(candidate)
        return json.loads(repaired)


from typing import Optional, List, Dict, Any, Tuple, Set
from datetime import datetime, timezone, timedelta

import httpx
import openai
from jira import JIRA
from jira.resources import Issue
from jira.exceptions import JIRAError
from pathlib import Path
from dotenv import load_dotenv

# -------------------------
# Load .env (must be BEFORE any os.getenv usage)
# -------------------------
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

# -------------------------
# Logging setup
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("jira_wifi_description")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -------------------------
# Env helpers (reduce redundancy)
# -------------------------
_FALSE_STRS = {"0", "false", "no", "off", "n"}

def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip() or default

def env_bool(name: str, default: bool = True) -> bool:
    v = (os.getenv(name) or "").strip()
    if not v:
        return default
    return v.lower() not in _FALSE_STRS

def env_int(name: str, default: int) -> int:
    v = (os.getenv(name) or "").strip()
    try:
        return int(v) if v else default
    except Exception:
        return default

def env_float(name: str, default: float) -> float:
    v = (os.getenv(name) or "").strip()
    try:
        return float(v) if v else default
    except Exception:
        return default

# -------------------------
# JIRA endpoints and project definitions
# -------------------------
JIRA_TEST_SERVER = "https://jiratest.idoc.intel.com"
JIRA_SERVER = "https://jira.idoc.intel.com"
JIRA_ISSUETYPE_NAME = "Bug"

# -------------------------
# Credentials (from .env / environment)
# -------------------------
JIRA_USER = env_str("JIRA_USER")
JIRA_PASSWORD = env_str("JIRA_PASSWORD")

# -------------------------
# LLM / GNAI config (from .env / environment)
# -------------------------
# GNAI OpenAI-compatible provider endpoint (preferred):
#   https://gnai.intel.com/api/providers/openai/v1
# Backward compatibility: EXPERTGPT_* vars are still accepted as fallback.
GNAI_TOKEN = env_str("GNAI_TOKEN", env_str("EXPERTGPT_TOKEN"))
GNAI_URL = env_str(
    "GNAI_URL",
    env_str("EXPERTGPT_URL", "https://gnai.intel.com/api/providers/openai/v1"),
).rstrip("/")
MODEL = env_str("GNAI_MODEL", env_str("EXPERTGPT_MODEL", env_str("MODEL", "gpt-4.1")))
GNAI_CA_BUNDLE = env_str("GNAI_CA_BUNDLE", env_str("EXPERTGPT_CA_BUNDLE"))
GNAI_INSECURE = env_bool("GNAI_INSECURE", env_bool("EXPERTGPT_INSECURE", False))

# Fail fast if missing
if not JIRA_USER or not JIRA_PASSWORD:
    raise SystemExit("Missing JIRA_USER/JIRA_PASSWORD. Set them in .env or OS environment variables.")
if not GNAI_TOKEN:
    raise SystemExit("Missing GNAI_TOKEN (or EXPERTGPT_TOKEN fallback). Set it in .env or OS environment variables.")

# -------------------------
# Customer-found "sightings" search defaults
# -------------------------
SIGHTINGS_PROJECT_IDS = ["10110", "10000"]
SIGHTINGS_STATUSES = ["Open", "In Progress", "Pending"]
SIGHTINGS_FOUND_BY_VALUE = "Customer Found"
SIGHTINGS_TEAM_VALUE = "CAE"

# -------------------------
# Comment scanning + filtering
# -------------------------
COMMENTS_ENABLED = env_bool("JIRA_USE_COMMENTS", True)
COMMENTS_LOOKBACK_HOURS_DEFAULT = env_int("JIRA_COMMENTS_LOOKBACK_HOURS", 72)
COMMENTS_MAX_ITEMS_FETCH = env_int("JIRA_COMMENTS_MAX_ITEMS", 15)
COMMENTS_MAX_ITEMS_TO_LLM = env_int("JIRA_COMMENTS_MAX_ITEMS_TO_LLM", 10)
COMMENTS_MAX_CHARS_TOTAL = env_int("JIRA_COMMENTS_MAX_CHARS_TOTAL", 6000)

COMMENTS_LLM_FILTER_ENABLED = env_bool("JIRA_COMMENTS_LLM_FILTER", True)
COMMENTS_LLM_TEMPERATURE = env_float("JIRA_COMMENTS_LLM_TEMP", 0.0)

# token control: per-comment body cap (used in LLM usefulness call)
COMMENTS_LLM_BODY_MAX_CHARS = env_int("JIRA_COMMENTS_LLM_BODY_MAX_CHARS", 600)

COMMENT_NOISE_PATTERNS = [
    r"\bassigned\b",
    r"\breassigned\b",
    r"\btriage\b",
    r"\bduplicate\b",
    r"\bclosing\b",
    r"\bclosed\b",
    r"\bresolved\b",
    r"\bthanks\b",
    r"\bthank you\b",
    r"\bfyi\b",
    r"\bping\b",
    r"\bplease advise\b",
    r"\bany update\b",
    r"\beta\b",
    r"\bstatus\b",
    r"\bper our discussion\b",
]
_COMMENT_NOISE_RE = re.compile("|".join(COMMENT_NOISE_PATTERNS), re.IGNORECASE)

# -------------------------
# Summary update config
# -------------------------
JIRA_UPDATE_SUMMARY_ENABLED = env_bool("JIRA_UPDATE_SUMMARY", True)
JIRA_SUMMARY_MAX_CHARS = env_int("JIRA_SUMMARY_MAX_CHARS", 120)

# Summary gating thresholds (safe defaults)
JIRA_SUMMARY_MIN_SCORE = env_int("JIRA_SUMMARY_MIN_SCORE", 55)
JIRA_SUMMARY_MIN_IMPROVEMENT = env_int("JIRA_SUMMARY_MIN_IMPROVEMENT", 8)

# -------------------------
# Validation engineer domain context (trimmed to reduce token bloat)
# -------------------------
VALIDATION_ENGINEER_CONTEXT = """
Boot / Restart
- Cold Boot (CB): Power-off to boot (ACPI G3/S5). Full POST; HW initialized from scratch.
- Warm Boot (WB) / Restart: OS-initiated restart; power rails remain; some low-level init may be skipped.
- Warm Boot Cycle Test: repeated restarts to catch rare timing/init failures ("Heisenbugs").

System Power States (ACPI)
- S3: Suspend to RAM (RAM powered). Fast wake; data lost if battery dies.
- S4: Hibernate (RAM image to disk e.g. `hiberfil.sys`). Slower wake; data retained.
- MS: Modern Standby (OS-level experience), requires S0ix.
- S0ix: Low Power Idle substates (S0i1/S0i2/S0i3), SoC aggressively power-gated.

Device Power States
- D0: device fully on/active.
- D3hot: aux power present; context preserved; can respond to SW / wake events.
- D3cold: main power removed; context lost; requires full re-init.

Customer validation shorthand
- VP: Reproducible
- VNP: Not reproducible
- CND: Cannot determine

Test Method
- TIS: Total Isotropic Sensitivity
""".strip()

ACCOUNT_NAME_MARKER = "account_name:"  # keep lowercase

# -------------------------
# Jira custom fields (WiFi project)
# -------------------------
FOUND_IN_BUILD_FIELD = "customfield_10215"
PLATFORMS_FIELD = "customfield_10242"
TESTED_HARDWARE_FIELD = "customfield_10223"
OS_FIELD = "customfield_10277"

# For faster Jira search payloads
SEARCH_FIELDS = [
    "summary",
    "description",
    "attachment",
    "reporter",
    FOUND_IN_BUILD_FIELD,
    PLATFORMS_FIELD,
    TESTED_HARDWARE_FIELD,
    OS_FIELD,
]

_FREQ_VALUE_RE = re.compile(r"frequency\s*:\s*(.+)", re.IGNORECASE)

# -------------------------
# Helper functions
# -------------------------
def _account_marker_idx(desc: str) -> int:
    if not desc:
        return -1
    return desc.lower().find(ACCOUNT_NAME_MARKER)

def extract_existing_description(full_description: str) -> str:
    idx = _account_marker_idx(full_description)
    if idx == -1:
        return (full_description or "").strip()
    return (full_description[:idx] or "").strip()

# Build description as:
#   <AI optimized block>
#   ----- divider -----
#   <original full description>
def build_full_description_with_ai_and_original(
    ai_block: str,
    original_full_description: str,
    divider_line: str,
) -> str:
    ai_block = (ai_block or "").strip()
    original_full_description = (original_full_description or "").strip()

    if not ai_block:
        return original_full_description
    if not original_full_description:
        return ai_block

    return f"{ai_block}\n\n{divider_line}\n\n{original_full_description}".rstrip()

def classify_issue_type_from_key(issue_key: str) -> str:
    if not issue_key:
        return "Unknown"
    k = issue_key.upper()
    if k.startswith("WIFI-"):
        return "Wi-Fi"
    if k.startswith("BT-"):
        return "Bluetooth"
    return "Unknown"

def extract_option_values(field_val) -> List[str]:
    if field_val is None:
        return []
    items = field_val if isinstance(field_val, list) else [field_val]
    out: List[str] = []
    for it in items:
        v = getattr(it, "value", None)
        out.append(str(v) if v is not None else str(it))
    return out

def extract_frequency_value_from_description(full_description: str) -> Optional[str]:
    if not full_description:
        return None
    m = _FREQ_VALUE_RE.search(full_description)
    if not m:
        return None
    line = m.group(1).strip()
    newline_idx = line.find("\n")
    if newline_idx != -1:
        line = line[:newline_idx]
    for token in [
        " Account Billing",
        " Account ",
        " Customer ",
        " intel_support_owner",
        " contact_name",
        " contact_email",
    ]:
        idx = line.find(token)
        if idx != -1:
            line = line[:idx].strip()
            break
    line = line.strip(" .")
    return line or None

def _unified_diff(before: str, after: str, fromfile: str, tofile: str) -> str:
    before_lines = (before or "").splitlines(keepends=True)
    after_lines = (after or "").splitlines(keepends=True)
    diff_lines = difflib.unified_diff(before_lines, after_lines, fromfile=fromfile, tofile=tofile)
    out = "".join(diff_lines)
    return out.strip() or "(no diff)"

def _parse_jira_dt(dt_str: str) -> Optional[datetime]:
    """
    Jira often returns: 2025-12-14T01:23:45.678+0000
    """
    if not dt_str:
        return None
    try:
        if dt_str.endswith("Z"):
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if len(dt_str) >= 5 and (dt_str[-5] in ["+", "-"]) and dt_str[-2:].isdigit():
            dt_norm = dt_str[:-5] + dt_str[-5:-2] + ":" + dt_str[-2:]
            return datetime.fromisoformat(dt_norm)
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None

def _looks_like_noise_comment(body: str) -> bool:
    if not body or not body.strip():
        return True
    t = body.strip()
    if len(t) < 12:
        return True
    if _COMMENT_NOISE_RE.search(t) and len(t) < 200:
        return True
    return False

def _truncate_text(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip() + "\n...(truncated)"

def _display_name(user_obj: Any) -> str:
    return (
        getattr(user_obj, "displayName", None)
        or getattr(user_obj, "name", None)
        or getattr(user_obj, "key", None)
        or "Unknown"
    )

# -------------------------
# Summary helpers
# -------------------------
def _normalize_one_line(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def split_jira_summary_tags(summary: str) -> Tuple[str, str]:
    """
    Splits Jira summary into:
      - prefix: leading [TAG][TAG2]...
      - core: remaining human-readable summary text
    """
    if not summary:
        return ("", "")
    s = summary.strip()
    tags: List[str] = []
    while s.startswith("["):
        end = s.find("]")
        if end == -1:
            break
        tags.append(s[: end + 1])
        s = s[end + 1 :].lstrip()
    return ("".join(tags).strip(), s.strip())

def build_llm_existing_desc_input(existing_desc_before_account: str, jira_summary_full: str) -> str:
    """
    When description (before account_name:) is empty, still proceed:
    - Use Jira summary core (tags stripped) as seed input if available.
    - Otherwise provide a minimal placeholder so the LLM relies on metadata + useful comments.
    """
    if (existing_desc_before_account or "").strip():
        return existing_desc_before_account.strip()

    _, summary_core = split_jira_summary_tags(jira_summary_full or "")
    summary_core = (summary_core or "").strip()

    if summary_core:
        return (
            "NOTE: Original Jira description (before 'account_name:') was empty.\n"
            "Using Jira summary as seed input:\n"
            f"{summary_core}"
        ).strip()

    return (
        "NOTE: Jira description (before 'account_name:') is empty and Jira summary core is empty.\n"
        "Please generate the canonical description using ONLY the provided metadata and useful comments.\n"
        "If anything is missing, write 'Not specified' / 'Not provided' and do not guess."
    ).strip()

def looks_human_written_summary(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    if not (20 <= len(t) <= 160):
        return False

    lower = t.lower()
    if lower in ("issue", "problem", "bug", "customer issue", "wifi issue", "bt issue"):
        return False

    signal_terms = [
        "fails", "failure", "crash", "hang", "stuck",
        "disconnect", "drop", "timeout", "bsod",
        "cannot", "unable", "regression", "not detected",
        "won't", "doesn't", "does not",
    ]
    if not any(x in lower for x in signal_terms):
        return False
    if lower.startswith(("issue with", "problem with", "bug with")):
        return False
    if any(k in lower for k in ("pls", "please", "any update", "eta", "thanks")):
        return False

    return True

def score_summary_quality(text: str) -> int:
    """
    Score the quality of a Jira summary for WiFi/Bluetooth issues.
    
    Scoring System (0-100):
    ----------------------
    LENGTH (mutually exclusive):
      +20 points: Optimal length (28-120 chars) - concise but informative
      +12 points: Acceptable length (20-160 chars) - could be tighter
    
    SYMPTOM KEYWORDS (+30 points):
      Indicates concrete failure mode: fail, crash, hang, disconnect, timeout,
      bsod, "not detected", cannot, unable
      → Essential for actionable bug reports
    
    CONTEXT KEYWORDS (+20 points):
      Provides trigger/scenario: resume, sleep, s3, s4, s0ix, reboot, connect,
      scan, pair, unpair, roam
      → Helps developers understand repro conditions
    
    SCOPE IDENTIFICATION:
      +15 points: Contains "wifi" or "wi-fi"
      +15 points: Contains "bt" or "bluetooth" (word boundary)
      → Helps with routing and filtering
    
    PENALTIES (deductions for poor quality):
      -15 points: Starts with generic "issue", "problem", or "bug"
       -5 points: Contains vague phrases "doesn't work" or "not working"
    
    Examples:
      High score (75+): "WiFi disconnects after S3 resume on MTL platform"
      Medium score (50-74): "Bluetooth pairing fails on Windows 11"
      Low score (0-49): "Issue with WiFi" or "Connection doesn't work"
    
    Returns:
      int: Quality score clamped to range [0, 100]
    """
    if not text:
        return 0

    t = _normalize_one_line(text)
    lower = t.lower()
    score = 0

    # Length scoring (optimal vs acceptable)
    if 28 <= len(t) <= 120:
        score += 20
    elif 20 <= len(t) <= 160:
        score += 12

    # Symptom keywords (concrete failure indication)
    symptom = ["fail", "crash", "hang", "disconnect", "timeout", "bsod", "not detected", "cannot", "unable"]
    if any(s in lower for s in symptom):
        score += 30

    # Context keywords (repro scenario/trigger)
    ctx = ["resume", "sleep", "s3", "s4", "s0ix", "reboot", "connect", "scan", "pair", "unpair", "roam"]
    if any(c in lower for c in ctx):
        score += 20

    # Scope identification (WiFi vs Bluetooth)
    if "wifi" in lower or "wi-fi" in lower:
        score += 15
    if re.search(r"\b(bt|bluetooth)\b", lower):
        score += 15

    # Penalties for poor quality indicators
    if lower.startswith(("issue", "problem", "bug")):
        score -= 15
    if "doesn't work" in lower or "not working" in lower:
        score -= 5

    return max(0, min(100, score))

# -------------------------
# Comment usefulness filter system prompt
# -------------------------
COMMENT_USEFULNESS_SYSTEM_PROMPT = """
You are a senior Windows Wi-Fi/Bluetooth debugging engineer.
Your task is to decide which Jira comments are USEFUL for reproducing or debugging the issue.

A useful comment typically contains at least one of:
- New/clarified steps to reproduce
- OS/build/driver/platform/hardware details
- Exact error messages, crash signatures, logs pointers
- Frequency/repro rate details
- Environmental/test conditions (AP/BT device, power state transitions, etc.)
- Attachments/log references with concrete context

Not useful:
- Pure assignment/status updates ("assigned to", "triage", "ETA", "any update", "thanks")
- Coordination chatter without technical content

IMPORTANT JSON SAFETY RULES:
- Output must be valid JSON parsable by Python json.loads().
- Do NOT include Windows paths using backslashes (e.g., C:\\Users\\...). If you must write a path, use forward slashes (e.g., C:/Users/...) to avoid invalid escape sequences.
- Do NOT include unescaped backslashes in any string value.

Return STRICT JSON:
{
  "useful_ids": [<list of integer ids>],
  "notes": "<short reason/summary>"
}
No markdown, no extra fields.
""".strip()

# -------------------------
# Single-call Dev + Summary system prompt
# -------------------------
DEV_DESC_SYSTEM_PROMPT = f"""
You are a senior Windows Wi-Fi / Bluetooth debugging engineer helping to triage Jira bugs.
Your job is to turn a Jira description into a clean, canonical, developer-ready description AND a short Jira summary.

You also have the following background knowledge from a validation engineer about boot/power/device states.
Use it only as domain knowledge to interpret content accurately:

{VALIDATION_ENGINEER_CONTEXT}

INPUTS YOU RECEIVE:
- Jira description text (only the part before 'account_name:')
- Structured metadata from Jira fields
- Useful comments digest (filtered; may include late repro/log details)

OUTPUT REQUIREMENTS:
You MUST respond in STRICT JSON with this exact schema:

{{
  "suggested_description": "<canonical description>",
  "summary_one_liner": "<short Jira summary core (NO [tags])>"
}}

CANONICAL DESCRIPTION:
You MUST always produce a complete, canonical description in this exact section order and with these headings:

0) "Issue Summary:"
1) "Steps to Reproduce:"
2) "Test Data / Conditions:"
3) "Actual Result:"
4) "Expected Result:"
5) "Reproducibility:"
6) "Logs / Evidence:"
7) "Impact / Workaround:"

OPTIONAL SECTION (ONLY if there are conflicts / unknowns that matter for repro/debug):
8) "Conflicts / Open Questions:"

Rules:
- "Issue Summary:" 1–3 sentences, max 30 words. Must include observed failure + trigger/context + scope (Wi-Fi/BT) if available.
- "Steps to Reproduce:" must be a numbered list.
- Each heading must appear exactly once, in the required order.
- If information is missing, include heading and write "Not specified" / "Not provided".
- Preserve technical details (OS build, driver, platform, hardware, AP/BT device, logs).
- Resolve conflicts by preferring the most recent technically detailed comment. If still conflicting, DO NOT GUESS:
  include the conflict in section 8 as a bullet list.

SUMMARY ONE-LINER:
- Output in JSON field "summary_one_liner" MUST be ONLY the core summary text (NO [tags], NO brackets).
- One line, max 20 words, ideally <= 110 characters.
- Include symptom/failure + trigger/context + scope (Wi-Fi or BT) if available.
- Avoid generic filler like "issue", "problem", "doesn't work".
- Do NOT invent details; omit unknowns rather than guessing.
- Use simple language for verbs/descriptors.
- You may use identifiers found anywhere in the ticket inputs (desc/comments/metadata/summary).
- If the original tail already contains short/important identifiers, you may keep them unchanged.
- No extra labels or formatting.

No extra fields. No markdown. No explanations.
""".strip()

# -------------------------
# LLM helper
# -------------------------
class LLM_helper:
    def __init__(self) -> None:
        self._http: Optional[httpx.Client] = None
        self.client: Optional[openai.OpenAI] = None
        self.model = MODEL
        self.set_up()

    def set_up(self) -> None:
        verify: bool | str = True
        if GNAI_INSECURE:
            verify = False
        elif GNAI_CA_BUNDLE:
            verify = GNAI_CA_BUNDLE

        self._http = httpx.Client(proxy=None, verify=verify, trust_env=False)
        self.client = openai.OpenAI(
            api_key=GNAI_TOKEN,
            http_client=self._http,
            base_url=GNAI_URL,
        )

    def close(self) -> None:
        try:
            if self._http:
                self._http.close()
        except Exception:
            pass

    def _chat_json(self, *, system_prompt: str, user_prompt: str, temperature: float) -> Dict[str, Any]:
        if self.client is None:
            return {}
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
            )
            content = resp.choices[0].message.content or ""
            return safe_json_loads_llm(content)  # <-- changed
        except Exception as e:
            log.error("[ERROR] LLM JSON call failed: %s", e)
            return {}


    def filter_useful_comments(
        self,
        *,
        issue_key: str,
        existing_desc: str,
        comments: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], str]:
        if not comments:
            return ([], "No comments")

        compact = [
            {
                "id": int(c.get("id", 0)),
                "created": c.get("created", "") or "",
                "author": c.get("author", "") or "",
                "body": _truncate_text(c.get("body", "") or "", COMMENTS_LLM_BODY_MAX_CHARS),
            }
            for c in comments
        ]

        user_prompt = f"""
Issue key: {issue_key}

Existing description (before account_name:):
\"\"\"{_truncate_text(existing_desc, 2500)}\"\"\"

Candidate comments (each has an integer id):
{json.dumps(compact, ensure_ascii=False)}

Select ONLY comments that add concrete repro/debugging value.
""".strip()

        obj = self._chat_json(
            system_prompt=COMMENT_USEFULNESS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=COMMENTS_LLM_TEMPERATURE,
        )
        if not obj:
            return ([], "LLM error or non-JSON")

        useful_ids: Set[int] = set()
        for x in obj.get("useful_ids", []) or []:
            try:
                useful_ids.add(int(x))
            except Exception:
                pass

        notes = (obj.get("notes", "") or "").strip()
        useful = [c for c in comments if int(c.get("id", -1)) in useful_ids]
        return (useful, notes or "ok")

    def generate_description_and_summary(
        self,
        *,
        issue_key: str,
        existing_desc: str,
        meta: Dict[str, Any],
    ) -> Dict[str, str]:
        # Pull digest OUT of meta so it is not duplicated in meta_json
        meta_for_llm = dict(meta)
        comments_digest = (meta_for_llm.pop("useful_comments_digest", "") or "").strip()
        meta_json = json.dumps(meta_for_llm, ensure_ascii=False)

        existing_desc_trim = _truncate_text(existing_desc or "", 5000)

        comments_block = ""
        if comments_digest:
            comments_block = f"""

Useful Jira comments (filtered; may include late repro/log details added after creation):
\"\"\"{_truncate_text(comments_digest, COMMENTS_MAX_CHARS_TOTAL)}\"\"\""""

        user_prompt = f"""
Issue key: {issue_key}

Current Jira description (only BEFORE 'account_name:'):
\"\"\"{existing_desc_trim}\"\"\"{comments_block}

Structured metadata extracted from Jira fields (JSON):
{meta_json}

Very important:
- If 'frequency' is present in metadata, use it verbatim in 'Reproducibility:'.
- If 'has_attachments' is true, reflect that in 'Logs / Evidence:'.
""".strip()

        result = self._chat_json(
            system_prompt=DEV_DESC_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
        )
        if not result:
            return {"suggested_description": "", "summary_one_liner": ""}

        suggested = (result.get("suggested_description", "") or "").strip()
        summary_core = _normalize_one_line(result.get("summary_one_liner", "") or "")

        # Hard safety: strip any accidental brackets/tags the model might output
        summary_core = re.sub(r"^\s*(\[[^\]]+\]\s*)+", "", summary_core).strip()
        summary_core = summary_core.replace("\n", " ").strip()

        return {"suggested_description": suggested, "summary_one_liner": summary_core}

# -------------------------
# Jira processor
# -------------------------
class JiraWiFiDescriptionRewriter:
    AI_HEADER_LINE = "AI re-arranged information"
    AI_DIVIDER_LINE = "----- AI-generated section above; original description below -----"

    def __init__(self, is_test_server: bool = False):
        log.info("JiraWiFiDescriptionRewriter CTOR")
        self._jira_server_base = (JIRA_TEST_SERVER if is_test_server else JIRA_SERVER).rstrip("/")
        self.__jira: JIRA = self.__connect_to_jira(is_test_server)
        self._llm: LLM_helper = LLM_helper()
        self._updated_records: List[Dict[str, Any]] = []

    def close(self) -> None:
        try:
            self._llm.close()
        except Exception:
            pass
        try:
            self.__jira.close()
        except Exception:
            pass


    def __connect_to_jira(self, is_test_server: bool) -> JIRA:
        if not JIRA_USER or not JIRA_PASSWORD:
            raise SystemExit("JIRA_USER or JIRA_PASSWORD is empty. Edit the script and fill them in.")
        jira_server = JIRA_SERVER if not is_test_server else JIRA_TEST_SERVER
        jira_options = {"server": jira_server, "verify": False}
        try:
            jira = JIRA(options=jira_options, basic_auth=(JIRA_USER, JIRA_PASSWORD))
        except JIRAError as exp:
            raise SystemExit(f"failed to connect to jira, message={exp}") from exp
        log.info("authentication to JIRA succeeded, server: %s", jira_server)
        return jira

    def _issue_url(self, key: str) -> str:
        return f"{self._jira_server_base}/browse/{key}"

    @staticmethod
    def build_customer_found_sightings_jql(days: int) -> str:
        try:
            days_int = max(0, int(days or 0))
        except Exception:
            days_int = 0

        projects = ", ".join([f'"{p}"' for p in SIGHTINGS_PROJECT_IDS])
        statuses = ", ".join([f'"{s}"' for s in SIGHTINGS_STATUSES])

        return (
            f"project in ({projects}) "
            f'AND issuetype = "{JIRA_ISSUETYPE_NAME}" '
            f"AND status in ({statuses}) "
            f'AND "Found by" = "{SIGHTINGS_FOUND_BY_VALUE}" '
            f'AND Team = "{SIGHTINGS_TEAM_VALUE}" '
            f"AND created >= -{days_int}d"
        )

    def _is_llm_modified_description(self, description: str) -> bool:
        if not description:
            return False
        stripped = description.lstrip()
        if stripped.lower().startswith(self.AI_HEADER_LINE.lower()):
            return True
        if self.AI_DIVIDER_LINE.lower() in description.lower():
            return True
        return False

    def _looks_like_canonical_dev_description(self, text: str) -> bool:
        if not text:
            return False
        lower = text.lower()
        expected = [
            "issue summary:",
            "steps to reproduce:",
            "test data / conditions:",
            "actual result:",
            "expected result:",
            "reproducibility:",
            "logs / evidence:",
            "impact / workaround:",
        ]
        pos = -1
        for h in expected:
            idx = lower.find(h, pos + 1)
            if idx == -1:
                return False
            pos = idx
        return True

    def _record_successful_update(
        self,
        *,
        issue: Issue,
        before_desc: str,
        after_desc: str,
        before_summary: str,
        after_summary: str,
        mode: str,
    ) -> None:
        url = self._issue_url(issue.key)
        desc_diff = _unified_diff(
            before_desc, after_desc,
            fromfile=f"{issue.key}-desc-before",
            tofile=f"{issue.key}-desc-after",
        )

        _, before_core = split_jira_summary_tags(before_summary or "")
        _, after_core = split_jira_summary_tags(after_summary or "")
        summary_diff = _unified_diff(
            before_core, after_core,
            fromfile=f"{issue.key}-summary-before",
            tofile=f"{issue.key}-summary-after",
        )

        self._updated_records.append(
            {
                "key": issue.key,
                "url": url,
                "before": before_desc,
                "after": after_desc,
                "diff": desc_diff,
                "summary_before": before_summary or "",
                "summary_after": after_summary or "",
                "summary_diff": summary_diff,
                "mode": mode,  # LIVE or DRYRUN
            }
        )

    def _decide_updated_summary(self, *, issue: Issue, proposed_core: str) -> Optional[str]:
        if not JIRA_UPDATE_SUMMARY_ENABLED:
            return None

        current_summary = issue.fields.summary or ""
        tag_prefix, current_core = split_jira_summary_tags(current_summary)

        proposed_core = _normalize_one_line(proposed_core)
        if not proposed_core or len(proposed_core) < 15:
            log.info("[%s] Skip summary update: proposed summary too weak/short", issue.key)
            return None

        proposed_core = re.sub(r"^\s*(\[[^\]]+\]\s*)+", "", proposed_core).strip()

        old_score = score_summary_quality(current_core)
        new_score = score_summary_quality(proposed_core)
        print(f"Summary quality score: old={old_score}, new={new_score}")

        if new_score < JIRA_SUMMARY_MIN_SCORE:
            log.info("[%s] Skip summary update: proposed score below threshold (%d < %d)", issue.key, new_score, JIRA_SUMMARY_MIN_SCORE)
            return None

        if looks_human_written_summary(current_core):
            if (new_score - old_score) < max(12, JIRA_SUMMARY_MIN_IMPROVEMENT):
                log.info("[%s] Skip summary update: existing summary looks high-quality human-written", issue.key)
                return None

        if (new_score - old_score) < JIRA_SUMMARY_MIN_IMPROVEMENT:
            log.info("[%s] Skip summary update: improvement too small (%d)", issue.key, (new_score - old_score))
            return None

        if len(proposed_core) > JIRA_SUMMARY_MAX_CHARS:
            proposed_core = proposed_core[: JIRA_SUMMARY_MAX_CHARS - 1].rstrip() + "…"

        return f"{tag_prefix} {proposed_core}".strip()

    def _fetch_issue_comments(self, issue: Issue) -> List[Dict[str, Any]]:
        try:
            comments = self.__jira.comments(issue)
        except Exception:
            comments = []

        out: List[Dict[str, Any]] = []
        for idx, c in enumerate(comments):
            author = getattr(getattr(c, "author", None), "displayName", "") or ""

            raw_id = getattr(c, "id", None)
            try:
                cid = int(raw_id) if raw_id is not None else (idx + 1)
            except Exception:
                cid = idx + 1

            out.append(
                {
                    "id": cid,  # stable
                    "created": getattr(c, "created", "") or "",
                    "author": author,
                    "body": getattr(c, "body", "") or "",
                }
            )
        return out

    def _select_recent_comments(self, comments: List[Dict[str, Any]], lookback_hours: int) -> List[Dict[str, Any]]:
        if not comments:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0, int(lookback_hours or 0)))

        enriched = [( _parse_jira_dt(c.get("created", "") or ""), c) for c in comments]
        enriched.sort(key=lambda x: (x[0] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)

        picked: List[Dict[str, Any]] = []
        for dt, c in enriched:
            if dt is None:
                continue
            if dt >= cutoff:
                picked.append(c)
            if len(picked) >= COMMENTS_MAX_ITEMS_FETCH:
                break
        return picked

    def _build_useful_comments_digest(self, issue: Issue, existing_desc: str) -> Tuple[str, str]:
        if not COMMENTS_ENABLED:
            return ("", "COMMENTS_ENABLED=0")

        all_comments = self._fetch_issue_comments(issue)
        recent = self._select_recent_comments(all_comments, COMMENTS_LOOKBACK_HOURS_DEFAULT)
        if not recent:
            return ("", "No recent comments")

        candidates = [c for c in recent if not _looks_like_noise_comment(c.get("body", ""))]
        candidates = candidates[: max(1, COMMENTS_MAX_ITEMS_TO_LLM)]
        if not candidates:
            return ("", "All recent comments looked like noise by heuristic")

        useful = candidates
        notes = "heuristic-only"
        if COMMENTS_LLM_FILTER_ENABLED:
            useful, notes = self._llm.filter_useful_comments(
                issue_key=issue.key,
                existing_desc=existing_desc,
                comments=candidates,
            )
            if not useful:
                return ("", f"LLM selected 0 useful comments. Notes: {notes}")

        parts: List[str] = []
        total = 0
        for c in useful:
            created = c.get("created", "") or ""
            author = c.get("author", "") or "Unknown author"
            body = (c.get("body", "") or "").strip()
            block = _truncate_text(f"[{created}] {author}:\n{body}", 2000)

            if total + len(block) + 2 > COMMENTS_MAX_CHARS_TOTAL:
                remaining = COMMENTS_MAX_CHARS_TOTAL - total - 2
                if remaining > 200:
                    parts.append(_truncate_text(block, remaining))
                break

            parts.append(block)
            total += len(block) + 2

        return ("\n\n".join(parts).strip(), notes)

    def _process_issue_dev_quality(self, issue: Issue, dry_run: bool, auto_apply: bool) -> None:

        full_desc = issue.fields.description or ""
        existing_desc = extract_existing_description(full_desc)
        current_summary_full = issue.fields.summary or ""

        has_account_marker = _account_marker_idx(full_desc) != -1
        tag_prefix, current_summary_core = split_jira_summary_tags(current_summary_full)

        llm_existing_desc_input = build_llm_existing_desc_input(existing_desc, current_summary_full)

        print("=" * 80)
        print(f"Issue: {issue.key}")
        print(f"Link : {self._issue_url(issue.key)}")
        print("-" * 80)

        if self._is_llm_modified_description(full_desc) or self._looks_like_canonical_dev_description(existing_desc):
            print("Description already appears to be AI-optimized. Skipping this issue.\n")
            return

        if not (existing_desc or "").strip():
            if has_account_marker:
                print("Existing description (before 'account_name:') is EMPTY, but 'account_name:' marker exists.")
            else:
                print("Description is EMPTY (no 'account_name:' marker).")
            print("Proceeding: will use metadata + useful comments + Jira summary (as seed input when available).\n")

        issue_type = classify_issue_type_from_key(issue.key)

        driver_info = getattr(issue.fields, FOUND_IN_BUILD_FIELD, None)
        driver_info_str = _truncate_text(str(driver_info).strip() if driver_info is not None else "", 200).replace("\n", " ")

        platforms_str = ", ".join(extract_option_values(getattr(issue.fields, PLATFORMS_FIELD, None)))
        hw_str = ", ".join(extract_option_values(getattr(issue.fields, TESTED_HARDWARE_FIELD, None)))
        os_str = ", ".join(extract_option_values(getattr(issue.fields, OS_FIELD, None)))

        freq_value = extract_frequency_value_from_description(full_desc)
        freq_str = freq_value or "Not specified"

        attachments = getattr(issue.fields, "attachment", []) or []
        has_attachments = bool(attachments)
        attachment_names = [getattr(a, "filename", str(a)) for a in attachments][:10]
        logs_evidence_hint = "Jira attachments present" if has_attachments else "No Jira attachments"

        meta: Dict[str, Any] = {
            "issue_key": issue.key,
            "issue_type": issue_type,
            "driver_info": driver_info_str,
            "platforms": platforms_str,
            "hardware": hw_str,
            "os": os_str,
            "frequency": freq_value or "",
            "logs_evidence": logs_evidence_hint,
            "has_attachments": has_attachments,
            "attachment_names": attachment_names,
            "attachment_count": len(attachments),
            "jira_summary_core": current_summary_core,
            "existing_desc_empty": (not bool((existing_desc or "").strip())),
            "has_account_name_marker": has_account_marker,
        }

        need_comments = COMMENTS_ENABLED and ((not (existing_desc or "").strip()) or (len(existing_desc.strip()) < 400))
        if need_comments:
            useful_digest, digest_notes = self._build_useful_comments_digest(issue, llm_existing_desc_input)
        else:
            useful_digest, digest_notes = ("", "Skipped comment fetch (description sufficient)")

        meta["useful_comments_digest"] = useful_digest or ""

        if useful_digest:
            print("Useful comments selected for LLM input:")
            print(_truncate_text(useful_digest, 2500))
            print(f"[Comment filter notes] {digest_notes}")
        else:
            print(f"No useful comments added. [Comment filter notes] {digest_notes}")
        print("-" * 80)

        print("Current description (before 'account_name:'):\n")
        print(existing_desc if (existing_desc or "").strip() else "(empty)")
        if not (existing_desc or "").strip():
            print("\n[Seed input to LLM (summary/meta/comments pipeline)]:")
            print(llm_existing_desc_input)
        print("-" * 80)

        print("Metadata extracted from Jira fields:")
        print(f"  Issue type:     {issue_type}")
        print(f"  Driver / build: {driver_info_str or 'N/A'}")
        print(f"  Platform(s):    {platforms_str or 'N/A'}")
        print(f"  Hardware:       {hw_str or 'N/A'}")
        print(f"  OS:             {os_str or 'N/A'}")
        print(f"  Frequency:      {freq_str}")
        print(f"  Attachments?:   {'YES' if has_attachments else 'NO'}")
        if has_attachments:
            print("  Attachment names:", ", ".join(attachment_names))
            if len(attachments) > len(attachment_names):
                print(f"  (and {len(attachments) - len(attachment_names)} more)")
        if current_summary_core:
            print(f"  Summary core:   {current_summary_core}")
        print("-" * 80)

        llm_out = self._llm.generate_description_and_summary(
            issue_key=issue.key,
            existing_desc=llm_existing_desc_input,
            meta=meta,
        )
        suggested = (llm_out.get("suggested_description", "") or "").strip()
        proposed_summary_core = (llm_out.get("summary_one_liner", "") or "").strip()

        if not suggested:
            print("→ LLM did not provide a structured suggested_description (empty). Skipping update for safety.\n")
            return

        if not self._looks_like_canonical_dev_description(suggested):
            print("→ LLM output missing required canonical headings. Skipping update for safety.\n")
            return

        print("Suggested improved description (before 'account_name:'):\n")
        print(suggested)
        print("-" * 80)

        print("Current Jira summary:")
        print(f"  Tags : {tag_prefix or '(none)'}")
        print(f"  Core : {current_summary_core or '(empty)'}")
        print("-" * 80)

        print("Proposed Jira summary core (LLM, no tags):")
        print(f"  {proposed_summary_core or '(empty)'}")
        print("-" * 80)

        print("--- SUMMARY DIFF (core only) ---")
        print(_unified_diff(current_summary_core, proposed_summary_core, fromfile="summary-old-core", tofile="summary-new-core"))
        print("-" * 80)

        final_summary = self._decide_updated_summary(issue=issue, proposed_core=proposed_summary_core)

        if final_summary:
            print("Final Jira summary to apply (tags preserved):")
            print(f"  {final_summary}")
        else:
            print("Jira summary will NOT be updated (safety gating).")
        print("-" * 80)

        if dry_run:
            after_summary = final_summary or current_summary_full
            self._record_successful_update(
                issue=issue,
                before_desc=existing_desc.strip(),
                after_desc=suggested.strip(),
                before_summary=current_summary_full,
                after_summary=after_summary,
                mode="DRYRUN",
            )
            print("DRY RUN: No changes applied to JIRA. Recorded proposed updates.\n")
            return

        if auto_apply:
            choice = "y"
            print(f"AUTO-APPLY: applying updates to issue {issue.key} without prompt.")
        else:
            while True:
                choice = input(f"Apply updates to issue {issue.key}? [y]es / [n]o: ").strip().lower()
                if choice in {"y", "yes", "n", "no"}:
                    break
                print("Please type 'y' or 'n'.")

        if choice in {"n", "no"}:
            print("→ User chose NOT to update this issue.\n")
            return

        header_lines = [
            self.AI_HEADER_LINE,
            "developer-ready description based on Jira fields + useful comments",
            "--------------------------------------",
        ]

        meta_summary_lines = []
        if issue_type and issue_type != "Unknown":
            meta_summary_lines.append(f"Issue type: {issue_type}")
        if driver_info_str:
            meta_summary_lines.append(f"Driver / build: {driver_info_str}")
        if platforms_str:
            meta_summary_lines.append(f"Platform(s): {platforms_str}")
        if hw_str:
            meta_summary_lines.append(f"Tested hardware: {hw_str}")
        if os_str:
            meta_summary_lines.append(f"Operating system(s): {os_str}")

        if meta_summary_lines:
            header_lines.append("\n".join(meta_summary_lines))
        header_lines.append("")

        ai_block = "\n".join(header_lines).rstrip() + "\n\n" + suggested.strip()

        new_full_desc = build_full_description_with_ai_and_original(
            ai_block=ai_block,
            original_full_description=full_desc,
            divider_line=self.AI_DIVIDER_LINE,
        )

        print("Final full Jira description that will be saved:\n")
        print(new_full_desc)
        print("-" * 80)

        fields_to_update: Dict[str, Any] = {"description": new_full_desc}
        if final_summary:
            fields_to_update["summary"] = final_summary

        try:
            issue.update(fields=fields_to_update)
            log.info("[%s] fields updated successfully (%s)", issue.key, ", ".join(fields_to_update.keys()))
            if "summary" in fields_to_update:
                print(f"✔ Summary updated for issue {issue.key}.")
            print(f"✔ Description updated for issue {issue.key}.\n")

            self._record_successful_update(
                issue=issue,
                before_desc=existing_desc.strip(),
                after_desc=suggested.strip(),
                before_summary=current_summary_full,
                after_summary=(final_summary or current_summary_full),
                mode="LIVE",
            )

        except JIRAError as exp:
            log.error("Failed to update fields for %s: %s", issue.key, str(exp))
            print(f"✖ Failed to update issue {issue.key}: {exp}\n")

    def review_customer_found_sightings_last_days(
        self,
        dry_run: bool = True,
        days: int = 4,
        limit: Optional[int] = None,
        auto_apply: bool = False,
) -> None:

        self._updated_records = []

        jql = self.build_customer_found_sightings_jql(days)
        log.info("Running JQL for customer-found sightings (last %dd): %s", int(days), jql)

        max_results = False if limit is None else limit
        try:
            issues_result = self.__jira.search_issues(
                jql,
                maxResults=max_results,
                fields=",".join(SEARCH_FIELDS),
            )
        except JIRAError as exp:
            log.error("Failed to search sightings with JQL: %s", exp)
            return

        issues = list(issues_result)
        log.info("Found %d sightings for last %d days", len(issues), int(days))

        processed_count = 0
        sighting_keys: List[str] = []
        sighting_reporters: Dict[str, str] = {}
        unique_reporters: Set[str] = set()

        for issue in issues:
            processed_count += 1
            sighting_keys.append(issue.key)

            rep_name = _display_name(getattr(issue.fields, "reporter", None))
            sighting_reporters[issue.key] = rep_name
            unique_reporters.add(rep_name)

            self._process_issue_dev_quality(issue, dry_run=dry_run, auto_apply=auto_apply)


        log.info("Processed %d sightings (dev-quality flow)", processed_count)

        print("\n" + "=" * 80)
        print(f"SUMMARY: Customer-found sightings created in last {int(days)} day(s)")
        print(f"Total matched by JQL: {len(issues)}")
        print(f"Processed (iterated): {processed_count}")
        print("Sighting keys to share with owners:")
        print(", ".join(sighting_keys) if sighting_keys else "(none)")

        print("\nSighting keys + reporters:")
        if sighting_keys:
            for k in sighting_keys:
                print(f"  {k}: {sighting_reporters.get(k, 'Unknown')}")
        else:
            print("  (none)")

        print("\nReporter names (unique):")
        print(", ".join(sorted(unique_reporters)) if unique_reporters else "(none)")
        print("=" * 80 + "\n")

# -------------------------
# __main__
# -------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WiFi project JIRA description optimizer (customer-found sightings).")
    parser.add_argument("--live", action="store_true", help="Apply changes to JIRA (default: DRY RUN).")
    parser.add_argument("--yes", "--auto-apply", dest="auto_apply", action="store_true",
                        help="In LIVE mode, apply updates without per-issue confirmation prompt.")
    parser.add_argument("--days", type=int, default=4, help="Process sightings created in the last N days (default: 4).")
    parser.add_argument("--limit", type=int, default=50, help="Max issues to process (default: 50). Use 0 for ALL.")
    parser.add_argument("--test-server", action="store_true", help="Use JIRA test server instead of production.")

    args = parser.parse_args()

    dry_run = not args.live
    days = max(0, int(args.days or 0))
    limit = None if (args.limit is not None and int(args.limit) <= 0) else int(args.limit)
    auto_apply = bool(args.auto_apply)

    if dry_run:
        log.info("Running in DRY RUN mode: no changes will be applied to JIRA.")
    else:
        log.info("Running in LIVE mode: descriptions WILL be updated in JIRA.")
        if not auto_apply:
            log.warning("LIVE mode without --yes: script will still prompt per issue (human intervention required).")

    jira_runner = JiraWiFiDescriptionRewriter(is_test_server=args.test_server)
    try:
        jira_runner.review_customer_found_sightings_last_days(
            dry_run=dry_run,
            days=days,
            limit=limit,
            auto_apply=auto_apply,
        )
    finally:
        jira_runner.close()

    log.info("done!")
