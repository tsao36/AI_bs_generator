# Graph Reminder Mail Runbook

## Preconditions
- Python environment is available.
- Graph auth settings are configured in environment.
- Recipient list and subject are reviewed with the requester.

## Recommended Flow
1. Edit reminder content and recipients in `jobs/06_reporting_and_notifications/send_pptx_update_reminder.py`.
2. Select template and run dry-run from `jobs/06_reporting_and_notifications`:
   - `python send_pptx_update_reminder.py --template weekly --dry-run`
   - `python send_pptx_update_reminder.py --template critical-topic --dry-run`
   - `python send_pptx_update_reminder.py --template management --dry-run`
3. Verify output:
   - To list
   - Cc list
   - Subject
   - HTML length
4. Run batch to send:
   - `run_pptx_update_reminder_wednesday.bat`
5. Check log under:
   - `jobs/06_reporting_and_notifications/logs`

## Troubleshooting
- `GRAPH_SENDER_UPN is required`: set sender UPN when using app auth mode.
- Empty recipient error: ensure default recipient list in script is not empty.
- Auth/token failures: verify client secret and Graph auth mode.
