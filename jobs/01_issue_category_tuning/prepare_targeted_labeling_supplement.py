"""
prepare_targeted_labeling_supplement.py

從 IPS_data_exported.csv 中針對性抽樣弱類別候選案件，
輸出 weekly_labeling_template.csv 相同格式的補充標記清單。

目標類別：
  - 稀有類別（訓練時被 drop）：Roaming, Miracast, WowLAN
  - 混淆類別（F1 持續下滑）：Performance, YB/Lost

用法：
  python prepare_targeted_labeling_supplement.py
  python prepare_targeted_labeling_supplement.py --ips IPS_data_exported.csv --out targeted_labeling_supplement.csv --seed 42
"""

from __future__ import annotations

import argparse
import csv
import os
import random
from datetime import datetime
from typing import Dict, List, Set

# ---------------------------------------------------------------------------
# 關鍵字定義：每個目標類別要從 IPS Subject 中比對的關鍵字
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "Roaming": [
        "roam", "802.11r", "fast transition", "fast roaming",
        "bss transition", "neighbor report",
    ],
    "Miracast": [
        "miracast", "wi-fi direct display", "wireless display",
        "widi", "wfd", "wireless projection",
    ],
    "WowLAN": [
        "wowlan", "wow lan", "wake on lan", "wake-on-lan",
        "wake on wireless", "magic packet", "wol ",
    ],
    # 混淆類別：Performance 案件標題常含 connectivity 字詞但其實是速度/延遲問題
    "Performance": [
        "slow", "throughput", "speed", "latency", "lag",
        "bandwidth", "poor performance", "degraded", "low rate",
        "jitter", "ping high", "high ping", "delay",
    ],
    # 混淆類別：YB/Lost 案件標題含重連字詞易被判為 Connectivity
    "YB/Lost": [
        "yellow bang", " yb ", "yb]", "[yb", "code 10",
        "not detected", "missing adapter", "disappeared",
        "device not found", "adapter missing", "lost device",
    ],
}

# 每個類別抽樣目標數量（比需求多一些，留裕度）
SAMPLE_TARGETS: Dict[str, int] = {
    "Roaming":     10,   # 需補 4 筆，多抽備用
    "Miracast":    12,   # 需補 7 筆
    "WowLAN":      12,   # 需補 7 筆
    "Performance": 15,   # 混淆修正
    "YB/Lost":     12,   # 混淆修正
}

OUTPUT_COLS = [
    "created_date", "assignee_email", "assignee_team", "technology",
    "ips_id", "ips_title", "predicted_category_existing",
    "predicted_category_model", "confidence", "human_category", "label_notes",
]


def _tech_from_subcategory(subcategory: str) -> str:
    s = subcategory.lower()
    if "wifi" in s or "wi-fi" in s or "wlan" in s:
        return "WiFi"
    if "bt" in s or "bluetooth" in s:
        return "BT"
    return subcategory.strip() or "WiFi"


def _team_from_tech(tech: str) -> str:
    return "bt" if tech.upper() == "BT" else "wifi"


def _load_existing_titles(dirs: List[str]) -> Set[str]:
    """已標記或已在 template 中的 ips_title（小寫），避免重複送標。"""
    seen: Set[str] = set()
    patterns = [
        "CFE_input/weekly_labels_*.csv",
        "tuning_outputs/weekly_*/weekly_labeling_template.csv",
    ]
    import glob
    for pattern in patterns:
        for f in glob.glob(pattern):
            try:
                for row in csv.DictReader(open(f, encoding="utf-8-sig")):
                    t = (row.get("ips_title") or row.get("Subject") or "").strip().lower()
                    if t:
                        seen.add(t)
            except Exception:
                pass
    return seen


def _load_ips_data(ips_path: str) -> List[Dict[str, str]]:
    rows = []
    for enc in ("utf-8-sig", "latin-1", "cp950"):
        try:
            with open(ips_path, encoding=enc) as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    if not rows:
        raise RuntimeError(f"無法讀取 IPS 資料：{ips_path}")
    return rows


def _matches(title_lower: str, keywords: List[str]) -> bool:
    return any(k.lower() in title_lower for k in keywords)


def main() -> None:
    parser = argparse.ArgumentParser(description="針對性抽樣補標候選清單")
    parser.add_argument("--ips", default="IPS_data_exported.csv")
    parser.add_argument("--out", default="")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not args.out:
        ts = datetime.now().strftime("%Y%m%d")
        args.out = f"targeted_labeling_supplement_{ts}.csv"

    random.seed(args.seed)

    print(f"[INFO] 讀取 IPS 資料：{args.ips}")
    ips_rows = _load_ips_data(args.ips)
    print(f"[INFO] 共 {len(ips_rows)} 筆 IPS 案件")

    existing_titles = _load_existing_titles([])
    print(f"[INFO] 已標記/已在 template 中的標題：{len(existing_titles)} 筆（排除重複用）")

    # 候選分組
    candidates: Dict[str, List[Dict[str, str]]] = {cat: [] for cat in CATEGORY_KEYWORDS}

    for row in ips_rows:
        title = (row.get("Subject") or "").strip()
        title_lower = title.lower()
        if not title or title_lower in existing_titles:
            continue
        # 建立 template 格式的 row
        opened = (row.get("Date/Time Opened") or "").strip()
        try:
            dt = datetime.strptime(opened, "%m/%d/%Y %I:%M %p")
            created_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            created_date = ""

        tech = _tech_from_subcategory(row.get("Case Subcategory") or "")
        base = {
            "created_date": created_date,
            "assignee_email": "",
            "assignee_team": _team_from_tech(tech),
            "technology": tech,
            "ips_id": (row.get("Case Number") or "").strip(),
            "ips_title": title,
            "predicted_category_existing": "",
            "predicted_category_model": "",
            "confidence": "",
            "human_category": "",
            "label_notes": "",
        }

        for cat, keywords in CATEGORY_KEYWORDS.items():
            if _matches(title_lower, keywords):
                # 避免同一 title 進多個類別（取第一個匹配）
                base_copy = dict(base)
                base_copy["predicted_category_model"] = cat
                base_copy["label_notes"] = f"[補標目標: {cat}]"
                candidates[cat].append(base_copy)
                break  # 只歸到第一個匹配類別

    output_rows: List[Dict[str, str]] = []
    print()
    for cat, target in SAMPLE_TARGETS.items():
        pool = candidates[cat]
        random.shuffle(pool)
        sampled = pool[:target]
        print(f"  {cat:15s}: 候選 {len(pool):4d} 筆 → 抽取 {len(sampled):2d} 筆")
        output_rows.extend(sampled)

    if not output_rows:
        print("[WARN] 沒有任何符合條件的候選，請確認 IPS 資料路徑正確。")
        return

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\n[OK] 已輸出 {len(output_rows)} 筆補標候選 → {args.out}")
    print("[提示] 下週 Monday pipeline 執行後，可將此檔案的列追加到")
    print("       tuning_outputs/weekly_YYYYMMDD/weekly_labeling_template.csv")


if __name__ == "__main__":
    main()
