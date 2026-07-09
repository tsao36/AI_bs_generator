# Golden Training Set Export (2026-06-13)

## Files in this package

- golden_training_set_20260613.csv
- golden_training_set_20260613_source_files.csv
- golden_training_set_20260613_excluded_conflicts.csv
- golden_training_set_20260613_summary.txt
- training_golden_candidates_manifest_20260613.csv
- training_exclude_manifest_20260613.csv

## Scope reviewed

- Source folders scanned:
  - CFE_input
  - CFE_reviewed_issue

## Rules used to build golden set

- Keep only rows with non-empty ips_title and non-empty human_category.
- Apply label normalization from train_issue_category_model._normalise_label.
- Keep only CSV files that can be read and contain data rows.
- Exclude ambiguous titles where the same normalized title maps to multiple human_category labels.
- Deduplicate exact duplicate rows after filtering.

## Result summary

- Input labeled rows: 1336
- Conflicting title keys: 7
- Excluded conflict rows: 66
- Final golden rows: 1266
- Source files contributing rows: 33
