# Task Scheduler Summary

Source: XML exports in this folder (`Task_scheduler/*.xml`).

## Active Task Map

| Task URI | Trigger | StartBoundary | Command |
|---|---|---|---|
| `\Weekly Monday issue category training set GET` | Weekly (Monday, every 1 week) | `2026-04-13T11:01:00+08:00` | `run_weekly_tuning_and_prepare_labeling.bat` |
| `\Daily bug category tuning CFE reminder` | Daily (every 1 day) | `2026-04-21T11:07:35` | `run_labeling_reminder_daily.bat` |
| `\Analyze issue category human prediction` | Weekly (Friday, every 1 week) | `2026-04-13T17:00:00` | `run_friday_auto_close_loop.bat` |
| `\Run Friday to check IPS issue category tuning result` | Weekly (Friday, every 1 week) | `2026-05-16T17:30:30` | `run_weekly_kpi_health_check.bat` |
| `\Run Friday to send tuning result update to team` | Weekly (Friday, every 1 week) | `2026-06-01T23:20:43+08:00` | `run_friday_model_update_and_summary.bat` |
| `\Weekly JIRA VERIFY closure notification` | Weekly (Monday, every 1 week) | `2026-06-01T11:01:05` | `run_verify_issue_close_notify_weekly.bat` |
| `\Update HSD` | Daily (every 1 day) | `2026-02-06T11:15:00` | `run_hsd_only.bat` |
| `\Daily issue loading notification` | Daily (every 1 day) | `2026-04-16T10:30:46+08:00` | `run_offload_loading_summary_daily.bat` |
| `\CFE offload Arrangement` | TimeTrigger (repeat every 30 min) | `2026-03-09T21:23:00` | `run_offload_reporter_issues_send_email_scheduler.bat` |
| `\2026 overall loading per engineer` | Weekly (Friday, every 1 week) | `2026-05-15T11:08:43` | `run_weekly_current_yearly_issue_project_count.bat` |
| `\Monthly issue overview update` | Weekly (Friday, every 4 weeks) | `2026-06-26T16:10:08+08:00` | `run_management_monthly_customer_issue_report.bat` |
| `\Wireless CE Weekly meeting summary` | Weekly (Monday, every 1 week) | `2026-03-02T11:15:23` | `C:\Users\jtsao1\OneDrive - Intel Corporation\Documents\My workspace\AI Project\run_onenote_summary_email.bat` |

## Notes

- All 12 task XML files are UTF-16 Task Scheduler exports.
- `\Wireless CE Weekly meeting summary` points to a BAT file outside this repo (`AI Project` workspace).
