"""Unified pipeline for IPS, JIRA, and HSD data.

This script fetches IPS and JIRA data using the existing Wireless dashboard modules
and enriches the merged dataset with HSD bugs fetched directly via the HSD REST API
(or an optional CSV export). It then writes the merged rows to Postgres using the
same DbConnector logic as the legacy dashboard.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import traceback
from datetime import date, datetime
from typing import List, Sequence

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_FATAL_LOG_PATH = os.path.join(_SCRIPT_DIR, "wireless_bug_dashboard_fatal.log")

# Ensure sibling job modules and root APIs are importable regardless of launch cwd.
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
_IMPORT_PATHS = [
    _WORKSPACE_ROOT,
    os.path.join(_WORKSPACE_ROOT, "APIs"),
    os.path.join(_WORKSPACE_ROOT, "jobs", "05_hsd_jira_data_pipeline"),
]
for _path in _IMPORT_PATHS:
    if _path not in sys.path:
        sys.path.append(_path)


def _persist_fatal(stage: str, exc: BaseException) -> None:
    try:
        with open(_FATAL_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("=" * 80 + "\n")
            fh.write(f"stage={stage} time={datetime.now().isoformat()}\n")
            fh.write(f"type={exc.__class__.__name__}\n")
            fh.write(f"message={exc}\n")
            fh.write("traceback:\n")
            fh.write(traceback.format_exc())
            if not traceback.format_exc().endswith("\n"):
                fh.write("\n")
    except Exception:
        # Never fail crash handling due to logging failures.
        pass


try:
    import psycopg2
    import HSD_access as hsd_access  # type: ignore
    from Wireless_bug_dashboard import (  # type: ignore
        DB_FUTURE_DATE,
        DB_NA,
        DbConnector,
        HsdBugData,
        IpsBug,
        JiraBug,
        RunOptions,
        apply_run_option,
        generate_merged_bug_list,
        load_hsd_bugs_from_csv,
        _load_team_assignees,
        normalize_hsd_owner,
        normalize_hsd_platform,
        pick_run_options_menu,
        stage_timer,
    )
except Exception as exc:
    _persist_fatal("import", exc)
    print(
        f"[FATAL] Import failed: {exc}. See {_FATAL_LOG_PATH} for traceback.",
        file=sys.stderr,
    )
    raise

LOG = logging.getLogger("wireless_ips_hsd")


def _setup_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    LOG.setLevel(numeric_level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    LOG.handlers.clear()
    LOG.addHandler(handler)

    # Ensure root/CUSTOMER_BUGS logs (DB connector, etc.) also show at the chosen level
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Silence noisy Snowflake OCSP/urllib3 retry warnings unless explicitly enabled.
    logging.getLogger("snowflake.connector.vendored.urllib3").setLevel(logging.ERROR)
    logging.getLogger("snowflake.connector.vendored.urllib3.connectionpool").setLevel(logging.ERROR)


def _parse_hsd_datetime(value: str | None) -> datetime:
    if not value or not value.strip():
        return DB_FUTURE_DATE
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(value.strip(), fmt)
            if parsed.tzinfo:
                return parsed.astimezone(None).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value.strip())
        return parsed if not parsed.tzinfo else parsed.astimezone(None).replace(tzinfo=None)
    except ValueError:
        return DB_FUTURE_DATE


def _owner_filter_set(raw: str | None) -> set[str]:
    if raw:
        return {token.strip().lower() for token in raw.split(",") if token.strip()}

    # Default to CFE team member list when no explicit HSD owner filter is provided.
    members = [str(name).strip().lower() for name in _load_team_assignees() if str(name).strip()]
    if members:
        LOG.info("HSD owner filter defaulted to CFE team list: %d member(s)", len(members))
    return set(members)


def _normalize_hsd_status_reason(value: str | None) -> str:
    if not value:
        return DB_NA
    cleaned = str(value).strip()
    if not cleaned or cleaned.lower() == "na":
        return DB_NA
    lowered = cleaned.lower()
    if "complete" in lowered or "implemented" in lowered or "rejected" in lowered:
        return "closed"
    if "open" in lowered:
        return "open"
    return cleaned


def _resolve_hsd_status_reason(status_reason: str | None, status: str | None) -> str:
    """Resolve final HSD status with closed states taking precedence across fields."""

    normalized_reason = _normalize_hsd_status_reason(status_reason)
    normalized_status = _normalize_hsd_status_reason(status)

    # Some API payloads keep status_reason stale (e.g. open) while status is complete.
    if normalized_reason == "closed" or normalized_status == "closed":
        return "closed"
    if normalized_reason == "open" or normalized_status == "open":
        return "open"
    return normalized_reason if normalized_reason != DB_NA else normalized_status


def _convert_hsd_bugs(hsd_bugs: Sequence[hsd_access.HsdBug]) -> List[HsdBugData]:
    converted: List[HsdBugData] = []
    for bug in hsd_bugs:
        owner = normalize_hsd_owner(bug.owner or DB_NA)
        LOG.debug(
            "HSD fetched: id=%s promoted=%s owner=%s status=%s title=%s",
            bug.bug_id,
            bug.promoted_id,
            owner,
            bug.status,
            (bug.title or "")[:80],
        )
        converted.append(
            HsdBugData(
                hsd_id=bug.bug_id or DB_NA,
                hsd_promoted_id=bug.promoted_id or DB_NA,
                hsd_status_reason=_resolve_hsd_status_reason(bug.status_reason, bug.status),
                hsd_customer_detail=bug.customer or DB_NA,
                hsd_owner=owner or DB_NA,
                hsd_title=bug.title or DB_NA,
                hsd_submitted_date=_parse_hsd_datetime(bug.submitted_date or bug.created_date),
                hsd_updated_date=_parse_hsd_datetime(bug.modified_date or bug.created_date),
                hsd_platform=normalize_hsd_platform(bug.component or DB_NA),
            )
        )
    return converted


def _fetch_hsd_via_api(args: argparse.Namespace) -> List[HsdBugData]:
    if not (args.hsd_query_id or args.hsd_query or args.hsd_owner or args.hsd_component or args.hsd_recent_days):
        LOG.info("No HSD query arguments supplied; skipping HSD API fetch.")
        return []

    client = hsd_access.HsdApiClient(
        token=None,
        verify=False if args.hsd_insecure else args.hsd_ca_bundle,
        auth_mode=args.hsd_auth_mode,
        kerberos_cache=args.hsd_krb_ccache,
    )
    try:
        if args.hsd_query_id:
            raw = client.get_bugs_from_saved_query(args.hsd_query_id, limit=args.hsd_limit)
        elif args.hsd_query:
            raw = client.search_bugs(args.hsd_query, limit=args.hsd_limit)
        elif args.hsd_owner:
            raw = client.get_bugs_by_owner(
                args.hsd_owner,
                limit=args.hsd_limit,
                created_year=args.created_year if hasattr(args, "created_year") else None,
            )
        elif args.hsd_component:
            raw = client.get_bugs_by_component(args.hsd_component, status=args.hsd_status, limit=args.hsd_limit)
        elif args.hsd_recent_days:
            raw = client.get_recent_bugs(days=args.hsd_recent_days, limit=args.hsd_limit)
        else:
            raw = []
    except Exception as exc:
        if args.hsd_skip_on_auth_failure:
            exc_name = exc.__class__.__name__
            msg = str(exc)
            if "NoCredential" in exc_name or "401" in msg or "403" in msg:
                LOG.warning(
                    "HSD API auth failed (%s). Skipping HSD data; use --hsd-auth-mode auto/token or --hsd-csv.",
                    exc,
                )
                return []
        raise
    finally:
        client.close()

    owners = _owner_filter_set(args.hsd_owner_filter)
    if owners:
        raw = [
            bug
            for bug in raw
            if (
                (bug.owner or "").strip().lower() in owners
                or normalize_hsd_owner(bug.owner or "").strip().lower() in owners
            )
        ]
        LOG.info("Owner filter reduced HSD list to %d rows", len(raw))

    LOG.info("Fetched %d HSD bug(s) via API", len(raw))
    converted = _convert_hsd_bugs(raw)
    if converted:
        ids = [bug.hsd_id for bug in converted]
        LOG.info("HSD fetched ids: %s", ", ".join(str(x) for x in ids))
    return converted


def _load_hsd_data(args: argparse.Namespace) -> List[HsdBugData]:
    if not args.hsd_only and not args.with_hsd:
        LOG.info("HSD fetch disabled; enable with --with-hsd or use --hsd-only.")
        return []
    if args.hsd_csv:
        return load_hsd_bugs_from_csv(args.hsd_csv)
    return _fetch_hsd_via_api(args)


def _apply_direct_overrides(opt: RunOptions, args: argparse.Namespace) -> RunOptions:
    if args.db_disable:
        opt.enable_db_insert = False
    if args.db_recreate:
        opt.db_recreate_table = True
    elif args.db_append:
        opt.db_recreate_table = False
    if args.db_batch:
        opt.db_use_batch_insert = True
    if args.limit_ips is not None:
        opt.limit_ips = args.limit_ips
    if args.limit_jira is not None:
        opt.limit_jira = args.limit_jira
    if args.enable_jira_comment_analysis:
        opt.enable_jira_comment_analysis = True
    opt.db_auto_add_missing_columns = bool(args.allow_ddl)
    return opt


def _parse_created_years(raw: str) -> list[str]:
    years = [token.strip() for token in raw.split(",") if token.strip()]
    if not years:
        return [raw.strip()]
    if len(years) == 1 and years[0].isdigit():
        return _expand_years_for_last_year_refresh()
    return years


def _expand_years_from(start_year: int) -> list[str]:
    current_year = datetime.now().year
    if start_year > current_year:
        return [str(start_year)]
    return [str(year) for year in range(start_year, current_year + 1)]


def _expand_years_for_last_year_refresh() -> list[str]:
    current_year = datetime.now().year
    start_year = current_year - 1
    return _expand_years_from(start_year)


def _dedupe_ips(rows: list) -> list:
    seen = set()
    unique = []
    missing_key = 0
    dupes = 0
    for item in rows:
        key = getattr(item, "ips_case_number", None)
        if key is None:
            missing_key += 1
            unique.append(item)
            continue
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        unique.append(item)
    if missing_key or dupes:
        LOG.info("IPS dedupe: %d missing keys, %d duplicate rows dropped", missing_key, dupes)
    return unique


def _dedupe_jira(rows: list) -> list:
    seen = set()
    unique = []
    missing_key = 0
    dupes = 0
    for item in rows:
        key = getattr(item, "jira_id", None)
        if key is None:
            missing_key += 1
            unique.append(item)
            continue
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        unique.append(item)
    if missing_key or dupes:
        LOG.info("JIRA dedupe: %d missing keys, %d duplicate rows dropped", missing_key, dupes)
    return unique


def _filter_hsd_by_years(rows: List[HsdBugData], years: Sequence[str]) -> List[HsdBugData]:
    if not years:
        return rows
    year_set = {int(y) for y in years if y.isdigit()}
    if not year_set:
        return rows
    kept = [bug for bug in rows if bug.hsd_submitted_date.year in year_set]
    dropped = [bug for bug in rows if bug.hsd_submitted_date.year not in year_set]
    if dropped:
        sample = "; ".join(
            f"hsd_id={getattr(bug, 'hsd_id', DB_NA)} submitted={getattr(bug, 'hsd_submitted_date', DB_NA)}"
            for bug in dropped[:10]
        )
        LOG.info(
            "HSD year filter kept %d/%d rows for years=%s; dropped %d row(s): %s",
            len(kept),
            len(rows),
            ",".join(str(year) for year in sorted(year_set)),
            len(dropped),
            sample,
        )
    else:
        LOG.info("HSD year filter kept all %d row(s) for years=%s", len(rows), ",".join(str(year) for year in sorted(year_set)))
    return kept


def _scope_start_date(years: Sequence[str]) -> date:
    parsed_years = [int(y) for y in years if y.isdigit()]
    if parsed_years:
        return date(min(parsed_years), 1, 1)
    today = date.today()
    return date(today.year, 1, 1)


def _clear_hsd_scope_before_sync(table_name: str, owner_filter_raw: str | None, years: Sequence[str]) -> None:
    if table_name != "ips_jira_bugs":
        return
    owners_raw = _owner_filter_set(owner_filter_raw)
    owners_expanded = set(owners_raw)
    for owner in list(owners_raw):
        normalized = normalize_hsd_owner(owner)
        if normalized:
            owners_expanded.add(normalized.strip().lower())

    owners = sorted(owners_expanded)
    if not owners:
        LOG.info("HSD pre-sync cleanup skipped: --hsd-owner-filter is empty.")
        return

    start_date = _scope_start_date(years)
    db_manager = DbConnector()
    try:
        cleared = db_manager.clear_hsd_snapshot_scope(table_name, owners, start_date)
        LOG.info(
            "HSD pre-sync cleanup cleared %s stale row(s) in %s for owners=%s from %s",
            cleared,
            table_name,
            ",".join(owners),
            start_date.isoformat(),
        )
    finally:
        del db_manager


def _insert_into_db(opt: RunOptions, rows: list) -> None:
    if not opt.enable_db_insert:
        LOG.info("DB insert disabled; skipping Postgres stage.")
        return
    # Debug: count CAE-Linux rows before insert
    cae_like = [r for r in rows if hasattr(r, "jira_data") and getattr(r.jira_data, "jira_team", "")]
    cae_like = [r for r in cae_like if "cae" in str(r.jira_data.jira_team).lower() and "linux" in str(r.jira_data.jira_team).lower()]
    if cae_like:
        sample_keys = [getattr(r.jira_data, "jira_id", DB_NA) for r in cae_like[:5]]
        LOG.info("Insert precheck: CAE-Linux-like rows=%d; sample jira_ids=%s", len(cae_like), sample_keys)
    else:
        LOG.warning("Insert precheck: no CAE-Linux-like jira_team rows in merged dataset")

    db_manager = DbConnector()
    try:
        db_manager.insert_to_table("ips_jira_bugs", rows, opt.db_recreate_table, opt=opt)
    finally:
        del db_manager


def _insert_hsd_into_db(opt: RunOptions, rows: List[HsdBugData], table_name: str) -> None:
    if not opt.enable_db_insert:
        LOG.info("DB insert disabled; skipping Postgres stage.")
        return
    if not rows:
        LOG.info("No HSD rows to insert; skipping Postgres stage.")
        return
    db_manager = DbConnector()
    try:
        if table_name == "ips_jira_bugs":
            try:
                updated = db_manager.update_hsd_columns(
                    table_name,
                    rows,
                    insert_missing=True,
                    update_customer_from_hsd=False,
                )
                LOG.info(
                    "HSD-only: refreshed %s row(s) in %s (input rows=%s)",
                    updated,
                    table_name,
                    len(rows),
                )
            except psycopg2.errors.UndefinedTable:
                LOG.warning(
                    "Target table %s does not exist; run full pipeline once to create it before --hsd-only refresh.",
                    table_name,
                )
            return
        try:
            db_manager.insert_to_table(table_name, rows, opt.db_recreate_table, opt=opt)
        except psycopg2.errors.UndefinedTable:
            LOG.info("HSD table %s missing; creating table and retrying insert.", table_name)
            conn = getattr(db_manager, "_DbConnector__connection", None)
            if conn is not None:
                conn.rollback()
            db_manager.insert_to_table(table_name, rows, True, opt=opt)
        try:
            rowcount = db_manager.get_table_rowcount(table_name)
            LOG.info("HSD table %s rowcount after insert: %s", table_name, rowcount)
        except Exception as exc:  # pragma: no cover - best-effort logging
            LOG.warning("Could not fetch rowcount for %s: %s", table_name, exc)
    finally:
        del db_manager


def main() -> int:
    parser = argparse.ArgumentParser(description="Combined IPS/JIRA/HSD dashboard pipeline")
    parser.add_argument(
        "--created-year",
        required=True,
        help=(
            "Only include bugs created since this year. "
            "If a single year is provided, a rolling last-year refresh is used. "
            "Use comma-separated years to specify exact years (e.g. 2025,2026)."
        ),
    )
    parser.add_argument("--no-menu", action="store_true", help="Skip the Wireless dashboard interactive menu")
    parser.add_argument("--run-option", type=int, choices=range(0, 12), help="Apply Wireless dashboard preset (0-11)")
    parser.add_argument("--limit-ips", type=int, help="Limit number of IPS rows for testing")
    parser.add_argument("--limit-jira", type=int, help="Limit number of JIRA rows for testing")
    parser.add_argument(
        "--enable-jira-comment-analysis",
        action="store_true",
        help="Enable JIRA comment analysis (disabled by default)",
    )
    parser.add_argument("--db-disable", action="store_true", help="Skip Postgres inserts")
    parser.add_argument("--db-append", action="store_true", help="Do not recreate the target table")
    parser.add_argument("--db-batch", action="store_true", help="Use batch inserts for Postgres")
    parser.add_argument("--db-recreate", action="store_true", help="Drop and recreate the target table before insert")
    parser.add_argument(
        "--allow-ddl",
        action="store_true",
        help="Allow DROP/CREATE table operations. By default only DML is allowed.",
    )
    parser.add_argument(
        "--append-last-year",
        dest="append_last_year_refresh",
        action="store_true",
        help="Shortcut: append to existing table and refresh data for the last full year and current year.",
    )
    parser.add_argument(
        "--append-from-2025",
        dest="append_last_year_refresh",
        action="store_true",
        help="Deprecated alias for --append-last-year.",
    )
    parser.add_argument("--hsd-only", action="store_true", help="Fetch only HSD data and write to Postgres")
    parser.add_argument("--hsd-table", default="hsd_bugs", help="Postgres table for --hsd-only (default: hsd_bugs)")
    parser.add_argument(
        "--with-hsd",
        action="store_true",
        help="Enable HSD fetch during the full IPS/JIRA pipeline",
    )

    # HSD options
    parser.add_argument("--hsd-csv", help="Path to a pre-exported HSD CSV file")
    parser.add_argument(
        "--hsd-query-id",
        help="HSD saved query ID",
    )
    parser.add_argument("--hsd-query", help="Raw HSD query string")
    parser.add_argument("--hsd-owner", help="Fetch HSD bugs by owner")
    parser.add_argument("--hsd-component", help="Fetch HSD bugs by component")
    parser.add_argument("--hsd-status", default="active", help="Status filter for component queries")
    parser.add_argument("--hsd-recent-days", type=int, help="Fetch HSD bugs updated in the last N days")
    parser.add_argument("--hsd-limit", type=int, default=500, help="Maximum HSD bugs to fetch")
    parser.add_argument("--hsd-owner-filter", help="Comma-separated owner filter applied after fetch")
    parser.add_argument(
        "--hsd-presync-cleanup",
        action="store_true",
        help=(
            "Enable HSD pre-sync cleanup before --hsd-only refresh. "
            "Default is disabled to preserve existing post-2025 HSD rows."
        ),
    )
    parser.add_argument("--hsd-krb-ccache", help="Kerberos credential cache path")
    parser.add_argument("--hsd-ca-bundle", help="Custom CA bundle for HSD HTTPS calls")
    parser.add_argument(
        "--hsd-insecure",
        action="store_true",
        help="Disable TLS verification for HSD API (self-signed envs); prefer --hsd-ca-bundle",
    )
    parser.add_argument(
        "--hsd-auth-mode",
        choices=("auto", "kerberos", "token"),
        default="auto",
        help="HSD auth mode: auto (default), kerberos, or token",
    )
    parser.add_argument(
        "--hsd-skip-on-auth-failure",
        action="store_true",
        help="Skip HSD API fetch if authentication fails",
    )

    parser.add_argument("--log-level", default="INFO", help="Logging level")

    args = parser.parse_args()
    _setup_logging(args.log_level)

    opt = RunOptions()
    if not args.hsd_only:
        if args.run_option is not None:
            opt = apply_run_option(args.run_option)
        elif not args.no_menu:
            opt = pick_run_options_menu()
    if args.append_last_year_refresh:
        args.db_append = True
    if args.db_recreate and args.db_append:
        LOG.info("Both --db-recreate and --append-last-year/--db-append set; --db-recreate takes precedence.")
        args.db_append = False
    opt = _apply_direct_overrides(opt, args)
    if args.db_recreate and not args.allow_ddl:
        raise SystemExit("--db-recreate is blocked unless --allow-ddl is provided.")
    if opt.db_recreate_table and not args.allow_ddl:
        LOG.warning(
            "DDL safeguard active: forcing append mode (no DROP/CREATE). "
            "Use --allow-ddl to override."
        )
        opt.db_recreate_table = False
    LOG.info("Run options: %s", opt)
    if args.run_option == 0 and not args.hsd_only:
        LOG.info(
            "Debug: enable_jira_duplicate_sw_check=%s",
            opt.enable_jira_duplicate_sw_check,
        )

    perf: dict[str, float] = {}
    t0 = time.perf_counter()
    jira_client: JiraBug | None = None

    try:
        if args.hsd_only:
            years = _parse_created_years(args.created_year)
        elif args.append_last_year_refresh:
            years = _expand_years_for_last_year_refresh()
        else:
            years = _parse_created_years(args.created_year)

        if args.hsd_only:
            with stage_timer("HSD_fetch", perf):
                hsd_data = _load_hsd_data(args)
                hsd_data = _filter_hsd_by_years(hsd_data, years)
            with stage_timer("Postgres_insert", perf):
                if hsd_data and args.hsd_presync_cleanup:
                    _clear_hsd_scope_before_sync(args.hsd_table, args.hsd_owner_filter, years)
                elif hsd_data:
                    LOG.info(
                        "HSD pre-sync cleanup disabled; preserving existing HSD rows in %s.",
                        args.hsd_table,
                    )
                _insert_hsd_into_db(opt, hsd_data, args.hsd_table)

            total = time.perf_counter() - t0
            LOG.info("=== Stage timing summary (seconds) ===")
            for stage, elapsed in perf.items():
                LOG.info("%-16s %6.2f", stage, elapsed)
            LOG.info("%-16s %6.2f", "TOTAL", total)
            return 0

        with stage_timer("IPS_fetch", perf):
            ips_data = []
            for year in years:
                ips = IpsBug(year)
                ips_data.extend(ips.get_all_bugs())
            ips_data = _dedupe_ips(ips_data)
            if opt.limit_ips and opt.limit_ips > 0:
                ips_data = ips_data[: opt.limit_ips]
                LOG.info("IPS limited to first %d rows", opt.limit_ips)

        with stage_timer("JIRA_fetch", perf):
            jira_data = []
            for year in years:
                jira_client = JiraBug(year, opt)
                jira_data.extend(jira_client.get_all_bugs())
            jira_data = _dedupe_jira(jira_data)
            if opt.limit_jira and opt.limit_jira > 0:
                jira_data = jira_data[: opt.limit_jira]
                LOG.info("JIRA limited to first %d rows", opt.limit_jira)

        with stage_timer("HSD_fetch", perf):
            hsd_data = _load_hsd_data(args)
            hsd_data = _filter_hsd_by_years(hsd_data, years)

        with stage_timer("MERGE", perf):
            merged = generate_merged_bug_list(ips_data, jira_data, jira_client, hsd_data)
            LOG.info("Merged dataset size: %d rows", len(merged))

        with stage_timer("Postgres_insert", perf):
            _insert_into_db(opt, merged)

        total = time.perf_counter() - t0
        LOG.info("=== Stage timing summary (seconds) ===")
        for stage, elapsed in perf.items():
            LOG.info("%-16s %6.2f", stage, elapsed)
        LOG.info("%-16s %6.2f", "TOTAL", total)
        return 0
    finally:
        if jira_client is not None:
            try:
                if hasattr(jira_client, "close"):
                    jira_client.close()
                elif hasattr(jira_client, "_session"):
                    jira_client._session.close()
                client = jira_client.get_jira()
                if client and hasattr(client, "close"):
                    client.close()
            except Exception as exc:  # pragma: no cover - cleanup best effort
                LOG.warning("Failed to close JIRA client: %s", exc)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        code = exc.code
        is_ok_exit = code in (None, 0)
        if not is_ok_exit:
            _persist_fatal("system_exit", exc)
            print(
                f"[FATAL] Exited with code {code}. See {_FATAL_LOG_PATH} for traceback.",
                file=sys.stderr,
            )
        raise
    except Exception as exc:
        _persist_fatal("runtime", exc)
        print(
            f"[FATAL] Runtime failure: {exc}. See {_FATAL_LOG_PATH} for traceback.",
            file=sys.stderr,
        )
        raise
