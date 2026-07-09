from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime
from typing import Any, Dict, List


def _read_csv_rows(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_xlsx_rows(path: str) -> List[Dict[str, str]]:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Reading XLSX requires pandas/openpyxl. Install with: pip install pandas openpyxl"
        ) from exc

    df = pd.read_excel(path, dtype=str)
    df = df.where(df.notna(), other="")
    rows: List[Dict[str, str]] = []
    for row in df.to_dict(orient="records"):
        clean_row: Dict[str, str] = {}
        for key, value in row.items():
            clean_row[str(key)] = str(value or "")
        rows.append(clean_row)
    return rows


def _read_rows(path: str) -> List[Dict[str, str]]:
    lower = path.lower()
    if lower.endswith(".xlsx"):
        return _read_xlsx_rows(path)
    return _read_csv_rows(path)


def _clean(value: str) -> str:
    return str(value or "").strip()


def _count_filled_human_category(rows: List[Dict[str, str]]) -> int:
    count = 0
    for row in rows:
        if _clean(str(row.get("human_category", ""))):
            count += 1
    return count


def _sibling_xlsx_path(path: str) -> str:
    base, _ = os.path.splitext(path)
    return base + ".xlsx"


def _is_csv_stale_vs_xlsx(csv_path: str) -> tuple[bool, str]:
    xlsx_path = _sibling_xlsx_path(csv_path)
    if not os.path.exists(xlsx_path):
        return False, ""

    try:
        csv_rows = _read_csv_rows(csv_path)
        xlsx_rows = _read_xlsx_rows(xlsx_path)
    except Exception as exc:
        return False, f"[WARN] Skipped CSV/XLSX freshness check: {exc}"

    csv_filled = _count_filled_human_category(csv_rows)
    xlsx_filled = _count_filled_human_category(xlsx_rows)
    csv_mtime = os.path.getmtime(csv_path)
    xlsx_mtime = os.path.getmtime(xlsx_path)

    # Treat CSV as stale if XLSX has more labeled rows, or same labels but newer save time.
    stale = (xlsx_filled > csv_filled) or (xlsx_filled == csv_filled and xlsx_mtime > csv_mtime)
    if not stale:
        return False, ""

    msg = (
        "Detected stale CSV compared with sibling XLSX. "
        f"CSV filled human_category={csv_filled}, XLSX filled human_category={xlsx_filled}. "
        f"CSV={csv_path} | XLSX={xlsx_path}. "
        "Use the XLSX file path for ingestion to avoid training on outdated labels."
    )
    return True, msg


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest reviewed weekly labels (CSV or XLSX) into CFE_input format.")
    parser.add_argument("--reviewed-csv", required=True)
    parser.add_argument("--output-dir", default="CFE_input")
    parser.add_argument("--output-name", default="")
    parser.add_argument(
        "--allow-stale-csv",
        action="store_true",
        help="Allow ingesting CSV even if a sibling XLSX appears newer/more complete.",
    )
    args = parser.parse_args()

    source_path = args.reviewed_csv
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Reviewed file not found: {source_path}")

    if source_path.lower().endswith(".csv") and not args.allow_stale_csv:
        stale, stale_msg = _is_csv_stale_vs_xlsx(source_path)
        if stale:
            raise RuntimeError(stale_msg)
        if stale_msg:
            print(stale_msg)

    rows = _read_rows(source_path)
    accepted: List[Dict[str, str]] = []

    for r in rows:
        title = _clean(r.get("ips_title", ""))
        human_category = _clean(r.get("human_category", ""))
        technology = _clean(r.get("technology", ""))
        predicted_existing = _clean(r.get("predicted_category_existing", ""))
        predicted_model = _clean(r.get("predicted_category_model", ""))
        predicted_category = predicted_existing or predicted_model

        if not title or not human_category:
            continue

        accepted.append(
            {
                "ips_title": title,
                "predicted_category": predicted_category,
                "technology": technology,
                "human_category": human_category,
            }
        )

    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    output_name = args.output_name.strip() if args.output_name else f"weekly_labels_{stamp}.csv"
    output_path = os.path.join(args.output_dir, output_name)

    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["ips_title", "predicted_category", "technology", "human_category"],
        )
        writer.writeheader()
        writer.writerows(accepted)

    print(f"[OK] Ingested labels written: {output_path}")
    print(f"[OK] Accepted labeled rows: {len(accepted)}")
    print(f"[INFO] Skipped rows (missing ips_title or human_category): {max(0, len(rows) - len(accepted))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
