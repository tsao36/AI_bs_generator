# Golden Training Set Export (2026-06-03)

## Files in this package

- `golden_training_set_20260603.csv`
- `golden_training_set_20260603_source_files.csv`
- `golden_training_set_20260603_excluded_conflicts.csv`
- `golden_training_set_20260603_summary.txt`
- `training_golden_candidates_manifest.csv`
- `training_exclude_manifest.csv`

## Scope reviewed

- Source folders scanned:
  - `CFE_input`
  - `CFE_reviewed_issue`

## Rules used to build golden set

- Keep only rows with non-empty `ips_title` and non-empty `human_category`.
- Keep only CSV files that can be read and contain data rows.
- Exclude ambiguous titles where the same normalized title maps to multiple `human_category` labels.
- Deduplicate exact duplicate rows after filtering.

## Result summary

- Input labeled rows: 1018
- Conflicting title keys: 4
- Excluded conflict rows: 50
- Final golden rows: 964
- Source files contributing rows: 28

## Notes

- Empty files were found and excluded (see `training_exclude_manifest.csv`).
- Conflicting labels were not silently merged; they were removed and listed in `golden_training_set_20260603_excluded_conflicts.csv` for manual adjudication.
- `source_folder` and `source_file` are retained in the golden CSV for traceability.
