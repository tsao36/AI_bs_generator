from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_DIR = os.path.dirname(_SCRIPT_DIR)
if _WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, _WORKSPACE_DIR)

from Wireless_bug_dashboard import DbConnector  # type: ignore
from issue_category_model import classify_issue_title, load_category_model
from offload_reporter_issues import _created_date_expr, _get_table_columns, _has, _title_expr


_EXPOSURE_ORDER = {
    "1-Critical": 0,
    "2-High": 1,
    "3-Medium": 2,
    "4-Low": 3,
}


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.upper() == "NA" else text


def _is_valid_jira_id(value: Any) -> bool:
    jira_id = _clean(value)
    if not jira_id:
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+$", jira_id))


def _dedupe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        year = _clean(row.get("issue_year"))
        ips_case_number = _clean(row.get("ips_case_number"))
        title = _clean(row.get("ips_title"))
        jira_id = _clean(row.get("jira_id")).lower()
        tech = _clean(row.get("technology")).lower()
        key = (year, ips_case_number, jira_id, title.lower(), tech)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _exposure_bucket(exposure: str) -> str:
    text = _clean(exposure)
    return text if text else "(missing)"


def _canonicalize_exposure(exposure: str) -> str:
    text = _clean(exposure).lower()
    if not text:
        return ""

    if re.search(r"\b1\b", text) or "critical" in text or "blocker" in text or "stopper" in text:
        return "1-Critical"
    if re.search(r"\b2\b", text) or "high" in text:
        return "2-High"
    if re.search(r"\b3\b", text) or "medium" in text or re.search(r"\bmed\b", text):
        return "3-Medium"
    if re.search(r"\b4\b", text) or "low" in text:
        return "4-Low"
    return _clean(exposure)


def _map_ips_priority_to_exposure(ips_priority: str) -> str:
    text = _clean(ips_priority).lower()
    if not text:
        return ""

    p_match = re.search(r"\bp\s*([0-4])\b", text)
    if p_match:
        p = p_match.group(1)
        if p == "0":
            return "1-Critical"
        if p == "1":
            return "2-High"
        if p == "2":
            return "3-Medium"
        return "4-Low"

    if "critical" in text or "stopper" in text or "blocker" in text:
        return "1-Critical"
    if "high" in text:
        return "2-High"
    if "medium" in text or "med" in text:
        return "3-Medium"
    if "low" in text:
        return "4-Low"
    return ""


def _resolve_exposure(jira_exposure: str, ips_priority: str) -> str:
    jira_value = _canonicalize_exposure(jira_exposure)
    if jira_value:
        return jira_value
    mapped = _map_ips_priority_to_exposure(ips_priority)
    return mapped if mapped else "(missing)"


def _normalize_technology(raw: str) -> str:
    text = _clean(raw).lower()
    if text in {"wifi", "wi-fi", "wlan"}:
        return "WiFi"
    if text in {"bt", "bluetooth"}:
        return "BT"
    return "Other"


_ARROW_LAKE_VARIANTS = {
    "arrow lake", "arrow lake-h", "arrow lake-hx", "arrow lake-s", "arrow lake-u",
    "arrow lake-p", "arrow lake-m",
}


def _normalize_platform(name: str) -> str:
    if name.lower() in _ARROW_LAKE_VARIANTS:
        return "Arrow Lake"
    return name


def _resolve_platform(jira_platform: str, ips_platform: str) -> str:
    # Mirror DAX rule: IF(jira_platform <> "NA", jira_platform, ips_platform)
    jira_val = _clean(jira_platform)
    if jira_val:
        return _normalize_platform(jira_val)
    ips_val = _clean(ips_platform)
    return _normalize_platform(ips_val) if ips_val else "(missing)"


def _is_sw_fix_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _clean(value).lower()
    return text in {"1", "true", "t", "yes", "y"}


def _sorted_counter_items(counter: Counter, key_type: str = "default") -> List[Tuple[str, int]]:
    if key_type == "exposure":
        return sorted(
            counter.items(),
            key=lambda kv: (_EXPOSURE_ORDER.get(kv[0], 999), -kv[1], kv[0]),
        )
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def _order_exposure_breakdown(rows: List[Dict[str, Any]], key_name: str) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (_EXPOSURE_ORDER.get(str(r.get(key_name, "")), 999), str(r.get(key_name, ""))))


def _assess_concern(values: List[float], epsilon: float = 0.2) -> str:
    if len(values) < 2:
        return "Insufficient Data"

    deltas = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    inc = all(d > epsilon for d in deltas)
    dec = all(d < -epsilon for d in deltas)
    flat = all(abs(d) <= epsilon for d in deltas)
    start = values[0]
    end = values[-1]
    recent_delta = deltas[-1]

    if inc:
        return "Rising concern"
    if dec:
        return "Improving trend"
    if flat:
        return "Stable"

    max_idx = max(range(len(values)), key=lambda i: values[i])
    min_idx = min(range(len(values)), key=lambda i: values[i])

    if 0 < max_idx < (len(values) - 1) and recent_delta < -epsilon:
        if end <= start + epsilon:
            return "Peaked, now trending downward"
        return "Peaked, easing but above baseline"
    if 0 < min_idx < (len(values) - 1) and recent_delta > epsilon:
        if end >= start - epsilon:
            return "Bottomed out, now trending upward"
        return "Bottomed out, partial rebound"

    net = end - start
    if net > epsilon and recent_delta > epsilon:
        return "Mixed, but concern rising"
    if net < -epsilon and recent_delta < -epsilon:
        return "Mixed, but improving"
    if recent_delta > epsilon:
        return "Mixed, recently worsening"
    if recent_delta < -epsilon:
        return "Mixed, recently improving"
    return "Mixed, no clear directional signal"


def _pct_breakdown(counter: Counter, total: int, key_name: str, key_type: str = "default") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key, cnt in _sorted_counter_items(counter, key_type):
        out.append(
            {
                key_name: key,
                "count": int(cnt),
                "pct": round((cnt / total) * 100.0, 2) if total > 0 else 0.0,
            }
        )
    return out


def _parse_date(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    for candidate in (text, text[:10]):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _calc_tat_days(created_value: Any, closed_value: Any) -> float | None:
    created_dt = _parse_date(created_value)
    closed_dt = _parse_date(closed_value)
    if created_dt is None or closed_dt is None:
        return None
    delta_days = (closed_dt.date() - created_dt.date()).days
    if delta_days < 0:
        return None
    return float(delta_days)


def _format_avg_tat(values: List[float]) -> str:
    if not values:
        return "N/A"
    return f"{round(sum(values) / len(values), 2)}"


def _build_issue_trend_comment(labels: List[str], values: List[int], scope_label: str) -> str:
    if not labels or not values or len(labels) != len(values):
        return f"Comment: No {scope_label.lower()} issue trend data available."

    assessment = _assess_concern([float(v) for v in values])
    max_idx = max(range(len(values)), key=lambda i: values[i])
    min_idx = min(range(len(values)), key=lambda i: values[i])
    return (
        f"Comment: {scope_label} issue volume is {assessment.lower()}. "
        f"Highest at {labels[max_idx]} ({values[max_idx]} issues) and lowest at {labels[min_idx]} ({values[min_idx]} issues)."
    )


def _query_issues(db: DbConnector, table: str, start_year: int, end_year: int, customer_filter: str = "lenovo") -> List[Dict[str, Any]]:
    columns = _get_table_columns(db, table)
    created_expr = _created_date_expr(columns)
    if created_expr == "NULL":
        raise RuntimeError(f"No created date columns found in table: {table}")

    customer_cols = [
        "customer",
        "jira_customer",
        "ips_customer",
        "customer_name",
        "jira_customer_name",
        "ips_customer_name",
        "oem_customer",
    ]
    existing_customer_cols = [col for col in customer_cols if _has(columns, col)]

    if customer_filter == "lenovo":
        if not existing_customer_cols:
            raise RuntimeError(
                "No customer columns found for Lenovo filtering. "
                f"Checked: {', '.join(customer_cols)}"
            )
        customer_clause = " OR ".join(
            [
                "LOWER(TRIM(COALESCE({col}::text, ''))) IN ('lenovo', 'lenovo ideapad', 'lenovo thinkpad')".format(
                    col=col
                )
                for col in existing_customer_cols
            ]
        )
        where_customer = f"WHERE ({customer_clause})"
    else:
        # all customers — no customer filter
        where_customer = ""

    title_expr = _title_expr(columns)
    jira_exposure_expr = "jira_exposure::text" if _has(columns, "jira_exposure") else "NULL::text"
    ips_priority_expr = "ips_priority::text" if _has(columns, "ips_priority") else "NULL::text"
    ips_case_number_expr = "ips_case_number::text" if _has(columns, "ips_case_number") else "NULL::text"
    jira_id_expr = "jira_id::text" if _has(columns, "jira_id") else "NULL::text"
    jira_platform_expr = "jira_platform::text" if _has(columns, "jira_platform") else "NULL::text"
    ips_platform_expr = "ips_platform::text" if _has(columns, "ips_platform") else "NULL::text"
    jira_is_sw_change_expr = "jira_is_sw_change::text" if _has(columns, "jira_is_sw_change") else "NULL::text"
    ips_created_date_expr = "ips_created_date::text" if _has(columns, "ips_created_date") else "NULL::text"
    ips_closed_date_expr = "ips_closed_date::text" if _has(columns, "ips_closed_date") else "NULL::text"

    if _has(columns, "technology"):
        technology_expr = "technology::text"
    elif _has(columns, "bug_project"):
        technology_expr = (
            "CASE "
            "WHEN LOWER(TRIM(COALESCE(bug_project::text, ''))) = 'wifi' THEN 'WiFi' "
            "WHEN LOWER(TRIM(COALESCE(bug_project::text, ''))) = 'bt' THEN 'BT' "
            "ELSE 'Other' END"
        )
    else:
        technology_expr = (
            "CASE "
            "WHEN UPPER(COALESCE(jira_id::text, '')) LIKE 'WIFI-%' THEN 'WiFi' "
            "WHEN UPPER(COALESCE(jira_id::text, '')) LIKE 'BT-%' THEN 'BT' "
            "ELSE 'Other' END"
        )

    ips_category_expr = "ips_category::text" if _has(columns, "ips_category") else "NULL::text"
    # Only include WiFi and BT entries; exclude WCS Innovation Engineering and other non-wireless categories
    if _has(columns, "bug_project"):
        category_clause = (
            "("
            "LOWER(TRIM(COALESCE(bug_project::text, ''))) IN ('wifi', 'bt', 'wot', 'dbgt')"
            " OR ("
            "LOWER(TRIM(COALESCE(bug_project::text, ''))) IN ('', 'na')"
            " AND LOWER(TRIM(COALESCE(ips_category::text, ''))) IN "
            "('wifi windows', 'wifi linux', 'wifi amt', 'wireless > wifi', 'bluetooth (bt)', 'wireless > bluetooth')"
            ")"
            ")"
        )
    elif _has(columns, "ips_category"):
        category_clause = (
            "LOWER(TRIM(COALESCE(ips_category::text, ''))) IN "
            "('wifi windows', 'wifi linux', 'wifi amt', 'wireless > wifi', 'bluetooth (bt)', 'wireless > bluetooth')"
        )
    else:
        category_clause = ""

    # Build the WHERE clause for the src CTE combining customer and category filters
    src_where_parts = []
    if where_customer:
        # where_customer already starts with "WHERE (...)", extract the condition
        src_where_parts.append(where_customer[len("WHERE "):].strip())
    if category_clause:
        src_where_parts.append(f"({category_clause})")
    src_where = f"WHERE {' AND '.join(src_where_parts)}" if src_where_parts else ""

    query = f"""
        WITH src AS (
            SELECT
                {created_expr} AS created_date,
                {title_expr} AS ips_title,
                {jira_exposure_expr} AS jira_exposure,
                {ips_priority_expr} AS ips_priority,
                {ips_case_number_expr} AS ips_case_number,
                {jira_id_expr} AS jira_id,
                {technology_expr} AS technology,
                {jira_platform_expr} AS jira_platform,
                {ips_platform_expr} AS ips_platform,
                {jira_is_sw_change_expr} AS jira_is_sw_change,
                {ips_created_date_expr} AS ips_created_date,
                {ips_closed_date_expr} AS ips_closed_date
            FROM {table}
            {src_where}
        )
        SELECT
            EXTRACT(YEAR FROM created_date)::int AS issue_year,
            COALESCE(NULLIF(TRIM(ips_title), ''), '') AS ips_title,
            COALESCE(NULLIF(TRIM(jira_exposure), ''), '') AS jira_exposure,
            COALESCE(NULLIF(TRIM(ips_priority), ''), '') AS ips_priority,
            COALESCE(NULLIF(TRIM(ips_case_number), ''), '') AS ips_case_number,
            COALESCE(NULLIF(TRIM(jira_id), ''), '') AS jira_id,
            COALESCE(NULLIF(TRIM(technology), ''), 'Other') AS technology,
            COALESCE(NULLIF(TRIM(jira_platform), ''), '') AS jira_platform,
            COALESCE(NULLIF(TRIM(ips_platform), ''), '') AS ips_platform,
            COALESCE(NULLIF(TRIM(jira_is_sw_change), ''), '') AS jira_is_sw_change,
            COALESCE(NULLIF(TRIM(ips_created_date), ''), '') AS ips_created_date,
            COALESCE(NULLIF(TRIM(ips_closed_date), ''), '') AS ips_closed_date
        FROM src
                WHERE created_date >= %s::date
                    AND created_date < %s::date
          AND ips_title IS NOT NULL
          AND TRIM(ips_title) <> ''
        ORDER BY created_date ASC;
    """
    params = [f"{start_year}-01-01", f"{end_year + 1}-01-01"]
    rows = db.query_rows(query, params)
    return _dedupe_rows(rows)


def _analyze_rows(
    rows: List[Dict[str, Any]],
    model_path: str,
    start_year: int,
    end_year: int,
) -> Dict[str, Any]:
    model_bundle = load_category_model(model_path)

    per_year: Dict[int, Dict[str, Any]] = {}
    for year in range(start_year, end_year + 1):
        per_year[year] = {
            "total_issues": 0,
            "technology_counts": Counter(),
            "platform_counts": Counter(),
            "by_technology": {
                "WiFi": {
                    "total_issues": 0,
                    "issue_category_counts": Counter(),
                    "jira_exposure_counts": Counter(),
                    "platform_counts": Counter(),
                },
                "BT": {
                    "total_issues": 0,
                    "issue_category_counts": Counter(),
                    "jira_exposure_counts": Counter(),
                    "platform_counts": Counter(),
                },
                "Other": {
                    "total_issues": 0,
                    "issue_category_counts": Counter(),
                    "jira_exposure_counts": Counter(),
                    "platform_counts": Counter(),
                },
            },
        }

    detailed_rows: List[Dict[str, Any]] = []

    for row in rows:
        year = int(row.get("issue_year"))
        if year not in per_year:
            continue

        title = _clean(row.get("ips_title"))
        exposure_raw = _clean(row.get("jira_exposure"))
        ips_priority = _clean(row.get("ips_priority"))
        exposure_bucket = _resolve_exposure(exposure_raw, ips_priority)
        technology = _normalize_technology(_clean(row.get("technology")))
        platform = _resolve_platform(_clean(row.get("jira_platform")), _clean(row.get("ips_platform")))

        category, confidence = classify_issue_title(
            model_bundle,
            title,
            predicted_category="",
            technology=technology,
        )

        y = per_year[year]
        y["total_issues"] += 1
        y["technology_counts"][technology] += 1
        y["platform_counts"][platform] += 1

        by_t = y["by_technology"][technology]
        by_t["total_issues"] += 1
        by_t["issue_category_counts"][category] += 1
        by_t["jira_exposure_counts"][exposure_bucket] += 1
        by_t["platform_counts"][platform] += 1

        detailed_rows.append(
            {
                "issue_year": year,
                "jira_id": _clean(row.get("jira_id")),
                "ips_title": title,
                "technology": technology,
                "platform": platform,
                "jira_platform": _clean(row.get("jira_platform")),
                "ips_platform": _clean(row.get("ips_platform")),
                "jira_is_sw_change": _clean(row.get("jira_is_sw_change")),
                "ips_created_date": _clean(row.get("ips_created_date")),
                "ips_closed_date": _clean(row.get("ips_closed_date")),
                "ips_priority": ips_priority,
                "deduced_issue_category": category,
                "category_confidence": round(float(confidence), 4),
                "jira_exposure": exposure_bucket,
            }
        )

    for year, stats in per_year.items():
        total = int(stats["total_issues"])
        stats["technology_breakdown"] = _pct_breakdown(stats["technology_counts"], total, "technology")
        stats["platform_breakdown"] = _pct_breakdown(stats["platform_counts"], total, "platform")

        for tech in ["WiFi", "BT", "Other"]:
            t_stats = stats["by_technology"][tech]
            t_total = int(t_stats["total_issues"])
            t_stats["issue_category_breakdown"] = _pct_breakdown(
                t_stats["issue_category_counts"],
                t_total,
                "issue_category",
            )
            t_stats["jira_exposure_breakdown"] = _pct_breakdown(
                t_stats["jira_exposure_counts"],
                t_total,
                "jira_exposure",
                "exposure",
            )
            t_stats["platform_breakdown"] = _pct_breakdown(
                t_stats["platform_counts"],
                t_total,
                "platform",
            )
            del t_stats["issue_category_counts"]
            del t_stats["jira_exposure_counts"]
            del t_stats["platform_counts"]

        del stats["technology_counts"]
        del stats["platform_counts"]

    yoy: List[Dict[str, Any]] = []
    for year in range(start_year + 1, end_year + 1):
        prev = per_year[year - 1]
        curr = per_year[year]

        prev_wifi = prev["by_technology"]["WiFi"]["total_issues"]
        curr_wifi = curr["by_technology"]["WiFi"]["total_issues"]
        prev_bt = prev["by_technology"]["BT"]["total_issues"]
        curr_bt = curr["by_technology"]["BT"]["total_issues"]

        yoy.append(
            {
                "year": year,
                "vs_year": year - 1,
                "issues_delta": int(curr["total_issues"] - prev["total_issues"]),
                "issues_growth_pct": round(
                    ((curr["total_issues"] - prev["total_issues"]) / prev["total_issues"]) * 100.0,
                    2,
                ) if prev["total_issues"] > 0 else None,
                "wifi_issues_delta": int(curr_wifi - prev_wifi),
                "wifi_issues_growth_pct": round(((curr_wifi - prev_wifi) / prev_wifi) * 100.0, 2) if prev_wifi > 0 else None,
                "bt_issues_delta": int(curr_bt - prev_bt),
                "bt_issues_growth_pct": round(((curr_bt - prev_bt) / prev_bt) * 100.0, 2) if prev_bt > 0 else None,
            }
        )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start_year": start_year,
        "end_year": end_year,
        "years": {str(year): per_year[year] for year in range(start_year, end_year + 1)},
        "year_over_year": yoy,
        "detailed_rows": detailed_rows,
    }


def _build_html_report(result: Dict[str, Any], output_dir: str, customer_label: str = "lenovo") -> str:
    years = [str(y) for y in range(int(result["start_year"]), int(result["end_year"]) + 1)]
    by_year = result["years"]
    detailed_rows = result.get("detailed_rows", [])

    yearly_rows = []
    tat_year_values: Dict[str, List[float]] = {y: [] for y in years}
    tat_platform_values: Dict[str, List[float]] = defaultdict(list)

    for r in detailed_rows:
        tat_days = _calc_tat_days(r.get("ips_created_date"), r.get("ips_closed_date"))
        if tat_days is None:
            continue
        year_key = str(r.get("issue_year") or "")
        if year_key in tat_year_values:
            tat_year_values[year_key].append(tat_days)
        platform_name = _clean(r.get("platform")) or "(missing)"
        tat_platform_values[platform_name].append(tat_days)

    for y in years:
        item = by_year[y]
        ips_all = int(item["total_issues"])
        ips_wifi = int(item["by_technology"]["WiFi"]["total_issues"])
        ips_bt = int(item["by_technology"]["BT"]["total_issues"])

        jira_total = 0
        jira_wifi = 0
        jira_bt = 0
        fixed_total = 0
        for r in detailed_rows:
            if str(r.get("issue_year") or "") != y:
                continue
            if _is_sw_fix_flag(r.get("jira_is_sw_change")):
                fixed_total += 1
            if not _is_valid_jira_id(r.get("jira_id")):
                continue
            tech = _clean(r.get("technology"))
            if tech == "WiFi":
                jira_wifi += 1
            elif tech == "BT":
                jira_bt += 1
        jira_total = jira_wifi + jira_bt
        conversion_rate = round((jira_total / ips_all) * 100.0, 2) if ips_all > 0 else 0.0
        issue_fixed_rate = round((fixed_total / ips_all) * 100.0, 2) if ips_all > 0 else 0.0

        yearly_rows.append(
            {
                "year": y,
                "ips_total": ips_all,
                "ips_wifi": ips_wifi,
                "ips_bt": ips_bt,
                "jira_wifi": jira_wifi,
                "jira_bt": jira_bt,
                "jira_total": jira_total,
                "ips_to_jira_conversion_rate": conversion_rate,
                "total_issue_closed_with_fix": fixed_total,
                "issue_fixed_rate": issue_fixed_rate,
                "avg_bug_tat_days": _format_avg_tat(tat_year_values.get(y, [])),
            }
        )

    chart_data: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for y in years:
        chart_data[y] = {}
        for tech in ["WiFi", "BT"]:
            t = by_year[y]["by_technology"][tech]
            exposure_rows = _order_exposure_breakdown(t["jira_exposure_breakdown"], "jira_exposure")
            chart_data[y][tech] = {
                "issue_category": {
                    "labels": [x["issue_category"] for x in t["issue_category_breakdown"]],
                    "values": [x["pct"] for x in t["issue_category_breakdown"]],
                },
                "jira_exposure": {
                    "labels": [x["jira_exposure"] for x in exposure_rows],
                    "values": [x["pct"] for x in exposure_rows],
                },
            }

    category_counter = Counter()
    exposure_counter = Counter()
    for r in detailed_rows:
        if str(r.get("issue_year") or "") not in years:
            continue
        tech = _clean(r.get("technology"))
        if tech not in {"WiFi", "BT"}:
            continue
        category_name = _clean(r.get("deduced_issue_category")) or "(missing)"
        if category_name.strip().lower() in {"yb", "yb/lost", "yb lost"}:
            category_name = "YB/Lost"
        category_counter[category_name] += 1
        exposure_counter[_clean(r.get("jira_exposure")) or "(missing)"] += 1

    overall_total = sum(category_counter.values())

    top_categories = sorted(
        [(name, cnt) for name, cnt in category_counter.items() if str(name).strip().lower() != "icps"],
        key=lambda kv: (-kv[1], kv[0]),
    )[:5]
    top_exposures: List[Tuple[str, int]] = []
    for exposure in ["1-Critical", "2-High", "3-Medium", "4-Low"]:
        cnt = int(exposure_counter.get(exposure, 0))
        if cnt > 0:
            top_exposures.append((exposure, cnt))
    for name, cnt in sorted(exposure_counter.items(), key=lambda kv: (-kv[1], kv[0])):
        if name not in _EXPOSURE_ORDER:
            top_exposures.append((name, int(cnt)))
    top_exposures = top_exposures[:5]

    overall_wifi_bt_total = sum(int(row["ips_total"]) for row in yearly_rows)
    first_year_total = int(yearly_rows[0]["ips_total"]) if yearly_rows else 0
    last_year_total = int(yearly_rows[-1]["ips_total"]) if yearly_rows else 0
    total_change_pct = round(((last_year_total - first_year_total) / first_year_total) * 100.0, 2) if first_year_total > 0 else 0.0
    top_category_name = top_categories[0][0] if top_categories else "N/A"
    top_category_pct = round((top_categories[0][1] / overall_total) * 100.0, 2) if top_categories and overall_total > 0 else 0.0
    top_exposure_name = top_exposures[0][0] if top_exposures else "N/A"
    top_exposure_pct = round((top_exposures[0][1] / overall_total) * 100.0, 2) if top_exposures and overall_total > 0 else 0.0

    platform_totals: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "wifi": 0, "bt": 0})
    for r in detailed_rows:
        platform_name = _clean(r.get("platform")) or "(missing)"
        technology = _clean(r.get("technology"))
        if technology == "WiFi":
            platform_totals[platform_name]["wifi"] += 1
        elif technology == "BT":
            platform_totals[platform_name]["bt"] += 1
        platform_totals[platform_name]["total"] = platform_totals[platform_name]["wifi"] + platform_totals[platform_name]["bt"]

    platform_rows = []
    focus_platforms = ["Meteor Lake", "Lunar Lake", "Arrow Lake", "Panther Lake"]
    platform_table_specs = [
        ("Alder Lake", "Alder Lake"),
        ("Raptor Lake Client Platforms", "Raptor Lake"),
        ("Meteor Lake", "Meteor Lake"),
        ("Lunar Lake", "Lunar Lake"),
        ("Arrow Lake", "Arrow Lake"),
        ("Panther Lake", "Panther Lake"),
    ]
    for source_name, display_name in platform_table_specs:
        counts = platform_totals.get(source_name, {"total": 0, "wifi": 0, "bt": 0})
        ips_wifi = int(counts["wifi"])
        ips_bt = int(counts["bt"])
        ips_total = int(counts.get("total", 0))

        jira_total = 0
        jira_wifi = 0
        jira_bt = 0
        fixed_total = 0
        for r in detailed_rows:
            if _clean(r.get("platform")) != source_name:
                continue
            if _is_sw_fix_flag(r.get("jira_is_sw_change")):
                fixed_total += 1
            if not _is_valid_jira_id(r.get("jira_id")):
                continue
            tech = _clean(r.get("technology"))
            if tech == "WiFi":
                jira_wifi += 1
            elif tech == "BT":
                jira_bt += 1
        jira_total = jira_wifi + jira_bt
        conversion_rate = round((jira_total / ips_total) * 100.0, 2) if ips_total > 0 else 0.0
        issue_fixed_rate = round((fixed_total / ips_total) * 100.0, 2) if ips_total > 0 else 0.0

        platform_rows.append(
            {
                "platform": display_name,
                "ips_total": ips_total,
                "ips_wifi": ips_wifi,
                "ips_bt": ips_bt,
                "jira_wifi": jira_wifi,
                "jira_bt": jira_bt,
                "jira_total": jira_total,
                "ips_to_jira_conversion_rate": conversion_rate,
                "total_issue_closed_with_fix": fixed_total,
                "issue_fixed_rate": issue_fixed_rate,
                "avg_bug_tat_days": _format_avg_tat(tat_platform_values.get(source_name, [])),
            }
        )

    overall_line_rows = [row for row in yearly_rows if 2021 <= int(row["year"]) <= 2025]
    if not overall_line_rows:
        overall_line_rows = yearly_rows[:5]

    overall_line_data = {
        "labels": [str(row["year"]) for row in overall_line_rows],
        "ips": [int(row["ips_total"]) for row in overall_line_rows],
        "jira": [int(row["jira_total"]) for row in overall_line_rows],
        "fixed": [int(row["total_issue_closed_with_fix"]) for row in overall_line_rows],
    }
    platform_line_data = {
        "labels": [str(row["platform"]) for row in platform_rows],
        "ips": [int(row["ips_total"]) for row in platform_rows],
        "jira": [int(row["jira_total"]) for row in platform_rows],
        "fixed": [int(row["total_issue_closed_with_fix"]) for row in platform_rows],
    }
    tat_year_rows = [
        {
            "year": y,
            "avg_bug_tat_days": _format_avg_tat(tat_year_values.get(y, [])),
        }
        for y in years
    ]
    tat_year_line_data = {
        "labels": [str(row["year"]) for row in tat_year_rows],
        "values": [
            float(row["avg_bug_tat_days"]) if str(row["avg_bug_tat_days"]).strip().upper() != "N/A" else None
            for row in tat_year_rows
        ],
    }
    tat_platform_rows = [
        {
            "platform": row["platform"],
            "avg_bug_tat_days": row["avg_bug_tat_days"],
        }
        for idx, row in enumerate(platform_rows)
    ]
    tat_platform_line_data = {
        "labels": [str(row["platform"]) for row in tat_platform_rows],
        "values": [
            float(row["avg_bug_tat_days"]) if str(row["avg_bug_tat_days"]).strip().upper() != "N/A" else None
            for row in tat_platform_rows
        ],
    }
    overall_trend_comment = _build_issue_trend_comment(
        overall_line_data["labels"],
        overall_line_data["ips"],
        "Total",
    )
    platform_trend_comment = _build_issue_trend_comment(
        platform_line_data["labels"],
        platform_line_data["ips"],
        "Platform",
    )

    yb_per_year: Dict[str, Dict[str, int]] = {y: {"total": 0, "wifi": 0, "bt": 0} for y in years}
    yb_per_platform: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "wifi": 0, "bt": 0})
    for r in detailed_rows:
        if _clean(r.get("deduced_issue_category")) != "YB/Lost":
            continue

        year_key = str(r.get("issue_year") or "")
        if year_key in yb_per_year:
            yb_per_year[year_key]["total"] += 1

        platform_name = _clean(r.get("platform")) or "(missing)"
        yb_per_platform[platform_name]["total"] += 1

        technology = _clean(r.get("technology"))
        if technology == "WiFi":
            if year_key in yb_per_year:
                yb_per_year[year_key]["wifi"] += 1
            yb_per_platform[platform_name]["wifi"] += 1
        elif technology == "BT":
            if year_key in yb_per_year:
                yb_per_year[year_key]["bt"] += 1
            yb_per_platform[platform_name]["bt"] += 1

    yb_year_rows: List[Dict[str, Any]] = []
    for y in years:
        total_issues_year = int(by_year[y]["by_technology"]["WiFi"]["total_issues"]) + int(by_year[y]["by_technology"]["BT"]["total_issues"])
        yb_wifi = int(yb_per_year[y]["wifi"])
        yb_bt = int(yb_per_year[y]["bt"])
        yb_count = yb_wifi + yb_bt
        yb_year_rows.append(
            {
                "year": y,
                "yb_total": yb_count,
                "wifi": yb_wifi,
                "bt": yb_bt,
                "yb_pct": round((yb_count / total_issues_year) * 100.0, 2) if total_issues_year > 0 else 0.0,
            }
        )

    yb_platform_rows: List[Dict[str, Any]] = []
    yb_platform_table_specs = [
        ("Alder Lake", "Alder Lake"),
        ("Raptor Lake Client Platforms", "Raptor Lake"),
        ("Meteor Lake", "Meteor Lake"),
        ("Lunar Lake", "Lunar Lake"),
        ("Arrow Lake", "Arrow Lake"),
        ("Panther Lake", "Panther Lake"),
    ]
    for source_name, display_name in yb_platform_table_specs:
        counts = yb_per_platform.get(source_name, {"total": 0, "wifi": 0, "bt": 0})
        total_platform_issues = int(platform_totals.get(source_name, {}).get("wifi", 0)) + int(platform_totals.get(source_name, {}).get("bt", 0))
        yb_wifi = int(counts["wifi"])
        yb_bt = int(counts["bt"])
        yb_count = yb_wifi + yb_bt
        yb_platform_rows.append(
            {
                "platform": display_name,
                "yb_total": yb_count,
                "wifi": yb_wifi,
                "bt": yb_bt,
                "yb_pct_within_platform": round((yb_count / total_platform_issues) * 100.0, 2) if total_platform_issues > 0 else 0.0,
            }
        )

    yb_year_series = [float(row["yb_pct"]) for row in yb_year_rows]
    yb_year_assessment = _assess_concern(yb_year_series)

    yb_platform_series = [
        round(((int(yb_per_platform.get(p, {}).get("wifi", 0)) + int(yb_per_platform.get(p, {}).get("bt", 0))) / (int(platform_totals.get(p, {}).get("wifi", 0)) + int(platform_totals.get(p, {}).get("bt", 0)))) * 100.0, 2)
        if (int(platform_totals.get(p, {}).get("wifi", 0)) + int(platform_totals.get(p, {}).get("bt", 0))) > 0
        else 0.0
        for p in focus_platforms
    ]
    yb_platform_assessment = _assess_concern(yb_platform_series)

    wifi_yb_rate_year: List[float] = []
    bt_yb_rate_year: List[float] = []
    for y in years:
        wifi_total_y = int(by_year[y]["by_technology"]["WiFi"]["total_issues"])
        bt_total_y = int(by_year[y]["by_technology"]["BT"]["total_issues"])
        wifi_yb = int(yb_per_year[y]["wifi"])
        bt_yb = int(yb_per_year[y]["bt"])
        wifi_yb_rate_year.append(round((wifi_yb / wifi_total_y) * 100.0, 2) if wifi_total_y > 0 else 0.0)
        bt_yb_rate_year.append(round((bt_yb / bt_total_y) * 100.0, 2) if bt_total_y > 0 else 0.0)

    wifi_yoy_quality_assessment = _assess_concern(wifi_yb_rate_year)
    bt_yoy_quality_assessment = _assess_concern(bt_yb_rate_year)

    wifi_platform_series: List[float] = []
    bt_platform_series: List[float] = []
    for p in focus_platforms:
        wifi_total_p = int(platform_totals.get(p, {}).get("wifi", 0))
        bt_total_p = int(platform_totals.get(p, {}).get("bt", 0))
        wifi_yb_p = int(yb_per_platform.get(p, {}).get("wifi", 0))
        bt_yb_p = int(yb_per_platform.get(p, {}).get("bt", 0))
        wifi_platform_series.append(round((wifi_yb_p / wifi_total_p) * 100.0, 2) if wifi_total_p > 0 else 0.0)
        bt_platform_series.append(round((bt_yb_p / bt_total_p) * 100.0, 2) if bt_total_p > 0 else 0.0)

    wifi_platform_assessment = _assess_concern(wifi_platform_series)
    bt_platform_assessment = _assess_concern(bt_platform_series)

    range_tag = f"{result['start_year']}_{result['end_year']}"
    html_path = os.path.join(output_dir, f"{customer_label}_issue_analysis_{range_tag}_report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>Customer Issue Analysis Report</title>
    <script src=\"https://cdn.jsdelivr.net/npm/chart.js\"></script>
    <style>
        body {
            font-family: 'Segoe UI', Inter, -apple-system, BlinkMacSystemFont, sans-serif;
            margin: 24px;
            color: #333333;
            background: #f7f8fa;
        }
        h1, h2, h3 { margin: 8px 0; color: #333333; font-weight: 600; }
        .meta { color: #666666; margin-bottom: 18px; }
        table { border-collapse: collapse; width: 100%; margin-bottom: 18px; }
        th, td { border: 1px solid #d1d5db; padding: 8px; text-align: center; }
        th { background: #f3f4f6; color: #333333; }
        .grid { display: grid; grid-template-columns: repeat(2, minmax(320px, 1fr)); gap: 16px; }
        .card {
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 12px;
            background: #ffffff;
            box-shadow: 0 4px 18px rgba(15, 23, 42, 0.04);
        }
        .year-section { margin-top: 18px; border-top: 2px solid #e5e7eb; padding-top: 12px; }
        .pie-wrap { width: 100%; max-width: 288px; aspect-ratio: 1 / 1; margin: 0 auto; }
        .line-wrap { width: 100%; height: 360px; margin: 6px 0 18px; }
        canvas { width: 100% !important; height: 100% !important; }
    </style>
</head>
<body>
""")
        customer_display = "Lenovo only (Lenovo/Lenovo IdeaPad/Lenovo ThinkPad)" if customer_label == "lenovo" else "All Customers"
        title_prefix = "Lenovo" if customer_label == "lenovo" else "All Customers"
        f.write(f"<h1>{title_prefix} Customer Issue Analysis ({years[0]}-{years[-1]})</h1>\n")
        f.write(
            f"<div class='meta'>Generated at: {result['generated_at']} | Scope: ips_jira_bugs | "
            f"Customer filter: {customer_display} | "
            "Breakdown: WiFi vs BT | Metrics: jira_exposure% and issue_category%</div>\n"
        )

        f.write("<h2>Executive Snapshot</h2>\n")
        f.write("<ul>")
        f.write(f"<li>Total {title_prefix} WiFi+BT issues ({years[0]}-{years[-1]}): {overall_wifi_bt_total}.</li>")
        f.write(f"<li>Yearly total moved from {years[0]}: {first_year_total} to {years[-1]}: {last_year_total} ({total_change_pct}%).</li>")
        f.write(f"<li>YB/Lost trend (customer-critical risk): {yb_year_assessment} ({years[0]}: {yb_year_series[0]}% -> {years[-1]}: {yb_year_series[-1]}%).</li>")
        trends_note = ("YB/Lost risk peaked in 2024 and stabilized through 2025-2026, with Bluetooth becoming the primary residual risk and increasingly dominating the YB/Lost mix. Platform risk peaked at Lunar Lake but showed improvement at Panther Lake." if customer_label == "lenovo" else "YB/Lost risk trends shown across all customers. Platform and technology breakdown reflects the full issue population.")
        f.write(f"<li><strong>Trends Overview:</strong> {trends_note}</li>")
        f.write(f"<li>Top issue category: {top_category_name} ({top_category_pct}%).</li>")
        f.write(f"<li>Top exposure bucket: {top_exposure_name} ({top_exposure_pct}%).</li>")
        f.write("</ul>")

        f.write("<h2>Executive Summary</h2>\n")
        f.write("<h3>Customer Concern Analysis</h3>\n")
        f.write("<table><thead><tr><th>Potential Customer Concern</th><th>Assessment</th><th>Evidence</th></tr></thead><tbody>")
        f.write(f"<tr><td>YB/Lost risk may be increasing year over year.</td><td>{yb_year_assessment}</td><td>YB/Lost % by year: {' -> '.join([f'{y}:{v}%' for y, v in zip(years, yb_year_series)])}</td></tr>")
        f.write(f"<tr><td>YB/Lost risk may rise across platform generations (Meteor Lake -> Lunar Lake -> Panther Lake).</td><td>{yb_platform_assessment}</td><td>YB/Lost % within platform: {' -> '.join([f'{p}:{v}%' for p, v in zip(focus_platforms, yb_platform_series)])}</td></tr>")
        f.write(f"<tr><td>Intel WiFi quality may be degrading year over year (YB/Lost used as risk proxy).</td><td>{wifi_yoy_quality_assessment}</td><td>WiFi YB/Lost rate by year: {' -> '.join([f'{y}:{v}%' for y, v in zip(years, wifi_yb_rate_year)])}</td></tr>")
        f.write(f"<tr><td>Intel BT quality may be degrading year over year (YB/Lost used as risk proxy).</td><td>{bt_yoy_quality_assessment}</td><td>BT YB/Lost rate by year: {' -> '.join([f'{y}:{v}%' for y, v in zip(years, bt_yb_rate_year)])}</td></tr>")
        f.write(f"<tr><td>Platform-over-platform WiFi quality may worsen (Meteor -> Lunar -> Panther).</td><td>{wifi_platform_assessment}</td><td>WiFi YB/Lost rate by platform: {' -> '.join([f'{p}:{v}%' for p, v in zip(focus_platforms, wifi_platform_series)])}</td></tr>")
        f.write(f"<tr><td>Platform-over-platform BT quality may worsen (Meteor -> Lunar -> Panther).</td><td>{bt_platform_assessment}</td><td>BT YB/Lost rate by platform: {' -> '.join([f'{p}:{v}%' for p, v in zip(focus_platforms, bt_platform_series)])}</td></tr>")
        f.write("</tbody></table>")

        f.write("<div class='grid'>")
        f.write(f"<div class='card'><h3>Top 5 Issue Categories ({years[0]}-{years[-1]})</h3><table><thead><tr><th>Category</th><th>Count</th><th>%</th></tr></thead><tbody>")
        for name, cnt in top_categories:
            pct = round((cnt / overall_total) * 100.0, 2) if overall_total > 0 else 0.0
            f.write(f"<tr><td>{name}</td><td>{cnt}</td><td>{pct}%</td></tr>")
        f.write("</tbody></table></div>")

        f.write(f"<div class='card'><h3>Top 5 Jira Exposure Buckets ({years[0]}-{years[-1]})</h3><table><thead><tr><th>Exposure</th><th>Count</th><th>%</th></tr></thead><tbody>")
        for name, cnt in top_exposures:
            pct = round((cnt / overall_total) * 100.0, 2) if overall_total > 0 else 0.0
            f.write(f"<tr><td>{name}</td><td>{cnt}</td><td>{pct}%</td></tr>")
        f.write("</tbody></table></div>")
        f.write("</div>")

        f.write("<h2>Overall Statistics by Year</h2>\n")
        f.write("<table><thead><tr><th>Year</th><th>IPS Total</th><th>IPS WiFi</th><th>IPS BT</th><th>JIRA WiFi</th><th>JIRA BT</th><th>JIRA Total</th><th>IPS to JIRA conversion rate</th><th>Total issue closed with fix</th><th>Issue fixed rate</th><th>Average IPS Bug TAT (days)</th></tr></thead><tbody>\n")
        for row in yearly_rows:
            f.write(f"<tr><td>{row['year']}</td><td>{row['ips_total']}</td><td>{row['ips_wifi']}</td><td>{row['ips_bt']}</td><td>{row['jira_wifi']}</td><td>{row['jira_bt']}</td><td>{row['jira_total']}</td><td>{row['ips_to_jira_conversion_rate']}%</td><td>{row['total_issue_closed_with_fix']}</td><td>{row['issue_fixed_rate']}%</td><td>{row['avg_bug_tat_days']}</td></tr>\n")
        f.write("</tbody></table>\n")
        f.write("<h3>Trend Analysis: Rising Issue Volume</h3>\n")
        f.write("<div class='meta'>Total issue trrend 2021-2025</div>\n")
        f.write("<div class='line-wrap'><canvas id='overall_2021_2025_line'></canvas></div>\n")
        f.write(f"<p>{overall_trend_comment}</p>\n")

        f.write(f"<h2>Average IPS Bug Turn Around Time by Year ({years[0]}-{years[-1]})</h2>\n")
        f.write("<table><thead><tr><th>Year</th><th>Average IPS Bug TAT (days)</th></tr></thead><tbody>\n")
        for row in tat_year_rows:
            f.write(f"<tr><td>{row['year']}</td><td>{row['avg_bug_tat_days']}</td></tr>\n")
        f.write("</tbody></table>\n")
        f.write("<h3>Average IPS Bug Turn Around Time by Year Trend</h3>\n")
        f.write("<div class='line-wrap'><canvas id='year_tat_line'></canvas></div>\n")

        f.write("<h2>Platform Breakdown</h2>\n")
        f.write("<table><thead><tr><th>Platform</th><th>IPS Total</th><th>IPS WiFi</th><th>IPS BT</th><th>JIRA WiFi</th><th>JIRA BT</th><th>JIRA Total</th><th>IPS to JIRA conversion rate</th><th>Total issue closed with fix</th><th>Issue fixed rate</th><th>Average IPS Bug TAT (days)</th></tr></thead><tbody>\n")
        for p in platform_rows:
            f.write(f"<tr><td>{p['platform']}</td><td>{p['ips_total']}</td><td>{p['ips_wifi']}</td><td>{p['ips_bt']}</td><td>{p['jira_wifi']}</td><td>{p['jira_bt']}</td><td>{p['jira_total']}</td><td>{p['ips_to_jira_conversion_rate']}%</td><td>{p['total_issue_closed_with_fix']}</td><td>{p['issue_fixed_rate']}%</td><td>{p['avg_bug_tat_days']}</td></tr>\n")
        f.write("</tbody></table>\n")
        f.write("<h3>Platform Breakdown Trend Graph</h3>\n")
        f.write("<div class='line-wrap'><canvas id='platform_breakdown_line'></canvas></div>\n")
        f.write(f"<p>{platform_trend_comment}</p>\n")

        f.write("<h2>Average IPS Bug Turn Around Time by Platform</h2>\n")
        f.write("<table><thead><tr><th>Platform</th><th>Average IPS Bug TAT (days)</th></tr></thead><tbody>\n")
        for row in tat_platform_rows:
            f.write(f"<tr><td>{row['platform']}</td><td>{row['avg_bug_tat_days']}</td></tr>\n")
        f.write("</tbody></table>\n")
        f.write("<h3>Average IPS Bug Turn Around Time by Platform Trend</h3>\n")
        f.write("<div class='line-wrap'><canvas id='platform_tat_line'></canvas></div>\n")

        f.write("<h2>YB/Lost Breakdown by Year</h2>\n")
        f.write("<table><thead><tr><th>Year</th><th>YB/Lost Bugs</th><th>WiFi Bugs</th><th>BT Bugs</th><th>YB/Lost % of Year</th></tr></thead><tbody>\n")
        for row in yb_year_rows:
            f.write(f"<tr><td>{row['year']}</td><td>{row['yb_total']}</td><td>{row['wifi']}</td><td>{row['bt']}</td><td>{row['yb_pct']}%</td></tr>\n")
        f.write("</tbody></table>\n")

        f.write("<h2>YB/Lost Breakdown by Platform</h2>\n")
        f.write("<table><thead><tr><th>Platform</th><th>YB/Lost Bugs</th><th>WiFi Bugs</th><th>BT Bugs</th><th>YB/Lost % within Platform</th></tr></thead><tbody>\n")
        for row in yb_platform_rows:
            f.write(f"<tr><td>{row['platform']}</td><td>{row['yb_total']}</td><td>{row['wifi']}</td><td>{row['bt']}</td><td>{row['yb_pct_within_platform']}%</td></tr>\n")
        f.write("</tbody></table>\n")

        for y in years:
            f.write(f"<div class='year-section'><h2>{y} Breakdown (WiFi vs BT)</h2>")
            f.write("<div class='grid'>")
            f.write(f"<div class='card'><h3>{y} WiFi - Issue Category %</h3><div class='pie-wrap'><canvas id='cat_{y}_wifi'></canvas></div></div>")
            f.write(f"<div class='card'><h3>{y} WiFi - Jira Exposure %</h3><div class='pie-wrap'><canvas id='exp_{y}_wifi'></canvas></div></div>")
            f.write(f"<div class='card'><h3>{y} BT - Issue Category %</h3><div class='pie-wrap'><canvas id='cat_{y}_bt'></canvas></div></div>")
            f.write(f"<div class='card'><h3>{y} BT - Jira Exposure %</h3><div class='pie-wrap'><canvas id='exp_{y}_bt'></canvas></div></div>")
            f.write("</div></div>")

        data_json = json.dumps(chart_data, ensure_ascii=False)
        overall_line_json = json.dumps(overall_line_data, ensure_ascii=False)
        platform_line_json = json.dumps(platform_line_data, ensure_ascii=False)
        tat_year_line_json = json.dumps(tat_year_line_data, ensure_ascii=False)
        tat_platform_line_json = json.dumps(tat_platform_line_data, ensure_ascii=False)
        line_colors = ["#F4B400", "#0F7A79", "#6DC1A2"]
        pie_colors = [
            "#0F7A79", "#6DC1A2", "#F4B400", "#D8A24B", "#7D8A96",
            "#B7C4CF", "#9BB7B0", "#C9D3A8", "#D7B98E", "#A7C7C4",
            "#C6CEC8", "#8EA6A3", "#E1D0A8", "#B8C8B8", "#D8DDDF"
        ]
        line_colors_json = json.dumps(line_colors)
        pie_colors_json = json.dumps(pie_colors)
        f.write("<script>\n")
        f.write(f"const chartData = {data_json};\n")
        f.write(f"const overallLineData = {overall_line_json};\n")
        f.write(f"const platformLineData = {platform_line_json};\n")
        f.write(f"const tatYearLineData = {tat_year_line_json};\n")
        f.write(f"const tatPlatformLineData = {tat_platform_line_json};\n")
        f.write(f"const LINE_COLORS = {line_colors_json};\n")
        f.write(f"const PIE_COLORS = {pie_colors_json};\n")
        f.write("const CHART_TEXT_COLOR = '#333333';\n")
        f.write("const GRID_COLOR = '#E6E6E6';\n")
        f.write("Chart.defaults.font.family = \"'Segoe UI', Inter, -apple-system, BlinkMacSystemFont, sans-serif\";\n")
        f.write("Chart.defaults.color = CHART_TEXT_COLOR;\n")
        f.write(
            """
const LABEL_COLOR_MAP = {
    // issue categories
    'Connectivity':       '#2563eb',
    'BSOD':               '#dc2626',
    'Performance':        '#d97706',
    'P2P':                '#7c3aed',
    'YB/Lost':            '#ef4444',
    'Sensing':            '#059669',
    'BIOS':               '#0891b2',
    'HLK':                '#ca8a04',
    'Power Consumption':  '#9333ea',
    'UEFI':               '#c2410c',
    'System Hang':        '#be185d',
    'RF':                 '#0d9488',
    'OEM Tools':          '#64748b',
    'ICPS':               '#854d0e',
    // exposure buckets
    '1-Critical':         '#dc2626',
    '2-High':             '#ea580c',
    '3-Medium':           '#ca8a04',
    '4-Low':              '#16a34a',
    '(missing)':          '#9ca3af',
    'No Data':            '#e5e7eb',
};
const FALLBACK_COLORS = [
    '#3b82f6','#10b981','#f59e0b','#8b5cf6','#ec4899',
    '#06b6d4','#f97316','#84cc16','#6366f1','#14b8a6',
    '#e11d48','#a78bfa','#34d399','#fbbf24','#fb7185',
];
function getLabelColors(labels) {
    let fallbackIdx = 0;
    return labels.map(function(label) {
        return LABEL_COLOR_MAP[label] || FALLBACK_COLORS[fallbackIdx++ % FALLBACK_COLORS.length];
    });
}

function makePie(canvasId, labels, values) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    const percentageLabelPlugin = {
        id: 'percentageLabelPlugin',
        afterDatasetsDraw(chart) {
            const { ctx } = chart;
            const dataset = chart.data.datasets[0];
            const meta = chart.getDatasetMeta(0);
            ctx.save();
            ctx.font = '11px Segoe UI';
            ctx.fillStyle = '#333333';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            meta.data.forEach((arc, index) => {
                const value = dataset.data[index] || 0;
                if (value < 3) return;
                const pos = arc.tooltipPosition();
                ctx.fillText(value + '%', pos.x, pos.y);
            });
            ctx.restore();
        }
    };

    if (!labels || labels.length === 0) {
        new Chart(ctx, {
            type: 'pie',
            data: { labels: ['No Data'], datasets: [{ data: [100], backgroundColor: ['#e5e7eb'] }] },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                aspectRatio: 1,
                plugins: {
                    legend: {
                        position: 'bottom',
                        align: 'center',
                        labels: {
                            usePointStyle: true,
                            pointStyle: 'circle',
                            boxWidth: 8,
                            boxHeight: 8,
                            padding: 14,
                            color: CHART_TEXT_COLOR,
                            font: { family: 'Segoe UI, Inter, sans-serif', size: 11 }
                        }
                    }
                }
            },
            plugins: [percentageLabelPlugin]
        });
        return;
    }
    new Chart(ctx, {
        type: 'pie',
        data: {
            labels: labels,
            datasets: [{ data: values, backgroundColor: PIE_COLORS, borderColor: '#ffffff', borderWidth: 1 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 1,
            plugins: {
                legend: {
                    position: 'bottom',
                    align: 'center',
                    labels: {
                        usePointStyle: true,
                        pointStyle: 'circle',
                        boxWidth: 8,
                        boxHeight: 8,
                        padding: 14,
                        color: CHART_TEXT_COLOR,
                        font: { family: 'Segoe UI, Inter, sans-serif', size: 11 }
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(255,255,255,0.98)',
                    titleColor: CHART_TEXT_COLOR,
                    bodyColor: CHART_TEXT_COLOR,
                    borderColor: '#e5e7eb',
                    borderWidth: 1,
                    callbacks: {
                        label: function(context) {
                            const v = context.parsed || 0;
                            return context.label + ': ' + v + '%';
                        }
                    }
                }
            }
        },
        plugins: [percentageLabelPlugin]
    });
}

function makeIssueTrendLine(canvasId, labels, ipsIssueCount, jiraIssueCount, issueClosedWithFix) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !labels || labels.length === 0) return;
    const allLineValues = [...ipsIssueCount, ...jiraIssueCount, ...issueClosedWithFix]
        .map((v) => Number(v))
        .filter((v) => !Number.isNaN(v));
    const maxValue = allLineValues.length ? Math.max(...allLineValues, 1) : 1;
    const yTickCount = 5;
    const rawStep = maxValue / yTickCount;
    const magnitude = Math.pow(10, Math.floor(Math.log10(Math.max(rawStep, 1))));
    const normalized = rawStep / magnitude;
    const niceMultiplier = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    const yStep = Math.max(1, niceMultiplier * magnitude);
    const yAxisMax = yStep * yTickCount;

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'ips issue count',
                    data: ipsIssueCount,
                    borderWidth: 2.5,
                    pointStyle: 'circle',
                    pointRadius: 5,
                    pointHoverRadius: 7,
                    pointBackgroundColor: LINE_COLORS[0],
                    pointBorderColor: '#333333',
                    pointBorderWidth: 1.25,
                    tension: 0.25,
                    borderColor: LINE_COLORS[0],
                    backgroundColor: LINE_COLORS[0],
                    fill: false
                },
                {
                    label: 'jira issue count',
                    borderWidth: 2.5,
                    data: jiraIssueCount,
                    borderColor: LINE_COLORS[1],
                    backgroundColor: LINE_COLORS[1],
                    pointRadius: 5,
                    pointHoverRadius: 7,
                    pointBackgroundColor: LINE_COLORS[1],
                    pointBorderColor: '#333333',
                    pointBorderWidth: 1.25,
                    tension: 0.25,
                    borderDash: [7, 4],
                    pointStyle: 'rectRot',
                    pointRadius: 4,
                    fill: false
                },
                {
                    label: 'issue closed with fix',
                    data: issueClosedWithFix,
                    borderWidth: 2.5,
                    pointStyle: 'triangle',
                    pointRadius: 5,
                    pointHoverRadius: 7,
                    pointBackgroundColor: LINE_COLORS[2],
                    pointBorderColor: '#333333',
                    pointBorderWidth: 1.25,
                    tension: 0.25,
                    borderColor: LINE_COLORS[2],
                    backgroundColor: LINE_COLORS[2],
                    fill: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            layout: {
                padding: { top: 22, right: 8, bottom: 4, left: 4 }
            },
            plugins: {
                legend: {
                    position: 'top',
                    align: 'center',
                    labels: {
                        usePointStyle: true,
                        pointStyle: 'circle',
                        boxWidth: 8,
                        boxHeight: 8,
                        padding: 16,
                        color: CHART_TEXT_COLOR,
                        font: { family: 'Segoe UI, Inter, sans-serif', size: 11 }
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(255,255,255,0.98)',
                    titleColor: CHART_TEXT_COLOR,
                    bodyColor: CHART_TEXT_COLOR,
                    borderColor: '#e5e7eb',
                    borderWidth: 1,
                    callbacks: {
                        afterLabel: function(context) {
                            return '';
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: {
                        display: false,
                        drawBorder: false
                    },
                    ticks: {
                        color: CHART_TEXT_COLOR,
                        font: { family: 'Segoe UI, Inter, sans-serif', size: 11 }
                    }
                },
                y: {
                    beginAtZero: true,
                    max: yAxisMax,
                    grid: {
                        color: GRID_COLOR,
                        drawBorder: false
                    },
                    ticks: {
                        stepSize: yStep,
                        precision: 0,
                        callback: function(value) { return String(Math.round(Number(value))); },
                        color: CHART_TEXT_COLOR,
                        font: { family: 'Segoe UI, Inter, sans-serif', size: 11 }
                    }
                }
            },
        },
        plugins: [lineValueLabelPlugin]
    });
}

const lineValueLabelPlugin = {
    id: 'lineValueLabelPlugin',
    afterDatasetsDraw(chart) {
        const { ctx } = chart;
        ctx.save();
        ctx.font = '11px Segoe UI';
        ctx.fillStyle = '#333333';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';

        chart.data.datasets.forEach((dataset, datasetIndex) => {
            const meta = chart.getDatasetMeta(datasetIndex);
            if (meta.hidden) return;
            meta.data.forEach((point, index) => {
                const rawValue = dataset.data[index];
                if (rawValue === null || rawValue === undefined || Number.isNaN(Number(rawValue))) return;
                const valueText = String(Math.round(Number(rawValue)));
                ctx.fillText(valueText, point.x, point.y - 10);
            });
        });
        ctx.restore();
    }
};

function makeTatTrendLine(canvasId, labels, tatValues) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !labels || labels.length === 0) return;

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'average ips bug tat (days)',
                    data: tatValues,
                    borderColor: '#F4B400',
                    backgroundColor: '#F4B400',
                    borderWidth: 2.5,
                    pointRadius: 5,
                    pointHoverRadius: 7,
                    pointBackgroundColor: '#F4B400',
                    pointBorderColor: '#333333',
                    pointBorderWidth: 1.25,
                    tension: 0.35,
                    fill: false,
                    spanGaps: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    align: 'center',
                    labels: {
                        usePointStyle: true,
                        pointStyle: 'circle',
                        boxWidth: 8,
                        boxHeight: 8,
                        padding: 16,
                        color: CHART_TEXT_COLOR,
                        font: { family: 'Segoe UI, Inter, sans-serif', size: 11 }
                    }
                }
            },
            scales: {
                x: {
                    grid: {
                        display: false,
                        drawBorder: false
                    },
                    ticks: {
                        color: CHART_TEXT_COLOR,
                        font: { family: 'Segoe UI, Inter, sans-serif', size: 11 }
                    }
                },
                y: {
                    beginAtZero: true,
                    grid: {
                        color: GRID_COLOR,
                        drawBorder: false
                    },
                    ticks: {
                        callback: function(value) { return String(Math.round(Number(value))); },
                        color: CHART_TEXT_COLOR,
                        font: { family: 'Segoe UI, Inter, sans-serif', size: 11 }
                    },
                    title: {
                        display: true,
                        text: 'Days',
                        color: CHART_TEXT_COLOR,
                        font: { family: 'Segoe UI, Inter, sans-serif', size: 11 }
                    }
                }
            }
        },
        plugins: [lineValueLabelPlugin]
    });
}

Object.keys(chartData).forEach((year) => {
    const wifi = chartData[year]['WiFi'];
    const bt = chartData[year]['BT'];
    makePie(`cat_${year}_wifi`, wifi.issue_category.labels, wifi.issue_category.values);
    makePie(`exp_${year}_wifi`, wifi.jira_exposure.labels, wifi.jira_exposure.values);
    makePie(`cat_${year}_bt`, bt.issue_category.labels, bt.issue_category.values);
    makePie(`exp_${year}_bt`, bt.jira_exposure.labels, bt.jira_exposure.values);
});

makeIssueTrendLine('overall_2021_2025_line', overallLineData.labels, overallLineData.ips, overallLineData.jira, overallLineData.fixed);
makeIssueTrendLine('platform_breakdown_line', platformLineData.labels, platformLineData.ips, platformLineData.jira, platformLineData.fixed);
makeTatTrendLine('year_tat_line', tatYearLineData.labels, tatYearLineData.values);
makeTatTrendLine('platform_tat_line', tatPlatformLineData.labels, tatPlatformLineData.values);
"""
        )
        f.write("</script>\n")
        f.write("</body></html>\n")

    return html_path


def _write_outputs(result: Dict[str, Any], output_dir: str, customer_label: str = "lenovo") -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)

    range_tag = f"{result['start_year']}_{result['end_year']}"

    json_path = os.path.join(output_dir, f"{customer_label}_issue_analysis_{range_tag}.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump({k: v for k, v in result.items() if k != "detailed_rows"}, handle, indent=2, ensure_ascii=False)

    detail_csv = os.path.join(output_dir, f"{customer_label}_issue_analysis_{range_tag}_detail.csv")
    with open(detail_csv, "w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "issue_year",
            "jira_id",
            "jira_is_sw_change",
            "ips_title",
            "technology",
            "platform",
            "jira_platform",
            "ips_platform",
            "ips_created_date",
            "ips_closed_date",
            "ips_priority",
            "deduced_issue_category",
            "category_confidence",
            "jira_exposure",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result["detailed_rows"])

    yoy_csv = os.path.join(output_dir, f"{customer_label}_issue_analysis_{range_tag}_yoy.csv")
    with open(yoy_csv, "w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "year",
            "vs_year",
            "issues_delta",
            "issues_growth_pct",
            "wifi_issues_delta",
            "wifi_issues_growth_pct",
            "bt_issues_delta",
            "bt_issues_growth_pct",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result["year_over_year"])

    report_html = _build_html_report(result, output_dir, customer_label)

    return {
        "json": json_path,
        "detail_csv": detail_csv,
        "yoy_csv": yoy_csv,
        "report_html": report_html,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Customer issue YoY analysis (2021-2026) using DB + issue category model.")
    parser.add_argument("--table", default="ips_jira_bugs")
    parser.add_argument("--model", default=os.path.join("models", "issue_category_model.joblib"))
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--output-dir", default=os.path.join("customer issue analysis", "outputs"))
    parser.add_argument(
        "--customer",
        default="lenovo",
        choices=["lenovo", "all"],
        help="Customer filter: 'lenovo' for Lenovo only, 'all' for all customers (default: lenovo)",
    )
    args = parser.parse_args()

    if args.start_year > args.end_year:
        raise ValueError("--start-year must be <= --end-year")

    customer_label = "lenovo" if args.customer == "lenovo" else "all_customers"

    db = DbConnector()
    try:
        rows = _query_issues(db, args.table, args.start_year, args.end_year, customer_filter=args.customer)
    finally:
        del db

    result = _analyze_rows(rows, args.model, args.start_year, args.end_year)
    paths = _write_outputs(result, args.output_dir, customer_label)

    print("[OK] Customer issue analysis completed.")
    print(f"[OK] Range: {args.start_year}-{args.end_year}")
    for year in range(args.start_year, args.end_year + 1):
        y = result["years"][str(year)]
        wifi_count = y["by_technology"]["WiFi"]["total_issues"]
        bt_count = y["by_technology"]["BT"]["total_issues"]
        print(
            f"[OK] {year}: total_issues={y['total_issues']} wifi={wifi_count} bt={bt_count}"
        )
    print(f"[OK] JSON: {paths['json']}")
    print(f"[OK] Detail CSV: {paths['detail_csv']}")
    print(f"[OK] YoY CSV: {paths['yoy_csv']}")
    print(f"[OK] Pie Chart Report (HTML): {paths['report_html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
