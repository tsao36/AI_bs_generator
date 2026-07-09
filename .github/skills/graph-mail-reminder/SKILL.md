---
name: graph-mail-reminder
description: 'Send reminder emails through Microsoft Graph using existing project scripts. Use when you need to update recipients, subject, PPT link, or reminder body and then run dry-run/send for CE notifications.'
argument-hint: 'Describe audience, subject, PPT link, and reminder tone'
user-invocable: true
---

# Graph Mail Reminder

Use this skill to prepare and send reminder emails via Microsoft Graph by reusing the existing project mail script.

## When To Use
- Send CE reminder emails from your mailbox through Graph.
- Update reminder recipients for a specific campaign.
- Change reminder email subject, message body, or SharePoint/PPT link.
- Run a safe dry-run before sending production emails.

## Primary Script
- [send_pptx_update_reminder.py](../../../jobs/06_reporting_and_notifications/send_pptx_update_reminder.py)
- [run_pptx_update_reminder_wednesday.bat](../../../jobs/06_reporting_and_notifications/run_pptx_update_reminder_wednesday.bat)

## Procedure
1. Confirm mail goal from user:
   - Audience (TO/CC)
   - Subject line
   - PPT/SharePoint link
   - Message tone and language
2. Update recipients and content in [send_pptx_update_reminder.py](../../../jobs/06_reporting_and_notifications/send_pptx_update_reminder.py).
3. Keep auth behavior unchanged unless user requests otherwise:
   - Delegated mode sends as signed-in user ("from my email").
   - App mode requires `GRAPH_SENDER_UPN`.
4. Pick a template without editing script code:
   - `weekly`
   - `critical-topic`
   - `management`
5. Validate with dry run:
   - `python send_pptx_update_reminder.py --template critical-topic --dry-run`
5. Send using the batch launcher:
   - [run_pptx_update_reminder_wednesday.bat](../../../jobs/06_reporting_and_notifications/run_pptx_update_reminder_wednesday.bat)
6. Report sent target list and log path to user.

## Examples
- Weekly default:
  - `python send_pptx_update_reminder.py --dry-run`
- Critical topic reminder:
  - `python send_pptx_update_reminder.py --template critical-topic --dry-run`
- Management tone with custom subject:
  - `python send_pptx_update_reminder.py --template management --subject "[Action Required] CE status update" --dry-run`

## Quick References
- Recipients JSON sample: [recipients_template.json](./assets/recipients_template.json)
- Operational checklist: [runbook.md](./references/runbook.md)

## Guardrails
- Do not send email until the user confirms final recipients and subject.
- Keep recipient domains restricted to expected corporate addresses unless user explicitly approves exceptions.
- Prefer dry-run before any real send.
