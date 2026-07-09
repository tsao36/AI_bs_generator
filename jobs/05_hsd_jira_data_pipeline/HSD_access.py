"""
HSD (Hardware & Software Database) Bug Access Script

This script provides access to Intel's HSD bug tracking database.
Supports multiple access methods: REST API, ODBC, web automation, or CSV import.

Requirements:
    pip install requests requests-kerberos python-dotenv pandas selenium webdriver-manager

Environment Variables (.env):
    HSD_USERNAME=your_idsid
    HSD_PASSWORD=your_intel_password
    KRB5CCNAME=path_to_cached_ticket (optional)
    HTTP_PROXY=http://proxy-chain.intel.com:911
    HTTPS_PROXY=http://proxy-chain.intel.com:911
    
NOTE: Can fully automate CSV export from HSD web interface using Selenium
      if API authentication is not available.
"""

from __future__ import annotations

import os
import sys
import json
import base64
import logging
import argparse
import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from requests_kerberos import HTTPKerberosAuth, OPTIONAL
from dotenv import load_dotenv
import urllib3

# Suppress SSL warnings for internal Intel services
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load environment variables
load_dotenv()
_script_env = Path(__file__).resolve().parent / ".env"
if _script_env.exists():
    load_dotenv(_script_env)


import time
import glob

# Ensure MIT Kerberos is on PATH for Windows
_bootstrap_log = logging.getLogger("hsd_access")


def _ensure_mit_kerberos_path() -> None:
    kerberos_bin = r"C:\Program Files\MIT\Kerberos\bin"
    current_path = os.environ.get("PATH", "")
    if kerberos_bin.lower() not in current_path.lower().split(";"):
        os.environ["PATH"] = f"{kerberos_bin};{current_path}" if current_path else kerberos_bin
        _bootstrap_log.debug("Added MIT Kerberos to PATH: %s", kerberos_bin)


_ensure_mit_kerberos_path()
# -------------------------
# Configuration
# -------------------------
# HSDes web interface and API URLs
HSD_WEB_BASE = (os.getenv("HSD_WEB_BASE") or "https://hsdes.intel.com").rstrip("/")
HSD_API_URL = (os.getenv("HSD_API_URL") or "https://hsdes-api.intel.com/rest/").rstrip("/")
HSD_USERNAME = (os.getenv("HSD_USERNAME") or "").strip()
HSD_PASSWORD = (os.getenv("HSD_PASSWORD") or "").strip()
HSD_API_TOKEN = (os.getenv("HSD_API_TOKEN") or "").strip()
HSD_CA_CERT = (os.getenv("HSD_CA_CERT") or os.getenv("REQUESTS_CA_BUNDLE") or "").strip()
OWNER_FILTER = {
    owner.strip().lower()
    for owner in (os.getenv("HSD_OWNER_FILTER") or "yaochien,timdaway").split(",")
    if owner.strip()
}

# Database connection (alternative to API)
HSD_DB_SERVER = (os.getenv("HSD_DB_SERVER") or "").strip()
HSD_DB_NAME = (os.getenv("HSD_DB_NAME") or "HSDES").strip()
HSD_DB_USER = (os.getenv("HSD_DB_USER") or "").strip()
HSD_DB_PASS = (os.getenv("HSD_DB_PASS") or "").strip()

DEFAULT_TIMEOUT = 30
DEFAULT_QUERY_ID = "16021056445"


def _coerce_int(value: Optional[str], default: int) -> int:
    """Convert environment string to int with fallback."""
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


# Customer engineering Postgres target (for inserting filtered HSD bugs)
CUSTOMER_DB_HOST = (os.getenv("DB_HOST") or os.getenv("CUSTOMER_DB_HOST") or "").strip()
CUSTOMER_DB_NAME = (os.getenv("DB_NAME") or os.getenv("CUSTOMER_DB_NAME") or "").strip()
CUSTOMER_DB_USER = (os.getenv("DB_USER") or os.getenv("CUSTOMER_DB_USER") or "").strip()
CUSTOMER_DB_PASS = (os.getenv("DB_PASS") or os.getenv("CUSTOMER_DB_PASS") or "").strip()
CUSTOMER_DB_PORT = _coerce_int((os.getenv("DB_PORT") or os.getenv("CUSTOMER_DB_PORT") or "").strip(), 5432)
CUSTOMER_DB_SSLMODE = (os.getenv("DB_SSLMODE") or os.getenv("CUSTOMER_DB_SSLMODE") or "require").strip() or "require"
DEFAULT_CUSTOMER_DB_TABLE = (os.getenv("HSD_TARGET_TABLE") or os.getenv("CUSTOMER_DB_TABLE") or "").strip()

_TABLE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


# -------------------------
# Logging Setup
# -------------------------
def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging with both console and file output."""
    logger = logging.getLogger("hsd_access")
    logger.setLevel(logging.DEBUG)
    
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(getattr(logging, level.upper(), logging.INFO))
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    # File handler
    fh = logging.FileHandler("hsd_access.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    return logger


log = setup_logging()


def resolve_ca_bundle(verify: Optional[str | bool]) -> bool | str:
    """Resolve CA bundle path or fallback to disabled verification."""
    if verify is not None:
        if isinstance(verify, bool):
            return verify
        return str(Path(str(verify)).expanduser())

    if HSD_CA_CERT:
        path = Path(HSD_CA_CERT).expanduser()
        if path.exists():
            return str(path)
        return HSD_CA_CERT

    ssl_cert = os.getenv("SSL_CERT_FILE")
    if ssl_cert:
        return str(Path(ssl_cert).expanduser())

    return False


def filter_bugs_by_owner(bugs: List["HsdBug"]) -> List["HsdBug"]:
    """Keep only bugs whose owner matches the configured filter."""
    if not OWNER_FILTER:
        return bugs

    filtered: List[HsdBug] = []
    for bug in bugs:
        owner_value = (bug.owner or "").strip().lower()
        if owner_value in OWNER_FILTER:
            filtered.append(bug)

    dropped = len(bugs) - len(filtered)
    if dropped:
        log.info(
            "Filtered out %d bug(s) whose owner is not in %s",
            dropped,
            ", ".join(sorted(OWNER_FILTER)),
        )
    return filtered


def _format_bug_table(bugs: List["HsdBug"]) -> str:
    headers = ["ID", "Title", "Promoted ID", "Customer", "Submitted", "Days Open"]
    rows: List[List[str]] = []
    for bug in bugs:
        rows.append([
            bug.bug_id,
            bug.title,
            bug.promoted_id,
            bug.customer,
            bug.submitted_date or bug.created_date,
            str(bug.days_open if bug.days_open is not None else ""),
        ])

    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell or ""))

    def fmt_row(cells: List[str]) -> str:
        return " | ".join((cell or "").ljust(widths[idx]) for idx, cell in enumerate(cells))

    lines = [fmt_row(headers), "-+-".join("-" * w for w in widths)]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


def _parse_hsd_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None

    formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            return dt
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        log.debug("Unable to parse date '%s'", value)
        return None


def _calculate_days_open(created_date: str, resolved_date: str) -> Optional[int]:
    created_dt = _parse_hsd_date(created_date)
    if not created_dt:
        return None
    end_dt = _parse_hsd_date(resolved_date) or datetime.now(created_dt.tzinfo)
    delta = end_dt - created_dt
    return max(delta.days, 0)


# -------------------------
# Data Models
# -------------------------
@dataclass
class HsdBug:
    """Represents an HSD bug/issue."""
    bug_id: str
    title: str
    status: str
    severity: str
    priority: str
    component: str
    owner: str
    submitter: str
    created_date: str
    modified_date: str
    status_reason: str = ""
    tenant: str = ""
    subject: str = ""
    description: str = ""
    resolved_date: str = ""
    promoted_id: str = ""
    customer: str = ""
    submitted_date: str = ""
    days_open: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


# -------------------------
# HSD API Client
# -------------------------
class HsdApiClient:
    """Client for accessing HSD via REST API."""
    
    def __init__(self, api_url: str = HSD_API_URL, username: str = HSD_USERNAME, 
                 password: str = HSD_PASSWORD, token: Optional[str] = None,
                 verify: Optional[str | bool] = None, auth_mode: Optional[str] = None,
                 kerberos_cache: Optional[str] = None, mutual_authentication: int = OPTIONAL):
        """
        Initialize HSD API client.
        
        Args:
            api_url: Base URL for HSD REST API
            username: HSD username
            password: HSD password
            token: Raw HSD API token (if provided directly)
            verify: CA bundle path or bool for SSL verification
            auth_mode: "token", "kerberos", or None to auto-detect
            mutual_authentication: Kerberos mutual auth policy
        """
        self.api_url = api_url
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.kerberos_cache = kerberos_cache
        self._auth_success_logged = False

        self.token = token

        self.verify = resolve_ca_bundle(verify)
        self.session.verify = self.verify
        if self.verify is False:
            # Avoid env vars re-enabling verification when insecure mode is requested
            self.session.trust_env = False

        requested_mode = (auth_mode or "auto").strip().lower()
        if requested_mode == "auto":
            if self.token and self.username:
                self.auth_mode = "token"
                log.info("HSD token detected; using token authentication")
                self._configure_token_auth()
            elif self.username and self.password:
                self.auth_mode = "basic"
                self._configure_basic_password_auth()
            else:
                self.auth_mode = "kerberos"
                self._configure_kerberos(mutual_authentication)
        elif requested_mode == "token":
            self.auth_mode = "token"
            if not self.token:
                raise ValueError("Token authentication selected but no token was found.")
            if not self.username:
                raise ValueError("HSD_USERNAME is required for token authentication.")
            self._configure_token_auth()
        else:
            self.auth_mode = "kerberos"
            self._configure_kerberos(mutual_authentication)
        log.info("HSD authentication mode in use: %s", self.auth_mode)
    
    def __del__(self):
        """Cleanup session on destruction."""
        try:
            self.session.close()
        except Exception:
            pass

    def _configure_token_auth(self) -> None:
        """Configure session for token-based (Basic) authentication."""
        auth_blob = f"{self.username}:{self.token}".encode("utf-8")
        encoded = base64.b64encode(auth_blob).decode("ascii")
        self.session.auth = None
        self.session.headers["Authorization"] = f"Basic {encoded}"

        log.info("Using HSD token authentication (token supplied via env/CLI)")

        if self.verify is False:
            log.warning("SSL verification disabled for HSD API requests; set HSD_CA_CERT to enable.")

    def _configure_basic_password_auth(self) -> None:
        """Configure session for username/password Basic authentication."""
        self.session.auth = HTTPBasicAuth(self.username, self.password)
        if "Authorization" in self.session.headers:
            self.session.headers.pop("Authorization", None)
        log.info("Using Basic authentication with HSD_USERNAME and HSD_PASSWORD")
        if self.verify is False:
            log.warning("SSL verification disabled for HSD API requests; set HSD_CA_CERT to enable.")

    def _configure_kerberos(self, mutual_authentication: int) -> None:
        """Configure session for Kerberos authentication."""
        cache_in_use = self._prepare_kerberos_cache()
        if cache_in_use:
            log.info("Kerberos credential cache in use: %s", cache_in_use)
        else:
            log.info("Kerberos credential cache in use: system default")
        self.session.auth = HTTPKerberosAuth(
            mutual_authentication=mutual_authentication,
            force_preemptive=True,
        )
        if cache_in_use:
            log.info("Using Kerberos authentication with cache %s", cache_in_use)
        else:
            log.info("Using Kerberos authentication; ensure 'kinit' has been run and KRB5CCNAME is set.")
        if self.token:
            log.debug("Kerberos mode selected; ignoring loaded token.")
        if self.verify is False:
            log.warning("SSL verification disabled for HSD API requests; set HSD_CA_CERT to enable.")

    def _prepare_kerberos_cache(self) -> Optional[str]:
        """Set KRB5CCNAME if requested and return the cache path in use."""
        if self.kerberos_cache:
            cache_path = str(Path(self.kerberos_cache).expanduser())
            os.environ["KRB5CCNAME"] = cache_path
            log.info("KRB5CCNAME set to %s", cache_path)
            return cache_path
        existing = os.getenv("KRB5CCNAME")
        if existing:
            log.debug("Using existing KRB5CCNAME=%s", existing)
        else:
            log.warning("KRB5CCNAME not set; Kerberos will use the system default credential cache.")
        return existing
    
    def close(self):
        """Explicitly close the session."""
        self.session.close()
    
    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Make HTTP request with error handling.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (will be appended to base URL)
            **kwargs: Additional arguments for requests
            
        Returns:
            Response object
            
        Raises:
            requests.HTTPError: If request fails
        """
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        
        # Set default timeout
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        # Explicitly pass verify per-call in case environment variables override session defaults
        kwargs.setdefault("verify", self.verify)
        
        log.debug("API request: %s %s", method, url)
        
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            if not self._auth_success_logged and self.auth_mode in {"token", "basic"}:
                log.info("HSD %s authentication succeeded", self.auth_mode)
                self._auth_success_logged = True
            return response
        except requests.exceptions.HTTPError as e:
            status_code = getattr(e.response, "status_code", None)
            if not self._auth_success_logged and self.auth_mode in {"token", "basic"}:
                if status_code in {401, 403}:
                    log.error("HSD %s authentication failed (HTTP %s)", self.auth_mode, status_code)
                    self._auth_success_logged = True
            log.error("API request failed: %s %s - %s", method, url, e)
            raise
        except requests.exceptions.RequestException as e:
            log.error("API request failed: %s %s - %s", method, url, e)
            raise
    
    def get_bug(self, bug_id: str) -> Optional[HsdBug]:
        """
        Fetch a single bug by ID.
        
        Args:
            bug_id: HSD bug ID (e.g., "1234567890")
            
        Returns:
            HsdBug object or None if not found
        """
        try:
            response = self._request("GET", f"article/{bug_id}")
            data = response.json()
            
            return self._parse_bug(data)
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                log.warning("Bug %s not found", bug_id)
                return None
            raise
    
    def search_bugs(self, query: str, limit: int = 100) -> List[HsdBug]:
        """
        Search for bugs using HSD query syntax.
        
        Args:
            query: HSD query string (e.g., "status:active AND component:wifi")
            limit: Maximum number of results to return
            
        Returns:
            List of HsdBug objects
        """
        log.info("Executing query: %s", query)
        
        params = {
            "q": query,
            "limit": limit,
            "fields": "id,title,status,severity,priority,component,owner,submitter,created_date,modified_date"
        }
        
        log.debug("Query parameters: %s", params)
        
        try:
            response = self._request("GET", "query", params=params)
            data = response.json()
            
            bugs = []
            for item in data.get("data", []):
                bug = self._parse_bug(item)
                if bug:
                    bugs.append(bug)
            
            log.info("Query returned %d bugs: %s", len(bugs), query)
            return bugs
        except Exception as e:
            log.error("Search failed for query '%s': %s", query, e)
            return []
    
    def get_bugs_by_component(self, component: str, status: str = "active", limit: int = 100) -> List[HsdBug]:
        """
        Get bugs for a specific component.
        
        Args:
            component: Component name (e.g., "WiFi", "Bluetooth")
            status: Bug status filter (e.g., "active", "resolved", "closed")
            limit: Maximum number of results
            
        Returns:
            List of HsdBug objects
        """
        query = f"component:{component}"
        if status:
            query += f" AND status:{status}"
        
        log.info("Searching by component - Query: %s", query)
        return self.search_bugs(query, limit=limit)
    
    def get_bugs_by_owner(
        self,
        owner: str,
        limit: int = 500,
        community_id: str = "1203659509",
        created_year: Optional[int] = None,
    ) -> List[HsdBug]:
        """
        Get bugs assigned to a specific owner via the /rest/article endpoint.

        The generic search_bugs() method hits /rest/query which is only
        for saved-query execution and ignores free-text filter params,
        returning the entire community.  Instead we use the article-level
        search endpoint that accepts structured field filters.

        Args:
            owner: Owner IDSID (e.g. "jtsao1")
            limit: Maximum number of results
            community_id: HSD community/parent_id to scope the search
            created_year: If set, restrict to articles created on/after Jan 1
                          of this year (ISO date prefix filter).
        """
        fields = (
            "id,title,status,status_reason,severity,priority,"
            "component,owner,submitter,created_date,modified_date,"
            "tenant,subject,promoted_id"
        )
        # Try structured article search with owner filter
        endpoints_to_try = [
            # Format 1: parent_id + filter param
            {
                "path": "article",
                "params": {
                    "subject": "bug",
                    "parent_id": community_id,
                    "owner": owner,
                    "start_at": 1,
                    "max_results": limit,
                    "fields": fields,
                },
            },
            # Format 2: filter expression
            {
                "path": "article",
                "params": {
                    "subject": "bug",
                    "parent_id": community_id,
                    "filter": f"owner='{owner}'",
                    "start_at": 1,
                    "max_results": limit,
                    "fields": fields,
                },
            },
            # Format 3: query-string search
            {
                "path": "article",
                "params": {
                    "subject": "bug",
                    "parent_id": community_id,
                    "q": f"owner:{owner}",
                    "start_at": 1,
                    "max_results": limit,
                    "fields": fields,
                },
            },
        ]

        for i, ep in enumerate(endpoints_to_try, 1):
            try:
                log.info(
                    "[owner-search %d/%d] GET %s/article owner=%s",
                    i, len(endpoints_to_try), self.api_url, owner,
                )
                response = self._request("GET", ep["path"], params=ep["params"])
                data = response.json()

                items = (
                    data.get("data")
                    or data.get("results")
                    or data.get("items")
                    or data.get("articles")
                    or (data if isinstance(data, list) else [])
                )

                # Guard: if we got back more than limit it means the owner
                # filter was ignored — skip this format.
                if len(items) > limit:
                    log.warning(
                        "owner-search format %d returned %d items (> limit %d); "
                        "owner filter was likely ignored — skipping",
                        i, len(items), limit,
                    )
                    continue

                bugs: List[HsdBug] = []
                for item in items:
                    bug = self._parse_bug(item)
                    if bug:
                        # Post-filter: confirm owner matches (case-insensitive)
                        if bug.owner and bug.owner.strip().lower() != owner.strip().lower():
                            continue
                        # Optional year filter
                        if created_year and bug.created_date:
                            try:
                                year = int(bug.created_date[:4])
                                if year < created_year:
                                    continue
                            except (ValueError, TypeError):
                                pass
                        bugs.append(bug)

                if bugs or len(items) == 0:
                    log.info(
                        "owner-search format %d succeeded: %d bug(s) for owner=%s",
                        i, len(bugs), owner,
                    )
                    return bugs

            except requests.HTTPError as e:
                log.debug("owner-search format %d HTTP error: %s", i, e)
            except Exception as e:
                log.debug("owner-search format %d error: %s", i, e)

        log.warning(
            "All owner-search formats failed for owner=%s. "
            "Create a saved HSD query filtered by owner=%s and use --hsd-query-id instead.",
            owner, owner,
        )
        return []
    
    def get_recent_bugs(self, days: int = 7, limit: int = 100) -> List[HsdBug]:
        """
        Get bugs created in the last N days.
        
        Args:
            days: Number of days to look back
            limit: Maximum number of results
            
        Returns:
            List of HsdBug objects
        """
        query = f"created_date:>now-{days}d"
        log.info("Searching recent bugs - Query: %s", query)
        return self.search_bugs(query, limit=limit)
    
    def get_bugs_from_saved_query(self, query_id: str, limit: int = 1000) -> List[HsdBug]:
        """
        Fetch bugs from a saved HSDes query/filter.
        
        Args:
            query_id: HSDes query ID (e.g., "16021056445")
            limit: Maximum number of results to return
            
        Returns:
            List of HsdBug objects
        
        Example URL:
            https://hsdes.intel.com/appstore/generalapps/#/pages/community/1203659509?queryId=16021056445
        """
        log.info("Fetching bugs from saved query ID: %s (limit: %d)", query_id, limit)
        log.info("Query URL: https://hsdes.intel.com/appstore/generalapps/#/pages/community/1203659509?queryId=%s", query_id)
        
        # Try multiple API endpoints that might work with HSDes
        endpoints = [
            {
                "path": f"query/{query_id}",
                "params": {
                    "include_text_fields": "Y",
                    "start_at": 1,
                    "max_results": min(limit, 150000),
                },
            },
            {"path": f"query/execute/{query_id}", "params": {"limit": limit}},
            {"path": f"query/{query_id}/execute", "params": {"limit": limit}},
            {"path": f"savedquery/{query_id}", "params": {"limit": limit}},
            {"path": f"article/query/{query_id}", "params": {"limit": limit}},
        ]
        
        for i, endpoint in enumerate(endpoints, 1):
            try:
                full_url = f"{self.api_url}/{endpoint['path']}"
                log.info("[Attempt %d/%d] Trying endpoint: %s", i, len(endpoints), full_url)
                response = self._request("GET", endpoint["path"], params=endpoint.get("params"))
                data = response.json()
                
                # Parse response based on structure
                bugs = []
                
                # Try different response structures
                if isinstance(data, dict):
                    items = (
                        data.get("data")
                        or data.get("results")
                        or data.get("items")
                        or data.get("articles")
                        or []
                    )
                elif isinstance(data, list):
                    items = data
                else:
                    items = []
                
                for item in items:
                    bug = self._parse_bug(item)
                    if bug:
                        bugs.append(bug)
                
                if bugs:
                    log.info("Successfully fetched %d bugs from query %s", len(bugs), query_id)
                    return bugs
                
            except requests.HTTPError as e:
                log.debug("Endpoint %s failed: %s", endpoint, e)
                continue
            except Exception as e:
                log.debug("Endpoint %s error: %s", endpoint, e)
                continue
        
        # If all API attempts fail, log warning
        log.warning("Could not fetch bugs from query ID %s via API endpoints", query_id)
        log.info("You may need to access the query directly via web interface or use alternate authentication")
        
        # Provide helpful guidance
        log.info("=" * 70)
        log.info("HSD API Authentication Failed - Alternative Options:")
        log.info("=" * 70)
        log.info("1. Validate Kerberos ticket:")
        log.info("   - Run 'klist' to verify a valid ticket exists")
        log.info("   - If expired or missing, run 'kinit your_upi@INTEL.COM'")
        log.info("")
        log.info("2. Manual Export (recommended for now):")
        log.info("   - Visit: https://hsdes.intel.com/appstore/generalapps/#/pages/community/1203659509?queryId=%s", query_id)
        log.info("   - Click 'Export' button")
        log.info("   - Save as CSV/Excel")
        log.info("")
        log.info("3. Request Faceless Account:")
        log.info("   - Email: ags@intel.com")
        log.info("   - Subject: 'Faceless Account for HSD API Access'")
        log.info("   - Get dedicated service account credentials")
        log.info("=" * 70)
        
        return []
    
    def _parse_bug(self, data: Dict[str, Any]) -> Optional[HsdBug]:
        """
        Parse API response data into HsdBug object.
        
        Args:
            data: Raw API response data
            
        Returns:
            HsdBug object or None if parsing fails
        """
        try:
            return HsdBug(
                bug_id=str(data.get("id", "")),
                title=str(data.get("title", "")),
                status=str(data.get("status", "")),
                status_reason=str(
                    data.get("status_reason", "")
                    or data.get("state_reason", "")
                    or data.get("reason", "")
                ),
                severity=str(data.get("severity", "")),
                priority=str(data.get("priority", "")),
                component=str(data.get("component", "")),
                owner=str(data.get("owner", "")),
                submitter=str(data.get("submitter", "")),
                created_date=str(data.get("created_date", "")),
                modified_date=str(data.get("modified_date", "")),
                tenant=str(data.get("tenant", "")),
                subject=str(data.get("subject", "")),
                description=str(data.get("description", "")),
                resolved_date=str(data.get("resolved_date", "")),
                promoted_id=str(data.get("promoted_id", "")),
                customer=str(data.get("customer", "")) or str(data.get("customer_detail", "")),
                submitted_date=str(data.get("ww_submitted", "")) or str(data.get("submitted_date", "")) or str(data.get("created_date", "")),
                days_open=_calculate_days_open(
                    str(data.get("created_date", "")),
                    str(data.get("resolved_date", "")),
                ),
            )
        except Exception as e:
            log.error("Failed to parse bug data: %s", e)
            return None


# -------------------------
# Database Access (Alternative)
# -------------------------
class HsdDatabaseClient:
    """Client for direct database access to HSD (requires pyodbc)."""
    
    def __init__(self, server: str = HSD_DB_SERVER, database: str = HSD_DB_NAME,
                 username: str = HSD_DB_USER, password: str = HSD_DB_PASS):
        """Initialize database connection."""
        self.server = server
        self.database = database
        self.username = username
        self.password = password
        self.connection = None
        
        if not all([server, username, password]):
            raise ValueError("Missing database connection parameters")
    
    def connect(self):
        """Establish database connection."""
        try:
            import pyodbc
            
            conn_str = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={self.server};"
                f"DATABASE={self.database};"
                f"UID={self.username};"
                f"PWD={self.password}"
            )
            
            self.connection = pyodbc.connect(conn_str, timeout=DEFAULT_TIMEOUT)
            log.info("Connected to HSD database: %s", self.database)
        except ImportError:
            log.error("pyodbc not installed. Run: pip install pyodbc")
            raise
        except Exception as e:
            log.error("Database connection failed: %s", e)
            raise
    
    def close(self):
        """Close database connection."""
        if self.connection:
            try:
                self.connection.close()
                log.info("Database connection closed")
            except Exception as e:
                log.warning("Failed to close database connection: %s", e)
    
    def execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """
        Execute SQL query and return results.
        
        Args:
            query: SQL query string
            params: Query parameters (for parameterized queries)
            
        Returns:
            List of dictionaries (column: value)
        """
        if not self.connection:
            self.connect()
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, params)
            
            columns = [column[0] for column in cursor.description]
            results = []
            
            for row in cursor.fetchall():
                results.append(dict(zip(columns, row)))
            
            cursor.close()
            log.debug("Query returned %d rows", len(results))
            return results
        except Exception as e:
            log.error("Query execution failed: %s", e)
            raise


# -------------------------
# Customer Engineering DB Writer
# -------------------------
class CustomerDbWriter:
    """Insert filtered HSD bugs into the customer engineering Postgres database."""

    COLUMN_NAMES = (
        "hsd_id",
        "hsd_title",
        "hsd_promoted_id",
        "hsd_customer",
        "hsd_submitted_date",
        "hsd_days_open",
    )

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        table_name: str,
        sslmode: str = "require",
        page_size: int = 500,
        create_table: bool = False,
    ):
        self.host = host
        self.port = port or 5432
        self.database = database
        self.user = user
        self.password = password
        self.sslmode = sslmode
        self.page_size = page_size
        self.create_table = create_table
        self.raw_table_name = (table_name or "").strip()
        if not self.raw_table_name:
            raise ValueError("Table name is required when enabling DB inserts")

        self._quoted_table = self._quote_table(self.raw_table_name)
        self._connection = None
        self._cursor = None
        self._psycopg2 = None
        self._execute_values = None

        for field_name, value in (
            ("db host", self.host),
            ("db name", self.database),
            ("db user", self.user),
            ("db password", self.password),
        ):
            if not value:
                raise ValueError(f"Missing {field_name} for DB inserts")

    @staticmethod
    def _quote_table(name: str) -> str:
        parts = name.split(".")
        if not parts or any(not _TABLE_IDENTIFIER_RE.match(part) for part in parts):
            raise ValueError(
                "Table names may only contain letters, numbers, underscores, and an optional schema prefix"
            )
        return ".".join(f'"{part}"' for part in parts)

    def _load_driver(self) -> None:
        if self._psycopg2 is not None:
            return
        try:
            import psycopg2  # type: ignore
            from psycopg2.extras import execute_values  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment specific
            raise RuntimeError("psycopg2 is required for --db-table operations") from exc
        self._psycopg2 = psycopg2
        self._execute_values = execute_values

    def _connect(self) -> None:
        if self._connection:
            return
        self._load_driver()
        self._connection = self._psycopg2.connect(
            host=self.host,
            port=self.port,
            dbname=self.database,
            user=self.user,
            password=self.password,
            sslmode=self.sslmode,
        )
        self._cursor = self._connection.cursor()

    def close(self) -> None:
        if self._cursor:
            try:
                self._cursor.close()
            except Exception:
                pass
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
        self._cursor = None
        self._connection = None

    def insert_bugs(self, bugs: List[HsdBug], dry_run: bool = True) -> int:
        rows = self._prepare_rows(bugs)
        if not rows:
            log.info("No valid HSD rows to insert into table %s", self.raw_table_name)
            return 0

        if dry_run:
            self._log_dry_run(rows)
            return len(rows)

        self._connect()
        if self.create_table:
            self._ensure_table()

        columns_sql = ", ".join(self.COLUMN_NAMES)
        upsert_sql = f"""
            INSERT INTO {self._quoted_table} ({columns_sql})
            VALUES %s
            ON CONFLICT (hsd_id) DO UPDATE SET
                hsd_title = EXCLUDED.hsd_title,
                hsd_promoted_id = EXCLUDED.hsd_promoted_id,
                hsd_customer = EXCLUDED.hsd_customer,
                hsd_submitted_date = EXCLUDED.hsd_submitted_date,
                hsd_days_open = EXCLUDED.hsd_days_open,
                inserted_at = CURRENT_TIMESTAMP
        """
        self._execute_values(self._cursor, upsert_sql, rows, page_size=self.page_size)
        self._connection.commit()
        log.info("Inserted %d row(s) into %s", len(rows), self.raw_table_name)
        return len(rows)

    def _ensure_table(self) -> None:
        assert self._cursor is not None
        create_sql = f"""
            CREATE TABLE IF NOT EXISTS {self._quoted_table} (
                hsd_id TEXT PRIMARY KEY,
                hsd_title TEXT,
                hsd_promoted_id TEXT,
                hsd_customer TEXT,
                hsd_submitted_date TIMESTAMP NULL,
                hsd_days_open INTEGER,
                inserted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """
        self._cursor.execute(create_sql)
        self._connection.commit()

    def _prepare_rows(self, bugs: List[HsdBug]) -> List[Tuple[Any, ...]]:
        rows: List[Tuple[Any, ...]] = []
        for bug in bugs:
            bug_id = (bug.bug_id or "").strip()
            if not bug_id:
                log.debug("Skipping HSD bug with missing ID: %s", bug)
                continue
            submitted_dt = self._normalize_datetime(bug.submitted_date) or self._normalize_datetime(bug.created_date)
            rows.append(
                (
                    bug_id,
                    bug.title or "",
                    bug.promoted_id or "",
                    bug.customer or "",
                    submitted_dt,
                    bug.days_open,
                )
            )
        return rows

    @staticmethod
    def _normalize_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        parsed = _parse_hsd_date(value)
        if not parsed:
            return None
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    def _log_dry_run(self, rows: List[Tuple[Any, ...]]) -> None:
        log.info(
            "Dry run only: would insert %d row(s) into %s. Rerun with --db-commit to persist rows.",
            len(rows),
            self.raw_table_name,
        )
        preview = rows[: min(5, len(rows))]
        for idx, row in enumerate(preview, 1):
            row_dict = {name: row[pos] for pos, name in enumerate(self.COLUMN_NAMES)}
            log.info("  Row %d preview: %s", idx, row_dict)


# -------------------------
# Export Functions
# -------------------------
def export_to_json(bugs: List[HsdBug], filename: str):
    """Export bugs to JSON file."""
    try:
        data = [bug.to_dict() for bug in bugs]
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log.info("Exported %d bugs to %s", len(bugs), filename)
    except Exception as e:
        log.error("Failed to export to JSON: %s", e)


def export_to_csv(bugs: List[HsdBug], filename: str):
    """Export bugs to CSV file."""
    try:
        import pandas as pd
        
        data = [bug.to_dict() for bug in bugs]
        df = pd.DataFrame(data)
        df.to_csv(filename, index=False, encoding="utf-8")
        log.info("Exported %d bugs to %s", len(bugs), filename)
    except ImportError:
        log.error("pandas not installed. Run: pip install pandas")
    except Exception as e:
        log.error("Failed to export to CSV: %s", e)


def auto_export_from_hsd_web(query_id: str, username: str, password: str, 
                             output_file: str = "hsd_export.csv", 
                             download_dir: Optional[str] = None,
                             headless: bool = True) -> Optional[str]:
    """
    Fully automated HSD CSV export using Selenium browser automation.
    
    Handles:
    - Intel SSO login
    - Navigate to saved query
    - Click export button
    - Download CSV automatically
    
    Args:
        query_id: HSD query ID (e.g., "16021056445")
        username: Intel IDSID
        password: Intel password
        output_file: Desired output filename
        download_dir: Download directory (default: current directory)
        headless: Run browser in background (no UI)
        
    Returns:
        Path to downloaded CSV file, or None if failed
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.edge.service import Service
        from selenium.webdriver.edge.options import Options
        from webdriver_manager.microsoft import EdgeChromiumDriverManager
    except ImportError:
        log.error("Selenium not installed. Run: pip install selenium webdriver-manager")
        return None
    
    if download_dir is None:
        download_dir = os.getcwd()
    
    log.info("Starting automated HSD export using Selenium")
    log.info("Query ID: %s", query_id)
    
    # Configure Edge browser (or Chrome)
    options = Options()
    if headless:
        options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or "http://proxy-chain.intel.com:911"
    if proxy:
        log.info("Using proxy: %s", proxy)
        options.add_argument(f"--proxy-server={proxy}")
        options.add_argument("--proxy-bypass-list=<-loopback>")
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
        os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])
    
    # Disable SSL verification for Intel internal sites
    options.add_argument("--ignore-certificate-errors")
    
    # Set download directory
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": False
    }
    options.add_experimental_option("prefs", prefs)
    
    driver = None
    
    try:
        # Initialize browser
        log.info("Initializing Edge browser...")
        try:
            service = Service(EdgeChromiumDriverManager().install())
            driver = webdriver.Edge(service=service, options=options)
        except Exception as e:
            log.error("Failed to initialize Edge browser: %s", e)
            log.info("Trying with system Edge driver...")
            # Try without webdriver_manager
            driver = webdriver.Edge(options=options)
        
        driver.set_page_load_timeout(60)
        wait = WebDriverWait(driver, 30)
        
        # Navigate to HSD query
        query_url = f"https://hsdes.intel.com/appstore/generalapps/#/pages/community/1203659509?queryId={query_id}"
        log.info("Navigating to: %s", query_url)
        driver.get(query_url)
        
        # Wait for and handle Intel SSO login
        log.info("Waiting for SSO login page...")
        time.sleep(3)
        
        # Check if login required
        if "login" in driver.current_url.lower() or "sso" in driver.current_url.lower():
            log.info("SSO login detected, entering credentials...")
            
            try:
                # Find username field (multiple possible IDs)
                username_field = wait.until(
                    EC.presence_of_element_located((By.ID, "i0116"))
                )
                username_field.clear()
                username_field.send_keys(username)
                
                # Click Next button
                next_button = driver.find_element(By.ID, "idSIButton9")
                next_button.click()
                time.sleep(2)
                
                # Enter password
                password_field = wait.until(
                    EC.presence_of_element_located((By.ID, "i0118"))
                )
                password_field.clear()
                password_field.send_keys(password)
                
                # Click Sign In
                signin_button = driver.find_element(By.ID, "idSIButton9")
                signin_button.click()
                
                log.info("Login submitted, waiting for page load...")
                time.sleep(5)
                
                # Handle "Stay signed in?" prompt if appears
                try:
                    stay_signed_in_yes = driver.find_element(By.ID, "idSIButton9")
                    if stay_signed_in_yes:
                        stay_signed_in_yes.click()
                        time.sleep(2)
                except:
                    pass
                
            except Exception as e:
                log.warning("SSO login automation failed: %s", e)
                log.info("You may need to log in manually. Browser will stay open for 30 seconds...")
                time.sleep(30)
        
        # Wait for HSD page to load
        log.info("Waiting for HSD query page to load...")
        time.sleep(10)
        
        # Look for Export button (multiple possible selectors)
        log.info("Looking for Export button...")
        export_button = None
        
        export_selectors = [
            "//button[contains(text(), 'Export')]",
            "//button[contains(@class, 'export')]",
            "//a[contains(text(), 'Export')]",
            "//span[contains(text(), 'Export')]",
        ]
        
        for selector in export_selectors:
            try:
                export_button = driver.find_element(By.XPATH, selector)
                if export_button:
                    log.info("Found Export button")
                    break
            except:
                continue
        
        if not export_button:
            log.warning("Could not find Export button automatically")
            log.info("Please click Export manually. Browser will stay open for 30 seconds...")
            time.sleep(30)
        else:
            # Click export
            log.info("Clicking Export button...")
            export_button.click()
            time.sleep(2)
            
            # Look for CSV option
            try:
                csv_option = driver.find_element(By.XPATH, "//button[contains(text(), 'CSV')]")
                csv_option.click()
                log.info("Selected CSV export format")
            except:
                log.warning("Could not find CSV format option, using default")
            
            time.sleep(3)
        
        # Wait for download to complete
        log.info("Waiting for download to complete...")
        time.sleep(10)
        
        # Find downloaded file
        downloads = glob.glob(os.path.join(download_dir, "*.csv"))
        downloads.sort(key=os.path.getmtime, reverse=True)
        
        if downloads:
            latest_file = downloads[0]
            final_path = os.path.join(download_dir, output_file)
            
            # Rename if needed
            if latest_file != final_path:
                import shutil
                shutil.move(latest_file, final_path)
            
            log.info("Successfully downloaded CSV: %s", final_path)
            error("Full error details: %s", str(e), exc_info=True)
        log.info("")
        log.info("Troubleshooting tips:")
        log.info("1. Check if Edge browser is installed")
        log.info("2. Try running without --headless flag to see what's happening")
        log.info("3. Check Intel proxy settings (HTTP_PROXY environment variable)")
        log.info("4. Try manual CSV export as fallback")
        log.info("")
        if driver:
            log.info("Browser will stay open for debugging for 10 seconds...")
            log.warning("No CSV file found in download directory")
            return None
        
    except Exception as e:
        log.error("Automated export failed: %s", e)
        log.info("Browser will stay open for debugging for 10 seconds...")
        if driver:
            time.sleep(10)
        return None
    
    finally:
        if driver:
            try:
                driver.quit()
                log.info("Browser closed")
            except:
                pass


# -------------------------
# CLI
# -------------------------
def main():
    parser = argparse.ArgumentParser(description="Access HSD bug database")
    parser.add_argument("--method", choices=["api", "db", "web"], default="api", 
                       help="Access method: api=REST API, db=database, web=automated browser export")
    parser.add_argument("--auto-export", action="store_true", help="Automatically export CSV from HSD web (uses Selenium)")
    parser.add_argument("--headless", action="store_true", help="Run browser in background (for --auto-export)")
    parser.add_argument("--bug-id", help="Fetch specific bug by ID")
    parser.add_argument("--query-id", default=DEFAULT_QUERY_ID,
                        help="Fetch bugs from saved HSDes query/filter ID (default: 16021056445)")
    parser.add_argument("--component", help="Filter by component")
    parser.add_argument("--owner", help="Filter by owner")
    parser.add_argument("--status", default="active", help="Filter by status (default: active)")
    parser.add_argument("--days", type=int, help="Get bugs from last N days")
    parser.add_argument("--query", help="Custom HSD query string")
    parser.add_argument("--limit", type=int, default=100, help="Maximum results (default: 100)")
    parser.add_argument("--export-json", help="Export results to JSON file")
    parser.add_argument("--export-csv", help="Export results to CSV file")
    parser.add_argument("--api-token", help="Raw HSD API token value (overrides HSD_API_TOKEN)")
    parser.add_argument("--ca-bundle", help="Path to PEM/CRT bundle for SSL verification")
    parser.add_argument("--krb-ccache", help="Path to Kerberos credential cache (sets KRB5CCNAME)")
    parser.add_argument("--auth-mode", choices=["token", "kerberos", "auto"],
                        default=os.getenv("HSD_AUTH_MODE", "auto"),
                        help="Force HSD auth mode (default: auto)")
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO)")
    parser.add_argument("--db-table", default=DEFAULT_CUSTOMER_DB_TABLE,
                        help="Insert filtered HSD rows into this Postgres table (default: env HSD_TARGET_TABLE)")
    parser.add_argument("--db-host", default=CUSTOMER_DB_HOST,
                        help="Postgres host for --db-table (default: env DB_HOST)")
    parser.add_argument("--db-port", type=int, default=CUSTOMER_DB_PORT or 5432,
                        help="Postgres port for --db-table (default: env DB_PORT or 5432)")
    parser.add_argument("--db-name", default=CUSTOMER_DB_NAME,
                        help="Postgres database name for --db-table (default: env DB_NAME)")
    parser.add_argument("--db-user", default=CUSTOMER_DB_USER,
                        help="Postgres username for --db-table (default: env DB_USER)")
    parser.add_argument("--db-password", default=CUSTOMER_DB_PASS,
                        help="Postgres password for --db-table (default: env DB_PASS)")
    parser.add_argument("--db-sslmode", default=CUSTOMER_DB_SSLMODE,
                        help="Postgres sslmode for --db-table (default: require)")
    parser.add_argument("--db-page-size", type=int, default=500,
                        help="Batch size for Postgres inserts (default: 500)")
    parser.add_argument("--db-commit", action="store_true",
                        help="Persist rows to Postgres instead of dry-run logging")
    parser.add_argument("--db-create-table", action="store_true",
                        help="Create the --db-table if it does not exist (requires privileges)")
    
    args = parser.parse_args()
    
    # Update log level
    global log
    log = setup_logging(args.log_level)

    if "--query-id" not in sys.argv and args.query_id == DEFAULT_QUERY_ID:
        log.info("Default query ID in use: %s (override with --query-id)", args.query_id)
    
    bugs: List[HsdBug] = []
    client = None
    db_writer: Optional[CustomerDbWriter] = None
    db_table_name = (args.db_table or "").strip()

    if args.db_commit and not db_table_name:
        log.error("--db-commit requires --db-table to be specified")
        return 1
    
    try:
        if args.method == "web" or args.auto_export:
            # Automated web export using Selenium
            if not HSD_USERNAME or not HSD_PASSWORD:
                log.error("HSD_USERNAME and HSD_PASSWORD required for automated web export")
                log.info("Add to .env file: HSD_USERNAME=your_idsid and HSD_PASSWORD=your_password")
                return 1
            
            if not args.query_id:
                log.error("--query-id required for automated web export")
                return 1
            
            log.info("Starting automated web export...")
            csv_path = auto_export_from_hsd_web(
                query_id=args.query_id,
                username=HSD_USERNAME,
                password=HSD_PASSWORD,
                output_file=f"hsd_export_{args.query_id}.csv",
                headless=args.headless
            )
            
            if csv_path and os.path.exists(csv_path):
                log.info("Export successful: %s", csv_path)
                log.info("CSV import is disabled; no bugs will be loaded from the export.")
            else:
                log.error("Automated export failed")
                return 1
        elif args.method == "api":
            # Use REST API
            client = HsdApiClient(
                token=args.api_token or HSD_API_TOKEN,
                verify=args.ca_bundle,
                auth_mode=args.auth_mode,
                kerberos_cache=args.krb_ccache,
            )
            
            if args.bug_id:
                bug = client.get_bug(args.bug_id)
                if bug:
                    bugs.append(bug)
                    print(f"\nBug {args.bug_id}:")
                    print(bug.to_json())
            elif args.query_id:
                bugs = client.get_bugs_from_saved_query(args.query_id, limit=args.limit)
                if not bugs:
                    log.warning("No bugs returned from query ID %s", args.query_id)
                    log.info("Note: You may need to configure API authentication or access via web interface")
            elif args.query:
                bugs = client.search_bugs(args.query, limit=args.limit)
            elif args.component:
                bugs = client.get_bugs_by_component(args.component, status=args.status, limit=args.limit)
            elif args.owner:
                bugs = client.get_bugs_by_owner(args.owner, limit=args.limit)
            elif args.days:
                bugs = client.get_recent_bugs(days=args.days, limit=args.limit)
            else:
                log.error("Please specify --bug-id, --query-id, --query, --component, --owner, or --days")
                return 1
        
        elif args.method == "db":
            # Use direct database access
            client = HsdDatabaseClient()
            client.connect()
            
            # Example query (adjust table/column names based on actual schema)
            query = "SELECT TOP (?) * FROM bugs WHERE status = ?"
            results = client.execute_query(query, (args.limit, args.status))
            
            log.info("Retrieved %d bugs from database", len(results))
            for result in results:
                print(json.dumps(result, indent=2, default=str))
        
        bugs = filter_bugs_by_owner(bugs)

        if db_table_name:
            if not bugs:
                log.info("Skipping DB insert: no bugs matched the filters for table %s", db_table_name)
            else:
                db_host = args.db_host or CUSTOMER_DB_HOST
                db_name = args.db_name or CUSTOMER_DB_NAME
                db_user = args.db_user or CUSTOMER_DB_USER
                db_password = args.db_password or CUSTOMER_DB_PASS
                db_port = args.db_port or CUSTOMER_DB_PORT or 5432
                db_sslmode = (args.db_sslmode or CUSTOMER_DB_SSLMODE or "require").strip()
                page_size = args.db_page_size if args.db_page_size and args.db_page_size > 0 else 500

                missing_fields = [
                    label
                    for label, value in (
                        ("DB host", db_host),
                        ("DB name", db_name),
                        ("DB user", db_user),
                        ("DB password", db_password),
                    )
                    if not value
                ]

                if missing_fields:
                    log.error(
                        "Cannot perform --db-table operation. Missing configuration: %s",
                        ", ".join(missing_fields),
                    )
                    return 1

                db_writer = CustomerDbWriter(
                    host=db_host,
                    port=db_port,
                    database=db_name,
                    user=db_user,
                    password=db_password,
                    table_name=db_table_name,
                    sslmode=db_sslmode,
                    page_size=page_size,
                    create_table=args.db_create_table,
                )
                inserted = db_writer.insert_bugs(bugs, dry_run=not args.db_commit)
                if args.db_commit:
                    log.info("Database upsert finished: %d row(s) processed into %s", inserted, db_table_name)
                else:
                    log.info(
                        "Dry run complete for table %s; add --db-commit to persist %d row(s)",
                        db_table_name,
                        inserted,
                    )

        # Display results
        if bugs:
            print(f"\n{'='*80}")
            print(f"Found {len(bugs)} bugs")
            print(f"{'='*80}\n")
            print(_format_bug_table(bugs))
            print()
        
        # Export if requested
        if args.export_json and bugs:
            export_to_json(bugs, args.export_json)
        
        if args.export_csv and bugs:
            export_to_csv(bugs, args.export_csv)
        
        return 0
    
    except Exception as e:
        log.error("Execution failed: %s", e)
        return 1
    
    finally:
        # Cleanup
        if client:
            try:
                client.close()
            except Exception as e:
                log.warning("Failed to close client: %s", e)
        if db_writer:
            try:
                db_writer.close()
            except Exception as e:
                log.warning("Failed to close DB writer: %s", e)


if __name__ == "__main__":
    sys.exit(main())

