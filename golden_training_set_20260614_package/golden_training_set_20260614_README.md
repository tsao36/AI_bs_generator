# Golden Training Set Export (20260614)

## Files in this package

- golden_training_set_20260614.csv
- golden_training_set_20260614_source_files.csv
- golden_training_set_20260614_summary.txt
- golden_training_set_20260614_README.md

## Source

- Built from curated latest golden CSV:
  - archive/model_tuning_scrub_20260616_085850/golden_training_set_20260614_p2pmerge_round6_focus_icps_oem.csv

## Rules / assumptions

- Keep rows with non-empty ips_title and non-empty human_category.
- Keep source trace columns (source_folder, source_file) for auditability.
- This package is a direct shareable export of the latest 2026-06-14 curated golden set.

## Result summary

- Final golden rows: 1266
- Source files contributing rows: 33
- Unique categories: 22
- Conflicting normalized-title keys inside this final set: 0
