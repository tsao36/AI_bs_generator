from pathlib import Path
from collections import Counter
import html
import re
import json
import urllib.request

import pandas as pd


OUT_DIR = Path("customer issue analysis") / "outputs"
SOURCE_CSV_CANDIDATES = [
    OUT_DIR / "Lenovo WiFi BT JIRA all time closed with SW FIX.csv",
    OUT_DIR / "WiFi BT JIRA all time closed with SW FIX.csv",
]
ROWS_PATH = OUT_DIR / "root_cause_categorized_rows_model_based_top4_by_year_2021_2026.csv"
SUMMARY_PATH = OUT_DIR / "root_cause_table_model_based_WIFI_BT_top4_by_year_2021_2026.csv"
REPORT_PATH = OUT_DIR / "root_cause_report_model_based_WIFI_BT_top4.html"

ALLOWED_CAT = {
    "WIFI": ["Connectivity", "YB/Lost", "BSOD", "Performance"],
    "BT": ["Connectivity", "YB/Lost", "BSOD", "HLK"],
}
INSUFFICIENT = "Insufficient Root-Cause Detail"
MANUAL_REVIEW = "Needs Manual Review (Issue-level check required)"

THEMES = [
    "BIOS Integration and Platform Configuration Issues",
    "Firmware Configuration and Parameter Tuning Issues",
    "Driver and OS Interface Contract Issues",
    "Memory and Resource Management Issues",
    "Power State Transition and Reset Recovery Issues",
    "RF Coexistence and Channel Behavior Issues",
    "Security Validation and Protection Control Issues",
    "State Machine Transition and Flow Handling Issues",
    "Timing Synchronization and Race Condition Issues",
    "Firmware and Hardware Interface Communication Issues",
    "Validation Coverage Gap and Test Escape Issues",
    "Duplicate or Reference Case (follow linked Jira root cause)",
    "Insufficient Root-Cause Detail",
]

# Rule: watchdog/reset-flow hangs should map to Connectivity unless explicit BSOD signal exists.
FLOW_RESET_PATTERN = re.compile(
    r"watch\s*dog|wd\s*timer|busy-?wait|stuck|rx\s*stop\s*flow|bmc\s*hw\s*reset|reset\s*flow|termination\s*handling",
    re.I,
)
BSOD_SIGNAL_PATTERN = re.compile(r"\bbsod\b|bugcheck|0x9f|blue\s*screen", re.I)
YB_LOST_PATTERN = re.compile(
    r"yellow\s*bang|\byb\b|item\s+not\s+found|device\s+(missing|not\s+found|lost|disappeared)|"
    r"adapter\s+(missing|not\s+found|lost|disappeared)|"
    r"(not\s+detected|enumeration\s+fail|failed\s+enumeration|code\s*10|code\s*43)",
    re.I,
)
HLK_PATTERN = re.compile(r"\bhlk\b|hciextensions|development\s+and\s+integration|whql", re.I)
PERF_PATTERN = re.compile(
    r"throughput|latency|performance|slow|bandwidth|speed\s+drop|low\s+rate|poor\s+performance",
    re.I,
)
CONNECTIVITY_PATTERN = re.compile(
    r"disconnect|cannot\s+connect|failed\s+to\s+connect|connection\s+drop|reconnect|link\s+down|pairing\s+fail",
    re.I,
)
REFERENCE_PATTERN = re.compile(
    r"details\s+in|same\s+as|duplicate|dup\s+of|refer\s+to|https?://jira\.idoc\.intel\.com/browse/[A-Z]+-\d+",
    re.I,
)
JIRA_KEY_PATTERN = re.compile(r"\b([A-Z]+-\d+)\b")
URL_PATTERN = re.compile(r"https?://jira\.idoc\.intel\.com/browse/[A-Z]+-\d+", re.I)


def load_summary_map() -> dict[str, str]:
    for p in SOURCE_CSV_CANDIDATES:
        if not p.exists():
            continue
        src = pd.read_csv(p, encoding="utf-8-sig")
        if "Issue key" not in src.columns or "Summary" not in src.columns:
            continue
        src["Issue key"] = src["Issue key"].fillna("").astype(str).str.strip()
        src["Summary"] = src["Summary"].fillna("").astype(str)
        src = src[src["Issue key"] != ""]
        return dict(zip(src["Issue key"], src["Summary"]))
    return {}


def load_rootcause_map() -> dict[str, str]:
    root_map = {}

    for p in SOURCE_CSV_CANDIDATES:
        if not p.exists():
            continue
        src = pd.read_csv(p, encoding="utf-8-sig")
        if "Issue key" in src.columns and "Custom field (Root Cause)" in src.columns:
            src["Issue key"] = src["Issue key"].fillna("").astype(str).str.strip()
            src["Custom field (Root Cause)"] = src["Custom field (Root Cause)"].fillna("").astype(str).str.strip()
            src = src[(src["Issue key"] != "") & (src["Custom field (Root Cause)"] != "")]
            for k, v in zip(src["Issue key"], src["Custom field (Root Cause)"]):
                root_map[k] = v

    master = Path("jira_2021_2026_may.csv")
    if master.exists():
        try:
            m = pd.read_csv(master, usecols=["Issue key", "Custom field (Root Cause)"], encoding="utf-8-sig")
            m["Issue key"] = m["Issue key"].fillna("").astype(str).str.strip()
            m["Custom field (Root Cause)"] = m["Custom field (Root Cause)"].fillna("").astype(str).str.strip()
            m = m[(m["Issue key"] != "") & (m["Custom field (Root Cause)"] != "")]
            for k, v in zip(m["Issue key"], m["Custom field (Root Cause)"]):
                if k not in root_map:
                    root_map[k] = v
        except Exception:
            pass

    return root_map


def extract_jira_refs(text: str) -> list[str]:
    refs = [x.upper() for x in JIRA_KEY_PATTERN.findall(str(text or ""))]
    out = []
    seen = set()
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def resolve_reference_root(issue_key: str, original_root: str, root_map: dict[str, str], depth_limit: int = 6) -> tuple[str, str]:
    current_root = str(original_root or "").strip()
    current_issue = issue_key
    visited = {current_issue}

    depth = 0
    while depth < depth_limit:
        refs = extract_jira_refs(current_root)
        next_issue = ""
        for r in refs:
            if r in visited:
                continue
            cand = str(root_map.get(r, "") or "").strip()
            if cand:
                next_issue = r
                current_root = cand
                break

        if not next_issue:
            break

        visited.add(next_issue)
        current_issue = next_issue
        depth += 1

    return current_issue, current_root


def strip_reference_prefix(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return ""

    # Remove Jira URLs and common referral phrases, keep substantive tail text.
    t = URL_PATTERN.sub(" ", t)
    t = re.sub(r"(?i)details\s+in|same\s+as|duplicate|dup\s+of|refer\s+to|see\s+more\s+details\s+in\s+jira\s+above", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .;:-")
    return t


def is_reference_only_text(text: str) -> bool:
    stripped = strip_reference_prefix(text)
    # No meaningful residual sentence after removing reference markers.
    return len(stripped) < 20


def apply_reference_root_resolution(rows: pd.DataFrame, root_map: dict[str, str]) -> list[str]:
    changed = []
    resolved_from = []
    new_roots = []

    for _, r in rows.iterrows():
        issue = str(r.get("Issue key", "") or "").strip()
        root = str(r.get("Root Cause", "") or "").strip()
        source_issue, resolved_root = resolve_reference_root(issue, root, root_map)

        # Fallback when referenced issue data is unavailable: keep the descriptive tail text.
        if source_issue == issue and REFERENCE_PATTERN.search(root):
            tail = strip_reference_prefix(root)
            if tail:
                resolved_root = tail

        if source_issue != issue and resolved_root and resolved_root != root:
            changed.append(issue)

        resolved_from.append(source_issue)
        new_roots.append(resolved_root if resolved_root else root)

    rows["Resolved From Issue"] = resolved_from
    rows["Root Cause"] = new_roots
    return changed


def parse_env(path: Path) -> dict[str, str]:
    cfg = {}
    if not path.exists():
        return cfg
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        cfg[k.strip()] = v.strip()
    return cfg


def parse_json_any(text: str):
    t = str(text or "").strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}|\[.*\]", t, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def llm_classify_rows(rows: pd.DataFrame, summary_map: dict[str, str]) -> tuple[pd.DataFrame, bool]:
    cfg = parse_env(Path(".env"))
    gnai_url = (cfg.get("GNAI_URL") or "").rstrip("/")
    gnai_token = (cfg.get("GNAI_TOKEN") or "").strip()
    gnai_model = (cfg.get("GNAI_MODEL") or "").strip()

    if not (gnai_url and gnai_token and gnai_model):
        return rows, False

    items = []
    for idx, r in rows.reset_index(drop=True).iterrows():
        issue_key = str(r.get("Issue key", "") or "")
        items.append(
            {
                "id": str(idx),
                "issue_key": issue_key,
                "technology": str(r.get("Technology", "") or "").upper(),
                "summary": summary_map.get(issue_key, ""),
                "root_cause": str(r.get("Root Cause", "") or ""),
            }
        )

    system_prompt = (
        "You are a strict Jira classifier. "
        "Classify each item into ONE category and ONE theme from allowed lists. "
        "Return JSON only: {\"results\":[{\"id\":\"...\",\"category\":\"...\",\"theme\":\"...\"}]}. "
        "Category definitions: "
        "Connectivity = connection establishment, link stability, reconnect, data path drop, functional communication break. "
        "YB/Lost = yellow bang/device missing/lost enumeration/adapter disappeared/unrecoverable device presence loss. "
        "BSOD = explicit OS crash/bugcheck/blue screen/kernel crash symptom. "
        "Performance (WIFI only) = throughput/latency/perf degradation without hard disconnect. "
        "HLK (BT only) = HLK certification test failures primarily in test harness/compliance scenarios. "
        "If root cause is duplicate/reference-only or insufficient, still choose best category by symptom context and use appropriate theme."
    )

    payload = {
        "model": gnai_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "allowed_categories": ALLOWED_CAT,
                        "allowed_themes": THEMES,
                        "items": items,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }

    req = urllib.request.Request(
        gnai_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {gnai_token}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=240) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    obj = json.loads(body)
    content = str((obj.get("choices") or [{}])[0].get("message", {}).get("content", ""))
    parsed = parse_json_any(content)
    arr = parsed.get("results") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])

    id_to_pred = {}
    for x in arr:
        if not isinstance(x, dict):
            continue
        i = str(x.get("id", "")).strip()
        cat = str(x.get("category", "")).strip()
        th = str(x.get("theme", "")).strip()
        if i:
            id_to_pred[i] = (cat, th)

    new_cat = []
    new_theme = []
    for idx, r in rows.reset_index(drop=True).iterrows():
        tech = str(r.get("Technology", "") or "").upper()
        allowed = ALLOWED_CAT.get(tech, ["Connectivity"])
        cat, th = id_to_pred.get(str(idx), ("", ""))

        if cat not in allowed:
            cat = "Connectivity"
        if th not in THEMES:
            th = INSUFFICIENT

        new_cat.append(cat)
        new_theme.append(th)

    rows = rows.copy()
    rows["Model Category"] = new_cat
    rows["Root Cause Theme"] = new_theme
    return rows, True


def apply_category_overrides(rows: pd.DataFrame) -> list[str]:
    changed = []
    for i, r in rows.iterrows():
        if r["Model Category"] != "BSOD":
            continue
        root = r["Root Cause"]
        if FLOW_RESET_PATTERN.search(root) and not BSOD_SIGNAL_PATTERN.search(root):
            rows.at[i, "Model Category"] = "Connectivity"
            changed.append(r["Issue key"])
    return changed


def apply_yb_lost_overrides(rows: pd.DataFrame, summary_map: dict[str, str]) -> list[str]:
    changed = []
    for i, r in rows.iterrows():
        issue = str(r["Issue key"])
        tech = str(r["Technology"])
        cat = str(r["Model Category"])
        root = str(r["Root Cause"])
        summary = summary_map.get(issue, "")
        signal_text = f"{summary} {root}"

        if tech not in ("WIFI", "BT"):
            continue
        if cat not in ALLOWED_CAT[tech]:
            continue
        if YB_LOST_PATTERN.search(signal_text):
            if cat != "YB/Lost":
                rows.at[i, "Model Category"] = "YB/Lost"
                changed.append(issue)
    return changed


def apply_summary_category_overrides(rows: pd.DataFrame, summary_map: dict[str, str]) -> list[str]:
    changed = []
    for i, r in rows.iterrows():
        issue = str(r["Issue key"])
        tech = str(r["Technology"])
        cat = str(r["Model Category"])
        root = str(r["Root Cause"])
        summary = summary_map.get(issue, "")
        text = f"{summary} {root}"

        if tech not in ("WIFI", "BT"):
            continue

        target = ""

        # Highest-priority hard signals first.
        if BSOD_SIGNAL_PATTERN.search(text):
            target = "BSOD"
        elif YB_LOST_PATTERN.search(text):
            target = "YB/Lost"
        elif tech == "BT" and HLK_PATTERN.search(text):
            target = "HLK"
        elif tech == "WIFI" and PERF_PATTERN.search(text):
            target = "Performance"
        elif CONNECTIVITY_PATTERN.search(text):
            target = "Connectivity"

        if target and target in ALLOWED_CAT[tech] and target != cat:
            rows.at[i, "Model Category"] = target
            changed.append(issue)
    return changed


def apply_reference_theme_overrides(rows: pd.DataFrame) -> list[str]:
    changed = []
    target = "Duplicate or Reference Case (follow linked Jira root cause)"
    for i, r in rows.iterrows():
        root = str(r.get("Root Cause", "") or "")
        if REFERENCE_PATTERN.search(root) and is_reference_only_text(root):
            if str(r.get("Root Cause Theme", "") or "") != target:
                rows.at[i, "Root Cause Theme"] = target
                changed.append(str(r.get("Issue key", "") or ""))
    return changed


def apply_theme_signal_overrides(rows: pd.DataFrame) -> list[str]:
    changed = []
    for i, r in rows.iterrows():
        root = str(r.get("Root Cause", "") or "")
        theme = str(r.get("Root Cause Theme", "") or "")

        target = ""
        text = root.lower()

        # Strong protocol/interface failure signals in HLK-like scenarios.
        if (
            "hciextensions" in text
            or "received 0 advertisements" in text
            or "expected at least 16" in text
        ):
            target = "Firmware and Hardware Interface Communication Issues"

        if target and target in THEMES and target != theme:
            rows.at[i, "Root Cause Theme"] = target
            changed.append(str(r.get("Issue key", "") or ""))

    return changed


def build_summary(rows: pd.DataFrame) -> pd.DataFrame:
    all_year = []
    for y in range(2021, 2027):
        ydf = rows[rows["Year"] == y]
        rs = []
        for tech in ["WIFI", "BT"]:
            for cat in ALLOWED_CAT[tech]:
                sub = ydf[(ydf["Technology"] == tech) & (ydf["Model Category"] == cat)]
                valid = [
                    t
                    for t in sub["Root Cause Theme"].tolist()
                    if str(t).strip() and str(t) != INSUFFICIENT
                ]
                c = Counter(valid)
                top = [k for k, _ in c.most_common(4)]
                while len(top) < 4:
                    top.append("")
                if len(sub) > 0 and not str(top[0]).strip():
                    top[0] = MANUAL_REVIEW
                rs.append(
                    {
                        "Technology": tech,
                        "Category": cat,
                        "Root Cause Type 1": top[0],
                        "Root Cause Type 2": top[1],
                        "Root Cause Type 3": top[2],
                        "Root Cause Type 4": top[3],
                        "Issue Count": int(len(sub)),
                        "Year": y,
                    }
                )
        all_year.append(pd.DataFrame(rs))

    summary = pd.concat(all_year, ignore_index=True)
    return summary


def render_html(rows: pd.DataFrame, summary: pd.DataFrame) -> str:
    lookup = {}
    for (year, tech, cat), g in rows.groupby(["Year", "Technology", "Model Category"]):
        ids = [x for x in g["Issue key"].tolist() if x]
        lookup[(int(year), str(tech), str(cat))] = sorted(set(ids))

    jira_base = "https://jira.idoc.intel.com/browse/"

    def render_table(df: pd.DataFrame) -> str:
        cols = [
            "Category",
            "Root Cause Type 1",
            "Root Cause Type 2",
            "Root Cause Type 3",
            "Root Cause Type 4",
            "Issue Count",
            "Jira Check",
        ]
        out = ["<table class=\"tbl\"><thead><tr>"]
        for c in cols:
            out.append(f"<th>{html.escape(c)}</th>")
        out.append("</tr></thead><tbody>")

        for _, rr in df.iterrows():
            y = int(rr["Year"])
            t = str(rr["Technology"])
            c = str(rr["Category"])
            ids = lookup.get((y, t, c), [])

            if ids:
                preview = ids[:30]
                extra = len(ids) - len(preview)
                links = "".join(
                    [
                        f"<a href=\"{jira_base}{html.escape(i)}\" target=\"_blank\" rel=\"noopener noreferrer\">{html.escape(i)}</a>"
                        for i in preview
                    ]
                )
                if extra > 0:
                    links += f"<div class=\"more\">... and {extra} more</div>"
                hover = (
                    "<div class=\"hoverwrap\"><span class=\"hoverbtn\">Hover to view issue links</span>"
                    f"<div class=\"hoverpanel\">{links}</div></div>"
                )
            else:
                hover = "<span class=\"na\">No issues</span>"

            out.append("<tr>")
            out.append(f"<td>{html.escape(c)}</td>")
            out.append(f"<td>{html.escape(str(rr.get('Root Cause Type 1', '') or ''))}</td>")
            out.append(f"<td>{html.escape(str(rr.get('Root Cause Type 2', '') or ''))}</td>")
            out.append(f"<td>{html.escape(str(rr.get('Root Cause Type 3', '') or ''))}</td>")
            out.append(f"<td>{html.escape(str(rr.get('Root Cause Type 4', '') or ''))}</td>")
            out.append(f"<td>{int(rr.get('Issue Count', 0) or 0)}</td>")
            out.append(f"<td>{hover}</td>")
            out.append("</tr>")

        out.append("</tbody></table>")
        return "".join(out)

    rows_total = int(len(rows))
    summary_total = int(summary["Issue Count"].sum())

    parts = []
    parts.append('<!doctype html><html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>')
    parts.append('<title>Lenovo WiFi/BT Root Cause Theme Top4 by Year (2021-2026)</title>')
    parts.append('<style>body{font-family:"Segoe UI",Arial,sans-serif;background:#f4f7fb;color:#13233a;margin:0}.wrap{max-width:1550px;margin:24px auto;padding:0 16px 30px}.hero{background:linear-gradient(120deg,#0f6cbd,#1f8de0);color:#fff;border-radius:12px;padding:18px 20px}.sec{margin-top:14px;background:#fff;border:1px solid #d7e0ea;border-radius:12px;padding:12px}.sec h2{margin:4px 0 10px}.tbl{width:100%;border-collapse:collapse;font-size:12px}.tbl th,.tbl td{border:1px solid #d7e0ea;padding:8px 10px;text-align:left;vertical-align:top}.tbl th{background:#eaf2fb}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media (max-width:1024px){.grid{grid-template-columns:1fr}}.meta{margin-top:8px;font-size:12px;opacity:.95}.hoverwrap{position:relative;display:inline-block;max-width:220px}.hoverbtn{display:inline-block;padding:2px 8px;border-radius:10px;background:#eef4ff;color:#0a4f98;font-size:11px}.hoverpanel{display:none;position:absolute;z-index:20;left:0;top:22px;width:360px;max-height:240px;overflow:auto;background:#fff;border:1px solid #bfd1e7;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.12);padding:8px}.hoverpanel a{display:block;font-size:12px;line-height:1.45;color:#0b5cab;text-decoration:none;padding:2px 0}.hoverpanel a:hover{text-decoration:underline}.hoverwrap:hover .hoverpanel{display:block}.more{font-size:11px;color:#4f6680;padding-top:4px}.na{color:#7a8796;font-size:11px}</style></head><body>')
    parts.append(f'<div class="wrap"><section class="hero"><h1>Lenovo WiFi/BT Root Cause Theme Top4 by Year (2021-2026)</h1><p>Refreshed from Lenovo source CSV with watchdog/reset-flow override rules.</p><div class="meta">Total Issue Count from rows: <b>{rows_total}</b> | Total Issue Count in summary tables: <b>{summary_total}</b></div></section>')

    for y in range(2021, 2027):
        yt = summary[summary["Year"] == y]
        w = yt[yt["Technology"] == "WIFI"]
        b = yt[yt["Technology"] == "BT"]
        parts.append(f'<section class="sec"><h2>{y}</h2><div class="grid"><div><h3>WIFI</h3>{render_table(w)}</div><div><h3>BT</h3>{render_table(b)}</div></div></section>')

    parts.append("</div></body></html>")
    return "".join(parts)


def main() -> None:
    summary_map = load_summary_map()
    root_map = load_rootcause_map()

    rows = pd.read_csv(ROWS_PATH, encoding="utf-8-sig")
    for c in ["Issue key", "Technology", "Model Category", "Root Cause", "Root Cause Theme"]:
        rows[c] = rows[c].fillna("").astype(str)
    rows["Issue key"] = rows["Issue key"].str.strip()
    rows["Year"] = pd.to_numeric(rows["Year"], errors="coerce").fillna(0).astype(int)

    # Remove rows with empty root cause before any classification logic.
    before_drop = len(rows)
    rows["Root Cause"] = rows["Root Cause"].fillna("").astype(str).str.strip()
    rows = rows[rows["Root Cause"] != ""].copy()
    dropped_blank_root = before_drop - len(rows)

    ref_resolved = apply_reference_root_resolution(rows, root_map)
    rows, llm_used = llm_classify_rows(rows, summary_map)
    yb_changed = apply_yb_lost_overrides(rows, summary_map)
    summary_changed = apply_summary_category_overrides(rows, summary_map)
    ref_theme_changed = apply_reference_theme_overrides(rows)
    signal_theme_changed = apply_theme_signal_overrides(rows)
    changed = apply_category_overrides(rows)
    rows.to_csv(ROWS_PATH, index=False, encoding="utf-8-sig")

    summary = build_summary(rows)
    summary.to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")

    html_text = render_html(rows, summary)
    REPORT_PATH.write_text(html_text, encoding="utf-8")

    print("llm_used=", llm_used)
    print("dropped_blank_root_cause_rows=", dropped_blank_root)
    print("reference_root_resolved_count=", len(ref_resolved))
    print("reference_root_resolved_keys=", ref_resolved)
    print("yb_lost_override_count=", len(yb_changed))
    print("yb_lost_override_keys=", yb_changed)
    print("summary_override_count=", len(summary_changed))
    print("summary_override_keys=", summary_changed)
    print("reference_theme_override_count=", len(ref_theme_changed))
    print("reference_theme_override_keys=", ref_theme_changed)
    print("signal_theme_override_count=", len(signal_theme_changed))
    print("signal_theme_override_keys=", signal_theme_changed)
    print("override_changed_count=", len(changed))
    print("override_changed_keys=", changed)
    print("rows_total=", len(rows), "summary_total=", int(summary["Issue Count"].sum()))
    print("report=", REPORT_PATH)


if __name__ == "__main__":
    main()
