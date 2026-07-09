-- Semantic view for chatbot access (read-only)
-- Run once in Postgres with a privileged user.

CREATE OR REPLACE VIEW vw_issues AS
SELECT
    ips_case_number,
    ips_title,
    ips_status,
    ips_sub_status,
    ips_created_date,
    ips_last_modified_date,
    ips_last_modified_days,
    ips_open_days,
    reporter,
    jira_id,
    jira_title,
    jira_status,
    bug_project,
    COALESCE(
        NULLIF(ips_platform, 'NA'),
        NULLIF(jira_platform, 'NA'),
        NULLIF(hsd_platform, 'NA'),
        'NA'
    ) AS platform,
    bug_category_custom,
    bug_criticality_custom,
    bug_status_custom,
    bug_created_date,
    COALESCE(
        NULLIF(TRIM(COALESCE(CFE_Team::text, '')), 'NA'),
        NULLIF(TRIM(COALESCE(engineer::text, '')), 'NA'),
        NULLIF(TRIM(COALESCE(reporter::text, '')), 'NA'),
        'NA'
    ) AS cfe_team,
    bug_origin,
    bug_created_year,
    jira_found_by,
    customer_custom,
    bug_closed_date,
    bug_created_date,
    is_ips_promoted_to_jira,
    ips_jira_promo_status,
    jira_final_component,
    jira_state_reason
FROM ips_jira_bugs;

-- Optional: grant read-only access to the view
-- GRANT SELECT ON vw_issues TO your_readonly_user;
