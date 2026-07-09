# Issue Category Tuning Handoff

This handoff note is for teammates who want to run and extend your issue-category prediction tuning flow.

## 1. What This Project Does

Your tuning flow is a weekly quality loop for issue-category prediction:
1. Read labeled training data from `CFE_input/*.csv`.
2. Build feature text from issue title + existing predicted category + technology.
3. Train and compare two ML models (Logistic Regression vs Linear SVC).
4. Pick the better model by macro F1.
5. Export model comparison, confusion matrix, and low-confidence candidates for review.

## 2. Main Entry Point (the BAT you run)

Main command:

```bat
.\run_weekly_category_tuning.bat 0.45
```

What it does:
1. Creates dated output folder `tuning_outputs/weekly_YYYYMMDD`.
2. Runs `run_category_tuning_cycle.py`.
3. Produces:
   - `model_compare_metrics.json`
   - `best_model_confusion_matrix.csv`
   - `low_confidence_candidates.csv`

## 3. Script-Level Process Map

### A) Weekly tuning entry
- `run_weekly_category_tuning.bat`
- Purpose: parameter wrapper + dated output folder + invoke tuning cycle.

### B) Tuning cycle and model comparison
- `run_category_tuning_cycle.py`
- Purpose:
  1. Load rows through `_load_training_rows(...)` from `train_issue_category_model.py`.
  2. Train two pipelines (LR and SVC).
  3. Compare metrics and select best by macro F1.
  4. Export confusion matrix.
  5. Generate low-confidence candidates from current active model.

### C) Training data loader and baseline trainer
- `train_issue_category_model.py`
- Purpose:
  1. Read `CFE_input/*.csv`.
  2. Require `ips_title` and `human_category`.
  3. Build `feature_text` using `_compose_feature_text(...)` from `issue_category_model.py`.
  4. Train baseline production model artifact (`models/issue_category_model.joblib`) when retraining is needed.

### D) Shared feature/prediction utilities
- `issue_category_model.py`
- Purpose:
  1. Canonical feature construction (`_compose_feature_text`).
  2. Model load and prediction helpers.
  3. Category weighting helper functions.

## 4. Files To Share With Coworker

## Minimal package (run weekly tuning only)
1. `run_weekly_category_tuning.bat`
2. `run_category_tuning_cycle.py`
3. `train_issue_category_model.py`
4. `issue_category_model.py`

## Recommended full package (to leverage your full structure)
1. `run_weekly_tuning_and_prepare_labeling.bat`
2. `prepare_weekly_labeling_template.bat`
3. `prepare_weekly_labeling_template.py`
4. `run_labeling_reminder_daily.bat`
5. `send_labeling_reminders.py`
6. `ingest_reviewed_labels.py`
7. `retrain_category_model.bat`
8. `run_weekly_category_tuning.bat`
9. `run_category_tuning_cycle.py`
10. `train_issue_category_model.py`
11. `issue_category_model.py`

## Data/config assets to include
1. `CFE_input/` (at least one sample CSV with required columns)
2. `models/issue_category_model.joblib` (if they need low-confidence generation against current model)
3. `issue_category_weights.json` (if weighting behavior is part of their use)
4. `recipients.json` (if they will run labeling assignment/reminder flow)

## 5. Required Input CSV Columns

For training (`CFE_input`):
1. `ips_title` (required)
2. `human_category` (required)
3. `predicted_category` (optional but used in features)
4. `technology` (optional but used in features)

## 6. Typical Weekly Ops Sequence

1. Monday:
   - Run `run_weekly_category_tuning.bat 0.45`
2. (Optional full loop) Monday:
   - Run `run_weekly_tuning_and_prepare_labeling.bat 0.45 all`
3. During the week:
   - Engineers fill `human_category` in weekly labeling template.
4. Friday:
   - Run retrain/close-loop scripts as your team policy defines.

## 7. Environment Dependencies

Install in Python environment:

```bash
pip install scikit-learn joblib
```

If teammate also runs reminder/template automation, they need dependencies used by those scripts (for example Excel and Graph-related packages, depending on local setup).

## 8. Quick Validation After Handoff

Ask coworker to run:

```bat
.\run_weekly_category_tuning.bat 0.45
```

Then verify these files exist under latest `tuning_outputs/weekly_YYYYMMDD/`:
1. `model_compare_metrics.json`
2. `best_model_confusion_matrix.csv`
3. `low_confidence_candidates.csv`
