# WiFi/BT Root Cause Analysis (2021-2026)

- Input file: customer issue analysis/outputs/WiFi BT JIRA all time closed with SW FIX.csv
- Rows analyzed (WIFI+BT, year in 2021-2026): 817

## Generic Root Cause Description by Category

### WIFI
- Connectivity: 主因多落在 Firmware Config / Parameter / Tuning (70)、Insufficient Root-Cause Detail (68)、Power State / Reset / PLDR (44)。
- YB/Lost: 主因多落在 State Machine / Flow Handling (9)、Firmware Config / Parameter / Tuning (7)、Power State / Reset / PLDR (6)。
- BSOD: 主因多落在 Power State / Reset / PLDR (67)、Firmware Config / Parameter / Tuning (67)、Race / Timing / Interrupt Ordering (55)。
- Performance: 主因多落在 RF / Coexistence / Channel Behavior (13)、Firmware Config / Parameter / Tuning (10)、Race / Timing / Interrupt Ordering (6)。

### BT
- Connectivity: 主因多落在 Power State / Reset / PLDR (67)、Race / Timing / Interrupt Ordering (67)、Firmware Config / Parameter / Tuning (56)。
- YB/Lost: 主因多落在 Power State / Reset / PLDR (27)、Race / Timing / Interrupt Ordering (15)、State Machine / Flow Handling (15)。
- BSOD: 主因多落在 Power State / Reset / PLDR (15)、Driver-OS Interface / API / OID (10)、Firmware Config / Parameter / Tuning (9)。
- HLK: 主因多落在 Power State / Reset / PLDR (4)、Security / Signature / Validation (4)、Firmware Config / Parameter / Tuning (3)。

## Table Files

- WIFI by year table: customer issue analysis/outputs/root_cause_table_WIFI_by_year_2021_2026.csv
- BT by year table: customer issue analysis/outputs/root_cause_table_BT_by_year_2021_2026.csv
- Categorized row-level export: customer issue analysis/outputs/root_cause_categorized_rows_WIFI_BT_2021_2026.csv