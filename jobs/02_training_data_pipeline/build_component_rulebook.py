from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
from typing import List, Tuple

TARGET_CATEGORIES: List[str] = [
    "BIOS",
    "BSOD",
    "Connectivity",
    "HLK",
    "ICPS/Killer",
    "OEM Tools",
    "P2P",
    "Performance",
    "Power Consumption",
    "RF",
    "Roaming",
    "Sensing",
    "System Hang",
    "UEFI",
    "YB/Lost",
]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _infer_category(component: str) -> Tuple[str, str, str]:
    """Return (category, level, reason). level in {hard, soft, review}."""
    c = component.lower()

    hard_rules = [
        (("icps",), "ICPS/Killer", "component contains 'icps'"),
        (("killer",), "ICPS/Killer", "component contains 'killer'"),
        (("connect", "conn"), "Connectivity", "component contains connectivity cues"),
        (("perform", "throughput", "rate"), "Performance", "component contains performance cues"),
        (("power", "d3"), "Power Consumption", "component contains power cues"),
        (("roam",), "Roaming", "component contains roaming cue"),
        (("p2p",), "P2P", "component contains p2p cue"),
        (("rf", "phy", "antenna", "regulatory"), "RF", "component contains rf/phy cues"),
        (("hlk",), "HLK", "component contains hlk cue"),
        (("bsod",), "BSOD", "component contains bsod cue"),
        (("hang",), "System Hang", "component contains hang cue"),
        (("bios",), "BIOS", "component contains bios cue"),
        (("uefi",), "UEFI", "component contains uefi cue"),
        (("assert", "yb", "lost"), "YB/Lost", "component contains yb/lost/assert cues"),
        (("sensing",), "Sensing", "component contains sensing cue"),
    ]

    for keys, cat, reason in hard_rules:
        if any(k in c for k in keys):
            return cat, "hard", reason

    soft_rules = [
        (("3rd party", "remote device", "iop"), "Connectivity", "3rd-party component often maps to connectivity-like issues"),
        (("driver", "windriver", "host windows"), "Connectivity", "driver stack components often map to connectivity in current data"),
        (("tools", "install", "installer"), "OEM Tools", "tools/install components tend to map to OEM Tools"),
        (("system", "fw", "protocol", "transport"), "Connectivity", "fw/system stack often manifests as connectivity symptoms"),
    ]

    for keys, cat, reason in soft_rules:
        if any(k in c for k in keys):
            return cat, "soft", reason

    return "", "review", "no reliable keyword rule"


def build_rulebook(input_csv: Path, output_csv: Path) -> dict:
    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    counts = Counter()
    with input_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            comp = _clean(row.get("jira_component"))
            if not comp:
                continue
            counts[comp] += 1

    rows = []
    level_counts = Counter()
    for comp, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower())):
        cat, level, reason = _infer_category(comp)
        level_counts[level] += 1
        rows.append(
            {
                "component": comp,
                "rows": n,
                "suggested_category": cat,
                "rule_level": level,
                "reason": reason,
                "approved_category": "",
                "enabled": "",
                "notes": "",
            }
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "component",
                "rows",
                "suggested_category",
                "rule_level",
                "reason",
                "approved_category",
                "enabled",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    return {
        "components": len(rows),
        "level_counts": dict(level_counts),
        "top_components": rows[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build component->category rulebook candidates.")
    parser.add_argument("--input-csv", default="training_title_component_pairs.csv")
    parser.add_argument("--output-csv", default="component_category_rulebook_candidates.csv")
    args = parser.parse_args()

    summary = build_rulebook(Path(args.input_csv), Path(args.output_csv))
    print(f"[OK] wrote: {args.output_csv}")
    print(f"[INFO] components={summary['components']}")
    print(f"[INFO] level_counts={summary['level_counts']}")
    print("[INFO] target_categories=" + ", ".join(TARGET_CATEGORIES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
