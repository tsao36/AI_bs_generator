"""One-time script to normalise human_category labels in CFE_input CSV files.

Rules:
  - Strip surrounding single quotes from all human_category values  e.g. 'YB/Lost' -> YB/Lost
  - YB            -> YB/Lost  (merge into single category)
  - OEM Tools / \nOEM Tools -> OEM Tools (strip leading newline)
  - fuck          -> DELETE row
  - Cant judge    -> DELETE row
  - Can't judge   -> DELETE row  (incl. Windows-1252 smart apostrophe variant)
"""
import csv
import glob
import io
import os

LABEL_MAP = {
    # case / typo normalisations
    "sensing": "Sensing",
    "P2p": "P2P",
    "p2p": "P2P",
    "roaming": "Roaming",
    "WowLan": "WowLAN",
    "Wowlan": "WowLAN",
    "Power consumption": "Power Consumption",
    "Lost": "YB/Lost",
    "YB": "YB/Lost",
    "\nOEM Tools": "OEM Tools",
    # deliberate remappings
    "TAS": "UEFI",
    "Assert": "YB/Lost",
    "FW Assert": "YB/Lost",
    "D3": "Power Consumption",
    "Performance/P2P": "P2P",
    "Connectivity/P2P": "P2P",
    "Needs-Triage": "Need-Triage",
    "Unknown": "Need-Triage",
    "unknown": "Need-Triage",
    "Not-Wireless": "Need-Triage",
    "not-wireless": "Need-Triage",
}
DELETE_LABELS = {"fuck", "Cant judge", "Can't judge", "Can\u2019t judge", "Can\x92t judge"}


def _normalise(raw: str) -> str:
    """Strip surrounding single quotes, then apply label map."""
    v = raw.strip()
    # strip wrapping single quotes e.g. 'YB/Lost' -> YB/Lost
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        v = v[1:-1].strip()
    # also strip leading newline before OEM Tools
    v = v.lstrip("\n").strip()
    return LABEL_MAP.get(v, v)


files = sorted(glob.glob("CFE_input/*.csv"))
total_fixed = 0
total_deleted = 0

for fpath in files:
    raw = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(fpath, "r", encoding=enc, newline="") as f:
                raw = f.read()
            break
        except Exception:
            continue
    if raw is None:
        print(f"  SKIP (unreadable): {os.path.basename(fpath)}")
        continue

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or "human_category" not in reader.fieldnames:
        continue

    rows = list(reader)
    new_rows = []
    file_fixed = 0
    file_deleted = 0

    for row in rows:
        orig = row.get("human_category", "")
        normalised = _normalise(orig)
        # delete junk rows
        if normalised in DELETE_LABELS or not normalised:
            if orig.strip():  # only count non-empty as deleted
                file_deleted += 1
            continue
        if normalised != orig:
            row["human_category"] = normalised
            file_fixed += 1
        new_rows.append(row)

    if file_fixed > 0 or file_deleted > 0:
        with open(fpath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(new_rows)
        print(f"  {os.path.basename(fpath):50s}  fixed={file_fixed}  deleted={file_deleted}")
        total_fixed += file_fixed
        total_deleted += file_deleted

print(f"\nDone. Total fixed={total_fixed}, deleted={total_deleted}")
