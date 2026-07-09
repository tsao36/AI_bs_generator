-- Purpose:
-- 1) Preview rows where reporter was likely overwritten by HSD owner.
-- 2) Repair known mismatch sample rows from checker report.
-- 3) Keep a backup copy for rollback.
--
-- Run in psql as a single transaction.

BEGIN;

-- A) Preview: known mismatch sample from checker report
SELECT jira_id, reporter, jira_reporter_name, hsd_owner, hsd_id, hsd_promoted_id
FROM ips_jira_bugs
WHERE jira_id IN (
  'BT-114738',
  'CIE-11587',
  'CIE-11894',
  'CIE-12053',
  'CIE-12162',
  'CIE-12543'
)
ORDER BY jira_id;

-- B) Backup rows before update
DROP TABLE IF EXISTS ips_jira_bugs_fix_backup_20260621;
CREATE TABLE ips_jira_bugs_fix_backup_20260621 AS
SELECT *
FROM ips_jira_bugs
WHERE jira_id IN (
  'BT-114738',
  'CIE-11587',
  'CIE-11894',
  'CIE-12053',
  'CIE-12162',
  'CIE-12543'
);

-- C) Repair reporter from jira_reporter_name, and clear suspicious HSD linkage
--    only for the known affected set and only when hsd_id is the polluted value.
UPDATE ips_jira_bugs
SET
  reporter = CASE
    WHEN jira_reporter_name IS NULL OR TRIM(jira_reporter_name) = '' OR UPPER(TRIM(jira_reporter_name)) = 'NA' THEN reporter
    WHEN POSITION(',' IN jira_reporter_name) > 0 THEN
      TRIM(SPLIT_PART(jira_reporter_name, ',', 2)) || ' ' || TRIM(SPLIT_PART(jira_reporter_name, ',', 1))
    ELSE jira_reporter_name
  END,
  hsd_id = 'NA',
  hsd_owner = 'NA',
  hsd_promoted_id = '0'
WHERE jira_id IN (
  'BT-114738',
  'CIE-11587',
  'CIE-11894',
  'CIE-12053',
  'CIE-12162',
  'CIE-12543'
)
AND COALESCE(hsd_id, 'NA') = '14020378740';

-- D) Verify after update
SELECT jira_id, reporter, jira_reporter_name, hsd_owner, hsd_id, hsd_promoted_id
FROM ips_jira_bugs
WHERE jira_id IN (
  'BT-114738',
  'CIE-11587',
  'CIE-11894',
  'CIE-12053',
  'CIE-12162',
  'CIE-12543'
)
ORDER BY jira_id;

COMMIT;
