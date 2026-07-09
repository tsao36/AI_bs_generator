# Weekly Category Tuning SOP

This document explains the weekly operating process for issue-category tuning, human labeling, retraining, and model promotion.

## Goal

Keep category prediction quality improving with a simple weekly loop:
- Monday: generate weekly review set for engineers.
- Weekdays: engineers fill human_category.
- Friday: automatically ingest labels, retrain candidate model, and decide whether to promote.

## Files and Scripts

Main Monday script:
- run_weekly_tuning_and_prepare_labeling.bat

Main Friday script:
- run_friday_auto_close_loop.bat

Engineer labeling file (generated weekly):
- tuning_outputs/weekly_YYYYMMDD/weekly_labeling_template.csv

Decision report file (generated on Friday):
- tuning_outputs/weekly_YYYYMMDD/model_promotion_decision.json

## Monday Scheduled Job

Schedule this command every Monday (example 09:00):

.\run_weekly_tuning_and_prepare_labeling.bat 0.45 50

Parameters:
- 0.45: low-confidence threshold
- 50: number of issues to ask engineers to label

Outputs generated in a dated folder:
- model_compare_metrics.json
- best_model_confusion_matrix.csv
- low_confidence_candidates.csv
- weekly_labeling_template.csv

## Engineer Action (Monday to Friday)

Engineers edit the weekly file in the same directory:
- tuning_outputs/weekly_YYYYMMDD/weekly_labeling_template.csv

Required column to fill:
- human_category

Optional column:
- label_notes

Keep all existing rows and columns unchanged. Only fill values.

## Friday Scheduled Job

Schedule this command every Friday (example 17:00):

.\run_friday_auto_close_loop.bat

What Friday automation does:
1. Read latest weekly_labeling_template.csv.
2. Ingest rows with non-empty human_category into CFE_input.
3. Train candidate model.
4. Compare candidate vs active model.
5. Auto-promote candidate only if policy is passed.
6. Write model_promotion_decision.json.
7. Re-run weekly tuning report in the same weekly folder.

## Auto-Promotion Policy

Default conditions to promote candidate model:
- accepted labeled rows >= 10
- macro_f1 gain >= 0.005
- key class recall does not drop more than 0.02

Default key classes:
- Audio
- Connectivity
- BSOD
- System Hang
- Performance

If conditions are not met, active model is kept.

## Weekly Checklist

Monday:
1. Confirm scheduled run succeeded.
2. Send weekly_labeling_template.csv to engineers.

Friday:
1. Confirm engineers completed human_category.
2. Confirm Friday scheduled run succeeded.
3. Review model_promotion_decision.json.
4. Review model_compare_metrics.json and confusion matrix.

## Troubleshooting

If Friday says accepted labels are too low:
- Engineers likely did not fill enough human_category values.
- Keep active model and rerun Friday script after labels are completed.

If no weekly folder is found:
- Run Monday script manually once.

If Python dependency errors happen:
- Install dependencies in environment:
  pip install scikit-learn joblib

## Manual Commands (Backup)

If needed, run steps manually:

Monday generation:
.\run_weekly_tuning_and_prepare_labeling.bat 0.45 50

Friday close-loop:
.\run_friday_auto_close_loop.bat

## Ownership

Recommended ownership split:
- Scheduler owner: data/tooling owner
- Labeling owner: engineering team
- Promotion review owner: model owner or triage lead
