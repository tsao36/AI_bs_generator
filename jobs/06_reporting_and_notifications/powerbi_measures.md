# Power BI Measures — ips_jira_bugs Dashboard

## Data Source
- Snowflake DB: `ips_jira_bugs`
- Relevant views/tables: (update as needed)
- Power BI refresh schedule: (update as needed)

---

## Measures
<!-- HOW TO ADD: In Power BI, right-click a measure > "Copy" or open the formula bar, copy the full expression, then paste below as a new section. -->

---

bug_category = IF(ips_jira_bugs[ips_category] <> "NA", ips_jira_bugs[ips_category], ips_jira_bugs[bug_project])
---

bug_category_custom = IF(ips_jira_bugs[bug_category] in {"Debug Tools", "OEM Tools","WOT","DBGT"}, "Tools", IF(ips_jira_bugs[bug_category] in {"Bluetooth (BT)","BT"}, "Bluetooth", IF(ips_jira_bugs[bug_category] in {"WCS Innovation Engineering","CIE"}, "ICPS/Killer",IF(ips_jira_bugs[bug_category] in {"WiFi Linux","WiFi Windows","WIFI","WiFi AMT","NA"}, "WiFi",ips_jira_bugs[bug_category]))))//NA count as WiFi because most likely it is WiFi

---

bug_closed_date = 
VAR Placeholder = DATE(2026, 1, 1)

// Replace placeholder with BLANK(), otherwise keep the real date
VAR ipsDate  = IF ( ips_jira_bugs[ips_closed_date] = Placeholder, BLANK(), ips_jira_bugs[ips_closed_date] )
VAR jiraDate = IF ( ips_jira_bugs[jira_closed_date] = Placeholder, BLANK(), ips_jira_bugs[jira_closed_date] )

// Find the later of the two valid dates
VAR LatestRealDate =
    MAXX (
        {
            ipsDate,
            jiraDate
        },
        [Value]
    )
RETURN
// If both are placeholders, return the placeholder (2026-01-01)
// Otherwise, return the latest valid date
IF (
    ISBLANK ( LatestRealDate ),
    Placeholder,
    LatestRealDate
)


---

bug_created_date = 
VAR origin   = ips_jira_bugs[bug_origin]
VAR ipsDate  = ips_jira_bugs[ips_created_date]
VAR hsdDate  = ips_jira_bugs[hsd_submitted_date]
VAR jiraDate = ips_jira_bugs[jira_created_date]
RETURN
SWITCH (
    TRUE(),
    origin = "IPS" && NOT ISBLANK ( ipsDate ), ipsDate,
    origin = "HSD" && NOT ISBLANK ( hsdDate ), hsdDate,
    NOT ISBLANK ( jiraDate ), jiraDate,
    BLANK()
)

---

bug_created_quarter = 
VAR d = ips_jira_bugs[bug_created_date]
RETURN
IF ( ISBLANK ( d ), BLANK(), "Q" & FORMAT ( d, "Q" ) )

---


bug_created_year = 
VAR origin = ips_jira_bugs[bug_origin]
RETURN
SWITCH (
    TRUE(),
    origin = "IPS", YEAR ( ips_jira_bugs[ips_created_date] ),
    origin = "HSD", YEAR ( ips_jira_bugs[hsd_submitted_date] ),
    YEAR ( ips_jira_bugs[jira_created_date] )
)

---
bug_created_year_month = 
    FORMAT(
        ips_jira_bugs[bug_created_date],
        "YYYYMM" // This format gives the year followed by the two-digit month number
    )
---
bug_created_year_text = format(ips_jira_bugs[bug_created_year], "General Number")
---
bug_criticality = IF(ips_jira_bugs[ips_priority_custom] <> "NA", ips_jira_bugs[ips_priority_custom], ips_jira_bugs[jira_exposure]) 
//If the ips_priority is not "NA", it uses the value from the ips_priority column.
//If the ips_priority is "NA", it uses the value from the jira_exposure column
//Using ips_priority_custom instead of ips_priority to demote priority when there is only IPS, not JIRA
---
bug_criticality_custom = IF(ips_jira_bugs[bug_criticality] in {"1-Critical", "Critical"}, "P1-Show Stopper", IF(ips_jira_bugs[bug_criticality] in {"2-High","High"}, "P2-High", IF(ips_jira_bugs[bug_criticality] in {"3-Medium","Medium"}, "P3-Medium",IF(ips_jira_bugs[bug_criticality] in {"4-Low","Low"}, "P4-Low",ips_jira_bugs[bug_category]))))
---
bug_hardware = IF(ips_jira_bugs[ips_hardware] <> "NA", ips_jira_bugs[ips_hardware], ips_jira_bugs[jira_nic]) 
//If the ips_hardware is not "NA", it uses the value from the ips_hardware.
//If the ips_hardware is "NA", it uses the value from the jira_nic column
---
bug_hardware_custom = 
VAR HW = ips_jira_bugs[bug_hardware]
RETURN
    SWITCH (
        TRUE (),

        
        /* =========================
        Whale Peak 2 (BE211)
        ========================= */
        HW IN {
            "Whale Peak 2 Blazar-I (BE211)",
            "Whale Peak 2 BnJ (BE211)",
            "Whale Peak 2 Scorpius (BE211)",
            "Whale Peak 2 Scorpius 2 (BE211)",
            "Whale Peak 2 Scorpius-2 (BE211)",
            "Whale Peak 2 Scorpius-2F (BE211)",
            "Whale Peak 2 STC Blazar-I (BE211)",
            "Whale Peak 2 STC BnJ (BE211)",
            "Whale Peak 2 STC Scorpius (BE211)"
        }, "WhP2 (BE211)",

        
        /* =========================
        Spider Peak 2 (BE213)
        ========================= */
        HW IN {
            "Spider Peak 2 Blazar-I (BE213)",
            "Spider Peak 2 Scorpius (BE213)",
            "Spider Peak 2 Scorpius-2 (BE213)"
        }, "SpP2 (BE213)",


        /* =========================
           Fillmore Peak 2 (BE201)
           ========================= */
        HW IN {
            "Fillmore Peak 2 Blazar (BE201)",
            "Fillmore Peak 2 Scorpius (BE201)"
        }, "FmP2 (BE201)",

        /* =========================
           Gale Peak 2 (BE200)
           ========================= */
        HW IN {
            "Gale Peak 2 (BE200)",
            "Gale Peak 2 (XXXX)"
        }, "GaP2 (BE200)",

        /* =========================
           Garfield Peak 2 (AX211)
           ========================= */
        HW IN {
            "Garfield Peak 2 Solar (AX211)",
            "Garfield Peak 2 Magnetar (AX211)",
            "Garfield Peak 2 Blazar (AX211)",
            "Garfield Peak 2 Scorpius (AX211)"
        }, "GfP2 (AX211)",

        /* =========================
           Harrison Peak 2 (AX201)
           ========================= */
        HW IN {
            "Harrison Peak 2 Solar (AX201)",
            "Harrison Peak 2 (AX201)",
            "Harrison Peak 2 Magnetar (AX201)",
            "Harrison Peak 2 Quasar (AX201)"
        }, "HrP2 (AX201)",

        /* =========================
           Jefferson Peak 1 (9461)
           ========================= */
        HW IN {
            "Jefferson Peak 1 Pulsar (9461)",
            "Jefferson Peak 1 Quasar (9461)",
            "Jefferson Peak 1 Solar (9461)"
        }, "JfP1 (9461)",

        /* =========================
           Jefferson Peak 1 (9462)
           ========================= */
        HW IN {
            "Jefferson Peak 1 Pulsar (9462)",
            "Jefferson Peak 1 Quasar (9462)",
            "Jefferson Peak 1 Solar (9462)"
        }, "JfP1 (9462)",

        /* =========================
           Jefferson Peak 2 (9560)
           ========================= */
        HW IN {
            "Jefferson Peak 2 Pulsar (9560)",
            "Jefferson Peak 2 Quasar (9560)",
            "Jefferson Peak 2 Solar (9560)"
        }, "JfP2 (9560)",

        /* =========================
           Johnson Peak 2 (AX203)
           ========================= */
        HW IN {
            "Johnson Peak 2 (AX203)",
            "Johnson Peak 2 Solar (AX203)"
        }, "JnP (AX203)",

        /* =========================
           Killer
           ========================= */
        HW IN { "Killer (1550)" }, "Killer (1550)",
        HW IN { "Killer (1550i)" }, "Killer (1550i)",

        /* =========================
           Madison Peak (AX204)
           ========================= */
        HW IN {
            "Madison Peak Solar (AX204)"
        }, "Madison Peak (AX204)",

        /* =========================
           Misty Peak 2 (BE202)
           ========================= */
        HW IN {
            "Misty Peak 2 (XXXX)"
        }, "MsP2 (BE202)",

        /* =========================
           Cyclone Peak 2 (AX200)
           ========================= */
        HW IN {
            "Cyclone Peak 2 (22260)"
        }, "CcP2 (AX200)",

        /* =========================
           Garfield Peak 4 (AX411)
           ========================= */
        HW IN {
            "Garfield Peak 4 Blazar (AX411)",
            "Garfield Peak 4 Solar (AX411)"
        }, "GfP4 (AX411)",

        /* =========================
           Sandy Peak (3168)
           ========================= */
        HW IN {
            "Sandy Peak (3168)"
        }, "SdP (3168)",

        /* =========================
           Snowfield Peak (8260)
           ========================= */
        HW IN {
            "Snowfield Peak (8260)"
        }, "SfP (8260)",

        /* =========================
           Stone Peak 1 (3165)
           ========================= */
        HW IN {
            "Stone Peak 1 (3165)"
        }, "StP1 (3165)",

        /* =========================
           Stone Peak 2 (7265)
           ========================= */
        HW IN {
            "Stone Peak 2 (7265)",
            "Stone Peak 2 D1 (7265)"
        }, "StP2 (7265)",

        /* =========================
           Thunder Peak 2 (9260)
           ========================= */
        HW IN {
            "Thunder Peak 2 (9260)"
        }, "ThP2 (9260)",

        /* =========================
           Typhoon Peak 2 (AX210)
           ========================= */
        HW IN {
            "Typhoon Peak 2 (AX210)",
            "Typhoon Peak 2 (XXXX)"
        }, "TyP2 (AX210)",

        /* =========================
           Wilkins Peak 2 (7260)
           ========================= */
        HW IN {
            "Wilkins Peak 2 (7260)"
        }, "WP2 (7260)",

        /* =========================
           Windstorm Peak (8265)
           ========================= */
        HW IN {
            "Windstorm Peak (8265)"
        }, "WsP2 (8265)",


        /* ==========================================================
           NEW: Spider Peak 2 (BE213)  -> SpP2 (BE213)
           (from the module list you provided)
           ========================================================== */
        HW IN {
            "Spider Peak 2 Blazar-I (BE213)",
            "Spider Peak 2 Scorpius (BE213)",
            "Spider Peak 2 Scorpius-2 (BE213)"
        }, "SpP2 (BE213)",

        /* ==========================================================
           NEW: Whale Peak 2 (BE211)   -> WhP2 (BE211)
           (from the module list you provided)
           ========================================================== */
        HW IN {
            "Whale Peak 2 Blazar-I (BE211)",
            "Whale Peak 2 BnJ (BE211)",
            "Whale Peak 2 Scorpius (BE211)",
            "Whale Peak 2 Scorpius 2 (BE211)",
            "Whale Peak 2 Scorpius-2 (BE211)",
            "Whale Peak 2 STC Blazar-I (BE211)",
            "Whale Peak 2 STC BnJ (BE211)",
            "Whale Peak 2 STC Scorpius (BE211)"
        }, "WhP2 (BE211)",

        /* =========================
           Default / fallback
           ========================= */
        BLANK ()
        -- or: HW   (if you prefer to keep original text when unmapped)
    )
---
Bug_num_issue_closed_as_fixed = 
          COUNTROWS(
            FILTER(
                ips_jira_bugs,
                ips_jira_bugs[jira_state_reason] IN {"Fixed", "Product Change", "3rd party", "Workaround", "Duplicate Root Cause", "Unknown Fix"} &&
                ips_jira_bugs[jira_team] IN {"cae"} &&
                ips_jira_bugs[jira_found_by] IN {"customer found"} &&
                ips_jira_bugs[bug_status_custom] IN {"Closed"}
            )
        )
---
Bug_num_of_closed_by_SW_Fix = countrows(FILTER(ips_jira_bugs,ips_jira_bugs[clo] IN {"Verify"})) //bug status custom is closed, verify, implemented
---
Bug_num_of_closed_sighting = countrows(FILTER(ips_jira_bugs,ips_jira_bugs[bug_status_custom] in {"closed"})) //bug status custom is closed
---
Bug_num_of_closed_verify_implemented_sighting = countrows(FILTER(ips_jira_bugs,ips_jira_bugs[bug_status_custom] IN {"Verify"})) //bug status custom is closed, verify, implemented
---
Bug_num_of_open_sighting = countrows(FILTER(ips_jira_bugs,ips_jira_bugs[bug_status_custom] in {"open"}))
---
bug_origin = 
VAR ipsNum =
    // If ips_case_number is text, VALUE() safely converts; returns BLANK() if not numeric
    VALUE ( ips_jira_bugs[ips_case_number] )
VAR hasIPS =
    NOT ISBLANK ( ipsNum ) && ipsNum > 0
VAR hasHSD =
    NOT ISBLANK ( ips_jira_bugs[hsd_submitted_date] )
RETURN
SWITCH (
    TRUE(),
    hasIPS, "IPS",
    hasHSD, "HSD",
    "JIRA"
)

---
bug_statistics = 
ADDCOLUMNS (
    month_list,
    "jira_wifi_critical_bug_count",
        CALCULATE (
            COUNTROWS(ips_jira_bugs),
            FILTER (
                ips_jira_bugs,
                ips_jira_bugs[jira_id] <> "NA" &&                                               // this is a real JIRA
                ips_jira_bugs[bug_project] = "WIFI" &&                                          // WIFI
                ips_jira_bugs[jira_exposure] IN {"1-Critical"} &&                               // critical
                ips_jira_bugs[jira_created_date] <= EDATE(month_list[round_month_date], 1)  &&  // craeted during or before next month (this month + 1)
                ips_jira_bugs[customer_closed_date] >= month_list[round_month_date]             // closed during or after this month
            )
        ),
    "jira_wifi_high_med_bug_count",
        CALCULATE (
            COUNTROWS(ips_jira_bugs),
            FILTER (
                ips_jira_bugs,
                ips_jira_bugs[jira_id] <> "NA" &&
                ips_jira_bugs[bug_project] = "WIFI" &&
                ips_jira_bugs[jira_exposure] IN {"2-High", "3-Medium"} &&
                ips_jira_bugs[jira_created_date] <= EDATE(month_list[round_month_date], 1)  &&
                ips_jira_bugs[customer_closed_date] >= month_list[round_month_date]
            )
        ),
    "jira_bt_critical_bug_count",
        CALCULATE (
            COUNTROWS(ips_jira_bugs),
            FILTER (
                ips_jira_bugs,
                ips_jira_bugs[jira_id] <> "NA" &&
                ips_jira_bugs[bug_project] = "BT" &&
                ips_jira_bugs[jira_exposure] IN {"1-Critical"} &&
                ips_jira_bugs[jira_created_date] <= EDATE(month_list[round_month_date], 1)  &&
                ips_jira_bugs[customer_closed_date] >= month_list[round_month_date]
            )
        ),
    "jira_bt_high_med_bug_count",
        CALCULATE (
            COUNTROWS(ips_jira_bugs),
            FILTER (
                ips_jira_bugs,
                ips_jira_bugs[jira_id] <> "NA" &&
                ips_jira_bugs[bug_project] = "BT" &&
                ips_jira_bugs[jira_exposure] IN {"2-High", "3-Medium"} &&
                ips_jira_bugs[jira_created_date] <= EDATE(month_list[round_month_date], 1)  &&
                ips_jira_bugs[customer_closed_date] >= month_list[round_month_date]
            )
        ),
    "jira_tools_critical_bug_count",
        CALCULATE (
            COUNTROWS(ips_jira_bugs),
            FILTER (
                ips_jira_bugs,
                ips_jira_bugs[jira_id] <> "NA" &&
                ips_jira_bugs[bug_project] IN {"WOT", "DBGT"} &&
                ips_jira_bugs[jira_exposure] IN {"1-Critical"} &&
                ips_jira_bugs[jira_created_date] <= EDATE(month_list[round_month_date], 1)  &&
                ips_jira_bugs[customer_closed_date] >= month_list[round_month_date]
            )
        ),
    "jira_tools_high_med_bug_count",
        CALCULATE (
            COUNTROWS(ips_jira_bugs),
            FILTER (
                ips_jira_bugs,
                ips_jira_bugs[jira_id] <> "NA" &&
                ips_jira_bugs[bug_project] IN {"WOT", "DBGT"} &&
                ips_jira_bugs[jira_exposure] IN {"2-High", "3-Medium"} &&
                ips_jira_bugs[jira_created_date] <= EDATE(month_list[round_month_date], 1)  &&
                ips_jira_bugs[customer_closed_date] >= month_list[round_month_date]
            )
        ),
    "ips_wifi_bug_count",
        CALCULATE (
            COUNTROWS(ips_jira_bugs),
            FILTER (
                ips_jira_bugs,
                ips_jira_bugs[ips_case_number] > 0 &&                                           // this is a real IPS
                ips_jira_bugs[bug_project] = "WIFI" &&                                          // WIFI
                ips_jira_bugs[reporter] <> "NA" &&                                              // bug was reaslly assigned on someone
                ips_jira_bugs[ips_created_date] <= EDATE(month_list[round_month_date], 1)  &&   // craeted during or before next month (this month + 1)
                ips_jira_bugs[ips_closed_date] >= month_list[round_month_date]                  // closed during or after this month
            )
        ),
    "ips_bt_bug_count",
        CALCULATE (
            COUNTROWS(ips_jira_bugs),
            FILTER (
                ips_jira_bugs,
                ips_jira_bugs[ips_case_number] > 0 &&
                ips_jira_bugs[bug_project] = "BT" &&
                ips_jira_bugs[reporter] <> "NA" &&
                ips_jira_bugs[ips_created_date] <= EDATE(month_list[round_month_date], 1)  &&
                ips_jira_bugs[ips_closed_date] >= month_list[round_month_date]
            )
        ),
    "ips_tools_bug_count",
        CALCULATE (
            COUNTROWS(ips_jira_bugs),
            FILTER (
                ips_jira_bugs,
                ips_jira_bugs[ips_case_number] > 0 &&
                ips_jira_bugs[bug_project] IN {"WOT", "DBGT"} &&
                ips_jira_bugs[reporter] <> "NA" &&
                ips_jira_bugs[ips_created_date] <= EDATE(month_list[round_month_date], 1)  &&
                ips_jira_bugs[ips_closed_date] >= month_list[round_month_date]
            )
        )
)
---
bug_status = 
VAR _jira = LOWER ( TRIM ( COALESCE ( ips_jira_bugs[jira_status], "" ) ) )
VAR _ips  = LOWER ( TRIM ( COALESCE ( ips_jira_bugs[ips_sub_status], "" ) ) )
RETURN
SWITCH (
    TRUE(),
    -- 1) JIRA overrides everything
    _jira IN { "verify", "implemented", "open", "in progress", "pending" }, "open",

    -- 2) Otherwise use IPS sub-status
    _ips IN { "closed", "close-pending" }, "closed",
    _ips = "investigating", "open",

    -- 3) Default
    "closed"
)
---
bug_status_custom = 
VAR _jira    = LOWER ( TRIM ( COALESCE ( ips_jira_bugs[jira_status], "" ) ) )
VAR _ipsMain = LOWER ( TRIM ( COALESCE ( ips_jira_bugs[ips_status], "" ) ) )
VAR _ipsSub  = LOWER ( TRIM ( COALESCE ( ips_jira_bugs[ips_sub_status], "" ) ) )
VAR _hsd     = LOWER ( TRIM ( COALESCE ( ips_jira_bugs[hsd_status_reason], "" ) ) )

VAR _jiraIsClosed = _jira = "closed"
VAR _jiraIsOpen   = _jira IN { "open", "in progress", "pending", "verify", "implemented" }

VAR _ipsIsClosed =
    _ipsMain = "closed" ||
    _ipsSub  = "closed" ||
    _ipsSub  = "close-pending" ||
    _ipsSub  = "close pending" ||
    _ipsSub  = "close_pending"

VAR _hsdIsClosed  = _hsd IN { "closed", "complete", "implemented", "rejected" }

VAR _ipsIsOpenish =
    _ipsMain IN { "open", "investigating", "na" } ||
    _ipsSub  IN { "open", "investigating", "na" }

VAR _hsdIsOpenish = _hsd IN { "open", "investigating", "na" }

RETURN
SWITCH (
    TRUE(),
    _jiraIsClosed, "closed",
    _ipsIsClosed || _hsdIsClosed, "closed",
    _jiraIsOpen, "open",
    _ipsIsOpenish || _hsdIsOpenish, "open",
    "closed"
)
---
CFE Team =
VAR _reporter = UPPER ( TRIM ( COALESCE ( ips_jira_bugs[reporter], "" ) ) )
VAR _assignee = UPPER ( TRIM ( COALESCE ( ips_jira_bugs[jira_assignee], "" ) ) )
VAR _wifiCfe =
    _reporter IN {
        UPPER ( "Brenton Wu" ), UPPER ( "Jonathan Tsao" ), UPPER ( "KJ Fang" ), UPPER ( "Zhiwei He" ), UPPER ( "Frank Lee" ), UPPER ( "Frank Yang" ),
        UPPER ( "Nicky Chen" ), UPPER ( "Charles Chu" ), UPPER ( "Zhiqiang Cai" ), UPPER ( "Timdaway Lai" ), UPPER ( "Zhanying Gao" ),
        UPPER ( "Jackx Lee" ), UPPER ( "Lydiax Chien" ), UPPER ( "Johnsonx Su" ), UPPER ( "Xihaox Yang" ), UPPER ( "Henryx Su" )
    }
    || _assignee IN {
        UPPER ( "Brenton Wu" ), UPPER ( "Jonathan Tsao" ), UPPER ( "KJ Fang" ), UPPER ( "Zhiwei He" ), UPPER ( "Frank Lee" ), UPPER ( "Frank Yang" ),
        UPPER ( "Nicky Chen" ), UPPER ( "Charles Chu" ), UPPER ( "Zhiqiang Cai" ), UPPER ( "Timdaway Lai" ), UPPER ( "Zhanying Gao" ),
        UPPER ( "Jackx Lee" ), UPPER ( "Lydiax Chien" ), UPPER ( "Johnsonx Su" ), UPPER ( "Xihaox Yang" ), UPPER ( "Henryx Su" )
    }
VAR _cieCfe =
    _reporter IN { UPPER ( "Sam Hsu" ) }
    || _assignee IN { UPPER ( "Sam Hsu" ) }
VAR _btCfe =
    _reporter IN {
        UPPER ( "Bingyue Sun" ), UPPER ( "Bing Chang" ), UPPER ( "Leaweix Chen" ), UPPER ( "Leo Chiang" ), UPPER ( "Steven1 Chen" ),
        UPPER ( "Wesley Kuo" ), UPPER ( "Tonyx Yeh" ), UPPER ( "Juan Zou" ), UPPER ( "Matt Chen" ), UPPER ( "Yu-wei Chen" )
    }
    || _assignee IN {
        UPPER ( "Bingyue Sun" ), UPPER ( "Bing Chang" ), UPPER ( "Leaweix Chen" ), UPPER ( "Leo Chiang" ), UPPER ( "Steven1 Chen" ),
        UPPER ( "Wesley Kuo" ), UPPER ( "Tonyx Yeh" ), UPPER ( "Juan Zou" ), UPPER ( "Matt Chen" ), UPPER ( "Yu-wei Chen" )
    }
RETURN
SWITCH (
    TRUE(),
    _wifiCfe, "WiFi CFE",
    _cieCfe, "CIE CFE",
    _btCfe, "BT CFE",
    "Other"
)
---
Customer_custom = 
SWITCH (
    TRUE(),
    AND ( ips_jira_bugs[customer] = "LENOVO", ips_jira_bugs[reporter] = "Wesley Kuo" ), "LENOVO Thinkpad",
    AND ( ips_jira_bugs[customer] = "LENOVO", ips_jira_bugs[reporter] = "Zou Juan" ), "LENOVO Ideapad",
    AND ( ips_jira_bugs[customer] = "LENOVO", ips_jira_bugs[reporter] = "Timdaway Lai" ), "LENOVO Thinkpad",
    AND ( ips_jira_bugs[customer] = "LENOVO", ips_jira_bugs[reporter] = "Zhiqiang Cai" ), "LENOVO Ideapad",
    AND ( ips_jira_bugs[customer] = "NA", ips_jira_bugs[reporter] = "Ling-yuan Tu" ), "ICS",
    AND ( ips_jira_bugs[customer] = "NA", ips_jira_bugs[reporter] = "Dale Cronau" ), "Field Issue",
    ips_jira_bugs[customer]
)
---
ips_category_custom = IF(ips_jira_bugs[ips_category] in {"Debug Tools", "OEM Tools"}, "Tools", IF(ips_jira_bugs[ips_category] in {"Bluetooth (BT)"}, "Bluetooth", IF(ips_jira_bugs[ips_category] in {"WCS Innovation Engineering"}, "ICPS/Killer",IF(ips_jira_bugs[ips_category] in {"WiFi Linux","WiFi Windows"}, "WiFi",ips_jira_bugs[ips_category]))))
---
ips_created_year_month = ips_jira_bugs[ips_created_date].[Year] & "'" & FORMAT(ips_jira_bugs[ips_created_date].[MonthNo], "0#")
---
ips_created_year_quarter = ips_jira_bugs[ips_created_date].[Year] & "'Q" & ips_jira_bugs[ips_created_date].[QuarterNo]
---
ips_id_without_zero = IF(ips_jira_bugs[ips_case_number] > 0, 1, 0)
---
ips_is_bug_open = 
IF (
    ips_jira_bugs[ips_case_number] > 0
        && ISBLANK ( ips_jira_bugs[ips_closed_date] ),
    1,
    0
)
---
ips_last_modified_days = DATEDIFF(ips_jira_bugs[ips_last_modified_date] , TODAY(), DAY) 
---
ips_only_tat_day = datediff(ips_jira_bugs[ips_created_date],ips_jira_bugs[ips_close_pending_date],DAY)
---
ips_only_tat_hour = datediff(ips_jira_bugs[ips_created_date],ips_jira_bugs[ips_close_pending_date],HOUR)
---
ips_open_days = IF(ips_jira_bugs[ips_is_bug_open], DATEDIFF(ips_jira_bugs[ips_created_date], TODAY(), DAY), -1)
---
ips_priority_custom = if (ips_jira_bugs[ips_priority] = "critical", "high", ips_jira_bugs[ips_priority]) //demote all IPS Priority = Critical to High to avoid unneeded attention
---
ips_promotion_percentage = 
INT(100 *
    DIVIDE(
        SUM('ips_jira_bugs'[is_ips_promoted_to_jira]),
        COUNT('ips_jira_bugs'[ips_case_number])
    )
)
---
ips_status_close_pending = (ips_jira_bugs[ips_sub_status]="Close-Pending")
---
ips_tat_till_jira_days = ips_jira_bugs[ips_tat_till_jira_hours]/24
---
is_bug_open = ips_jira_bugs[ips_is_bug_open] || ips_jira_bugs[jira_is_bug_open]
---
is_meteor_lake = 
CONTAINSSTRING(ips_jira_bugs[jira_platform], "Meteor") || 
CONTAINSSTRING(ips_jira_bugs[ips_platform], "Meteor")
---
is_stale_ips = 
VAR _ips_status =
    UPPER ( TRIM ( COALESCE ( ips_jira_bugs[ips_status], "" ) ) )

VAR _ips_sub_status =
    LOWER ( TRIM ( COALESCE ( ips_jira_bugs[ips_sub_status], "" ) ) )

VAR _jira_status =
    LOWER ( TRIM ( COALESCE ( ips_jira_bugs[jira_status], "" ) ) )

VAR _jira_is_done =
    _jira_status IN { "closed", "verify", "implemented" }
        || _jira_status = ""
        || _jira_status = "na"

VAR _last_mod_d =
    COALESCE ( ips_jira_bugs[ips_last_modified_days], 0 )

VAR _open_d =
    COALESCE ( ips_jira_bugs[ips_open_days], 0 )

RETURN
IF (
    _ips_status <> "CLOSED"
        && _ips_sub_status <> "close-pending"
        && _jira_is_done
        && ( _last_mod_d > 21 || _open_d > 30 ),
    1,
    0
)
---
is_wifi_7 = 
CONTAINSSTRING(ips_jira_bugs[jira_nic], "Gale") || 
CONTAINSSTRING(ips_jira_bugs[jira_nic], "Misty") || 
CONTAINSSTRING(ips_jira_bugs[jira_nic], "Fillmore") || 
CONTAINSSTRING(ips_jira_bugs[ips_hardware], "Gale") || 
CONTAINSSTRING(ips_jira_bugs[ips_hardware], "Misty") || 
CONTAINSSTRING(ips_jira_bugs[ips_hardware], "Fillmore")
---
Issue Fixed Rate = 
---
Issues Closed in Period = 
VAR CurrentDates = VALUES( DateTable[Date] )
RETURN
CALCULATE (
    COUNTROWS ( 'ips_jira_bugs' ),
    ALL ( DateTable ),
    KEEPFILTERS( CurrentDates ),
    USERELATIONSHIP ( DateTable[Date], 'ips_jira_bugs'[bug_closed_date] )
)
---
Issues Created in Period = 
CALCULATE (
    COUNTROWS ( 'ips_jira_bugs' ),
    -- Explicitly activate the relationship that should be active by default
    USERELATIONSHIP ( DateTable[Date], 'ips_jira_bugs'[bug_created_date] )
)
---
jira_avg_nun_of_reporter_comments = 
DIVIDE(
	SUM('ips_jira_bugs'[num_of_comments_by_reporter]),
	COUNTA('ips_jira_bugs'[jira_id])
)
---
jira_created_year_month = ips_jira_bugs[jira_created_date].[Year] & "'" & FORMAT(ips_jira_bugs[jira_created_date].[MonthNo], "0#")
---
jira_created_year_quarter = ips_jira_bugs[jira_created_date].[Year] & "'Q" & ips_jira_bugs[jira_created_date].[QuarterNo]
---
jira_good_bug_ratio = 
    DIVIDE(
        COUNTROWS(
            FILTER(
                ips_jira_bugs,
                ips_jira_bugs[jira_state_reason] IN {"Fixed", "Product Change", "3rd party", "Workaround", "Duplicate Root Cause", "Unknown Fix"} &&
                ips_jira_bugs[jira_team] IN {"cae"} &&
                ips_jira_bugs[jira_found_by] IN {"customer found"} &&
                ips_jira_bugs[bug_status_custom] IN {"Closed"}
            )
        ),
        COUNTROWS(
            FILTER(
                ips_jira_bugs,
                ips_jira_bugs[bug_status_custom] IN {"Closed"} &&
                ips_jira_bugs[jira_team] IN {"cae"} &&
                ips_jira_bugs[jira_found_by] IN {"customer found"}
            )
        ),
        0
    )

---
jira_is_bug_open = IF(ips_jira_bugs[jira_status] IN {"In Progress", "Open", "Pending"} , 1, 0)
---
jira_open_days = IF(ips_jira_bugs[jira_is_bug_open], DATEDIFF(ips_jira_bugs[jira_created_date], TODAY(), DAY),-1)
---
jira_originated_from_ips_percentage = 
    COUNTROWS(FILTER(ips_jira_bugs, ips_jira_bugs[ips_case_number] > 0 && ips_jira_bugs[jira_is_sw_change])) /  // bugs having IPS
    COUNTROWS(FILTER(ips_jira_bugs, ips_jira_bugs[jira_is_sw_change]))                                          // all fixed bugs
---
jira_pending_days = ips_jira_bugs[jira_pending_hours]/24
---
jira_sighting_days = ips_jira_bugs[jira_sighting_hours]/24
---
jira_sighting_percentage = 
    COUNTROWS(FILTER(ips_jira_bugs, ips_jira_bugs[jira_sighting_hours] > 0 && ips_jira_bugs[jira_is_sw_change])) /  // sighting fixed only
    COUNTROWS(FILTER(ips_jira_bugs, ips_jira_bugs[jira_is_sw_change]))                                              // all fixed bugs
---
jira_sw_change_percentage = 
( 
    DIVIDE(
	    SUM('ips_jira_bugs'[jira_is_sw_change]),
	    COUNTA('ips_jira_bugs'[jira_id])
    )
)
---
jira_total_num_of_fixed_bugs = 0 + COUNTROWS(FILTER(ips_jira_bugs, ips_jira_bugs[jira_is_sw_change]))
---
jira_triage_rate = DIVIDE(ips_jira_bugs[num_of_promoted_ips], ips_jira_bugs[total_num_of_ips], 0)
---
num_of_not_promoted_hsd = 
COALESCE (
    CALCULATE (
        DISTINCTCOUNT ( ips_jira_bugs[hsd_id] ),
        FILTER (
            ips_jira_bugs,
            VAR h = UPPER ( TRIM ( COALESCE ( ips_jira_bugs[hsd_id], "" ) ) )
            VAR p = UPPER ( TRIM ( COALESCE ( ips_jira_bugs[hsd_promoted_id], "" ) ) )
            VAR s = LOWER ( TRIM ( COALESCE ( ips_jira_bugs[hsd_status_reason], "" ) ) )
            RETURN
                h <> ""
                && h <> "NA"
                && ( p = "" || p = "NA" )
                && NOT ( s IN { "closed", "complete", "implemented", "rejected" } )
        )
    ),
    0
)
---
num_of_not_promoted_ips = 
VAR _total = [total_num_of_ips]
VAR _prom  = [num_of_promoted_ips]
RETURN COALESCE( MAX( _total - _prom, 0 ), 0 )
---
num_of_promoted_hsd = 
VAR Result =
    COUNTROWS (
        FILTER (
            ips_jira_bugs,
            VAR hsd = TRIM ( ips_jira_bugs[hsd_promoted_id] )
            RETURN
                hsd <> "NA"
                && hsd <> ""
                && NOT ISBLANK ( hsd )
        )
    )
RETURN COALESCE ( Result, 0 )
---
num_of_promoted_ips = 
COALESCE(
    CALCULATE(
        DISTINCTCOUNT(ips_jira_bugs[ips_case_number]),
        KEEPFILTERS(ips_jira_bugs[ips_case_number] > 0),
        KEEPFILTERS( UPPER(TRIM(ips_jira_bugs[ips_jira_promo_status])) IN { "PROMOTED", "DONE" } )
        -- We include closed by not filtering sub_status here
    ),
    0
)
---
platform = IF(ips_jira_bugs[jira_platform] <> "NA", ips_jira_bugs[jira_platform], ips_jira_bugs[ips_platform])
---
RPL-P PV = "2022'12"
---
RPL-S PV = "2022'09"
---
RPL-S Refresh PV = "2023'09"
---
sankey_hook_ips_next = IF(ips_jira_bugs[is_ips_promoted_to_jira], "JIRA", "Reject")
---
sankey_hook_jira = IF(ips_jira_bugs[jira_id] <> "NA", "JIRA", "IPS_ONLY")
---
sankey_hook_jira_next = IF(ips_jira_bugs[jira_is_sw_change], "Fixed", "Reject")
---
sankey_hook_project = IF(ips_jira_bugs[bug_project] in {"DBGT", "WOT", "NA"}, "Tools", ips_jira_bugs[bug_project])
---
Sighting_Open_Rate = DIVIDE([Bug_num_of_open_sighting],[total_num_of_sighting])
---
total_AVG_tat_days_CFE = 
    INT(
        DIVIDE(
            (
                AVERAGE(ips_jira_bugs[ips_tat_till_jira_hours]) + 
                AVERAGE(ips_jira_bugs[jira_sighting_hours]) + 
                AVERAGE(ips_jira_bugs[jira_pending_hours])
            ), 24
        )
    )
---
total_current_issue_count = 
VAR JiraIssues =
    [total_num_of_jira]

VAR NotPromotedIPS =
    [num_of_not_promoted_ips]

VAR StaleIPS =
    [total_num_of_stale_ips]

VAR ClosePendingIPS =
    [total_num_of_close_pending_ips]

VAR HSD =
    [num_of_not_promoted_hsd]

VAR StaleHSD =
    [total_num_of_stale_hsd]

RETURN
COALESCE (
    JiraIssues
        + NotPromotedIPS
        - StaleIPS
        - ClosePendingIPS
        + HSD
        - StaleHSD,
    0
)
---
total_issue_count = 
[total_num_of_jira]
+ [num_of_not_promoted_ips] + [num_of_not_promoted_hsd]
---
total_num_of_close_pending_ips = 
COALESCE (
    CALCULATE (
        DISTINCTCOUNT ( ips_jira_bugs[ips_case_number] ),
        ips_jira_bugs[ips_case_number] > 0,
        LOWER ( TRIM ( ips_jira_bugs[ips_sub_status] ) ) = "close-pending",
        UPPER ( TRIM ( ips_jira_bugs[ips_jira_promo_status] ) ) = "NOT YET PROMOTED"
    ),
    0
)
---
total_num_of_hsd = 
VAR Result =
    CALCULATE(
        DISTINCTCOUNT ( ips_jira_bugs[hsd_id] ),
        KEEPFILTERS(
            FILTER(
                ips_jira_bugs,
                NOT ISBLANK ( ips_jira_bugs[hsd_id] ) &&
                TRIM ( ips_jira_bugs[hsd_id] ) <> "" &&
                ips_jira_bugs[hsd_id] <> "NA"
            )
        )
    )
RETURN COALESCE ( Result, 0 )
---
total_num_of_ips = 
COALESCE(
    CALCULATE(
        DISTINCTCOUNT(ips_jira_bugs[ips_case_number]),
        KEEPFILTERS(ips_jira_bugs[ips_case_number] > 0)
        -- Do NOT exclude "closed" here; closed = work done and must be counted
    ),
    0
)
---
total_num_of_ips_where_Jira_already_closed = COUNTROWS(
    FILTER(
        ips_jira_bugs,
        (ips_jira_bugs[jira_status] = "closed" || ips_jira_bugs[jira_status] = "implemented") &&
        ips_jira_bugs[ips_sub_status] <> "pending close"
    )
) //count IPS where JIRA is already closed or implemented
---
total_num_of_jira_all_status = 
// Count all Jira keys regardless of IPS/Jira status.
// Use this as the base measure, then slice by date/status in visuals.
VAR _keys =
    SELECTCOLUMNS (
        ips_jira_bugs,
        "jira_key",
            VAR _jira_id = TRIM ( COALESCE ( ips_jira_bugs[jira_id], "" ) )
            VAR _ips_jira_id = TRIM ( COALESCE ( ips_jira_bugs[ips_jira_id], "" ) )
            RETURN
                IF (
                    _jira_id <> "" && UPPER ( _jira_id ) <> "NA",
                    _jira_id,
                    IF (
                        _ips_jira_id <> "" && UPPER ( _ips_jira_id ) <> "NA",
                        _ips_jira_id,
                        BLANK ()
                    )
                )
    )
VAR _valid =
    FILTER ( _keys, NOT ISBLANK ( [jira_key] ) )
RETURN
COALESCE ( COUNTROWS ( DISTINCT ( _valid ) ), 0 )
---
total_num_of_jira_active_only = 
// Active-only Jira count: excludes rows where IPS is closed / close-pending / pending-closed.
// Keep this measure when you want old offload-style logic.
VAR _keys =
    SELECTCOLUMNS (
        FILTER (
            ips_jira_bugs,
            NOT (
                LOWER ( TRIM ( COALESCE ( ips_jira_bugs[ips_sub_status], "" ) ) )
                    IN { "close-pending", "pending-closed" }
            )
            && LOWER ( TRIM ( COALESCE ( ips_jira_bugs[ips_status], "" ) ) ) <> "closed"
        ),
        "jira_key",
            VAR _jira_id = TRIM ( COALESCE ( ips_jira_bugs[jira_id], "" ) )
            VAR _ips_jira_id = TRIM ( COALESCE ( ips_jira_bugs[ips_jira_id], "" ) )
            RETURN
                IF (
                    _jira_id <> "" && UPPER ( _jira_id ) <> "NA",
                    _jira_id,
                    IF (
                        _ips_jira_id <> "" && UPPER ( _ips_jira_id ) <> "NA",
                        _ips_jira_id,
                        BLANK ()
                    )
                )
    )
VAR _valid =
    FILTER ( _keys, NOT ISBLANK ( [jira_key] ) )
RETURN
COALESCE ( COUNTROWS ( DISTINCT ( _valid ) ), 0 )
---
total_num_of_jira = [total_num_of_jira_all_status]
---
Total_num_of_open_ips = 
CALCULATE(
    DISTINCTCOUNT('ips_jira_bugs'[ips_case_number]),
    KEEPFILTERS('ips_jira_bugs'[ips_status] = "Open"),
    KEEPFILTERS('ips_jira_bugs'[ips_case_number] > 0)
)
---
total_num_of_sighting = [total_num_of_ips] + [total_num_of_jira] - [num_of_promoted_ips]//total number of issue ips+jira
---
total_num_of_stale_hsd = 
COALESCE (
    CALCULATE (
        [num_of_not_promoted_hsd],
        FILTER (
            ips_jira_bugs,
            DATEDIFF ( ips_jira_bugs[hsd_submitted_date], TODAY (), DAY ) > 30
        )
    ),
    0
)
---
total_num_of_stale_ips = 
COALESCE (
    CALCULATE (
        [num_of_not_promoted_ips],
        (
            ips_jira_bugs[ips_last_modified_days] > 21
                || ips_jira_bugs[ips_open_days] > 30
        ),
        LOWER ( TRIM ( ips_jira_bugs[ips_sub_status] ) ) <> "close-pending"
    ),
    0
)
---
total_num_of_unpromoted_hsd = 
COALESCE (
    CALCULATE (
        DISTINCTCOUNT ( ips_jira_bugs[hsd_id] ),

        -- HSD rows only
        FILTER (
            ips_jira_bugs,
            VAR h = UPPER ( TRIM ( COALESCE ( ips_jira_bugs[hsd_id], "" ) ) )
            RETURN h <> "" && h <> "NA"
        ),

        -- Unpromoted: hsd_promoted_id blank or NA
        FILTER (
            ips_jira_bugs,
            VAR p = UPPER ( TRIM ( COALESCE ( ips_jira_bugs[hsd_promoted_id], "" ) ) )
            RETURN p = "" || p = "NA"
        ),

        -- Exclude HSD statuses already treated as closed by bug_status_custom
        FILTER (
            ips_jira_bugs,
            VAR s = LOWER ( TRIM ( COALESCE ( ips_jira_bugs[hsd_status_reason], "" ) ) )
            RETURN NOT ( s IN { "closed", "complete", "implemented", "rejected" } )
        )
    ),
    0
)
---
total_open_bugs = COUNTROWS(FILTER(ips_jira_bugs, ips_jira_bugs[is_bug_open])) + 0
---
total_open_meteor_lake_bugs = COUNTROWS(FILTER(ips_jira_bugs, ips_jira_bugs[is_bug_open] && ips_jira_bugs[is_meteor_lake])) + 0
---
total_open_wifi_7_bugs = COUNTROWS(FILTER(ips_jira_bugs, ips_jira_bugs[is_bug_open] && ips_jira_bugs[is_wifi_7])) + 0
---
total_tat_days = 
    INT(
        DIVIDE(
            (
                AVERAGE(ips_jira_bugs[ips_tat_till_jira_hours]) + 
                AVERAGE(ips_jira_bugs[jira_sighting_hours]) + 
                AVERAGE(ips_jira_bugs[jira_open_hours]) + 
                AVERAGE(ips_jira_bugs[jira_in_progress_hours]) + 
                AVERAGE(ips_jira_bugs[jira_pending_hours])
            ), 24
        )
    )
---

## Known Discrepancies / Issues
<!-- Format: [YYYY-MM-DD] Description — Root cause — Resolution -->

- [2026-05-10] bat shows different Total than Power BI for some reporters — Root cause: bat output was from an older run; data changed between runs (num_jira increased by 1 for Frank Lee). Both Python and Power BI showed 9 when queried at same time. Not a bug.
- [2026-07-18] total_num_of_jira changed to status-agnostic counting: include all Jira keys (jira_id fallback ips_jira_id), excluding only blank/NA keys. Status segmentation is now expected to be done via report filters/slicers (date/status/sub-status).
- [2026-05-18] HSD rejected-count fix: `num_of_not_promoted_hsd` and `total_num_of_unpromoted_hsd` now explicitly exclude `hsd_status_reason` in {closed, complete, implemented, rejected}. This aligns current/open HSD counts with `bug_status_custom`, which already treats `rejected` as closed.

