# Project Explanation - Weekly Issue Category Tuning

## 1) Project Purpose

This project improves issue category detection quality by combining:
- automated weekly issue extraction,
- team-based human labeling,
- periodic retraining,
- gated model promotion.

The goal is to keep production classification stable while continuously improving accuracy with real weekly data.

## 2) High-Level Workflow

Weekly cycle:
1. Monday: extract all new issues from last week and generate a shared labeling file.
2. Weekdays: engineers fill human_category only for their assigned rows.
3. Friday: ingest labels, retrain a candidate model, evaluate candidate vs active model, and auto-decide promotion.

## 3) Technology-Based Assignment

Assignments are generated from recipients.json using technology_assignee_map:
- WiFi issues are assigned only to the WiFi assignee list.
- BT issues are assigned only to the BT assignee list.
- The assignment is round-robin balanced within each technology group.

The generated file includes:
- assignee_email
- assignee_team
- human_category (to be filled by engineers)

## 4) Key Scripts and Batch Files

Monday automation:
- run_weekly_tuning_and_prepare_labeling.bat

Friday automation:
- run_friday_auto_close_loop.bat

Template generation (from last week new issues):
- prepare_weekly_labeling_template.py
- prepare_weekly_labeling_template.bat

Label ingestion:
- ingest_reviewed_labels.py
- ingest_weekly_reviewed_labels.bat

Retraining:
- train_issue_category_model.py
- retrain_category_model.bat

Promotion decision logic:
- friday_auto_promote_model.py

## 5) Main Data Inputs

Model training data source:
- CFE_input/*.csv

Weekly review source:
- new issues from last full week extracted from ips_jira_bugs

Configuration:
- recipients.json
- issue_category_weights.json

## 6) Main Outputs

Weekly folder:
- tuning_outputs/weekly_YYYYMMDD/

Important output files:
- weekly_labeling_template.csv
- model_compare_metrics.json
- best_model_confusion_matrix.csv
- low_confidence_candidates.csv
- model_promotion_decision.json

## 7) Promotion Gate (Friday)

Default promotion policy:
- minimum accepted new labels reached,
- candidate macro_f1 gain above threshold,
- key class recall does not degrade beyond tolerance.

If policy is not passed, active model remains unchanged.

## 8) Team Operating Guidance

Engineers:
1. Open weekly_labeling_template.csv.
2. Filter by assignee_email to own email.
3. Fill human_category for assigned rows.
4. Optionally fill label_notes.

Model owner:
1. Ensure Monday and Friday schedules run successfully.
2. Review model_promotion_decision.json every Friday.
3. Track trend of macro_f1 and class recall over time.

## 9) Why This Design

This setup avoids one-person labeling bottlenecks and keeps work distributed.
It also prevents risky model changes by enforcing a promotion gate before replacing the active model.
