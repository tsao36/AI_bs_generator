-- Purpose:
-- Broad cleanup for known HSD linkage pollution (`hsd_id = 14020378740`).
--
-- This script:
-- 1) Previews impact counts.
-- 2) Backs up all affected rows.
-- 3) Restores reporter from jira_reporter_name when available.
-- 4) Clears polluted HSD linkage fields for affected rows.
-- 5) Verifies post-fix counts.
--
-- Execute in psql (single transaction).

BEGIN;

-- A) Preview impact
SELECT COUNT(*) AS polluted_rows
FROM ips_jira_bugs
WHERE COALESCE(hsd_id, 'NA') = '14020378740';

SELECT COUNT(*) AS reporter_mismatch_rows
FROM ips_jira_bugs
WHERE COALESCE(hsd_id, 'NA') = '14020378740'
  AND COALESCE(NULLIF(TRIM(jira_reporter_name), ''), 'NA') <> 'NA'
  AND LOWER(TRIM(COALESCE(reporter, ''))) <> LOWER(TRIM(
        CASE
          WHEN POSITION(',' IN jira_reporter_name) > 0
            THEN TRIM(SPLIT_PART(jira_reporter_name, ',', 2)) || ' ' || TRIM(SPLIT_PART(jira_reporter_name, ',', 1))
          ELSE jira_reporter_name
        END
      ));

-- B) Backup rows for rollback
DROP TABLE IF EXISTS ips_jira_bugs_fix_backup_polluted_hsd_20260621;
CREATE TABLE ips_jira_bugs_fix_backup_polluted_hsd_20260621 AS
SELECT *
FROM ips_jira_bugs
WHERE COALESCE(hsd_id, 'NA') = '14020378740';

-- C1) Restore reporter from Jira reporter name where possible
UPDATE ips_jira_bugs
SET reporter = CASE
    WHEN POSITION(',' IN jira_reporter_name) > 0
      THEN TRIM(SPLIT_PART(jira_reporter_name, ',', 2)) || ' ' || TRIM(SPLIT_PART(jira_reporter_name, ',', 1))
    ELSE jira_reporter_name
  END
WHERE COALESCE(hsd_id, 'NA') = '14020378740'
  AND COALESCE(NULLIF(TRIM(jira_reporter_name), ''), 'NA') <> 'NA';

-- C2) Clear polluted HSD linkage
UPDATE ips_jira_bugs
SET
  hsd_id = 'NA',
  hsd_owner = 'NA',
  hsd_promoted_id = '0'
WHERE COALESCE(hsd_id, 'NA') = '14020378740';

-- D) Verify post-fix
SELECT COUNT(*) AS remaining_polluted_rows
FROM ips_jira_bugs
WHERE COALESCE(hsd_id, 'NA') = '14020378740';

-- Sample check for known mismatches
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

-- Rollback recipe (manual, if needed):
-- BEGIN;
-- DELETE FROM ips_jira_bugs WHERE COALESCE(hsd_id, 'NA') = '14020378740';
-- INSERT INTO ips_jira_bugs SELECT * FROM ips_jira_bugs_fix_backup_polluted_hsd_20260621;
-- COMMIT;
