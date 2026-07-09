import os
import tempfile
import unittest
from unittest import mock
import sys
from pathlib import Path

# Ensure project root is importable when executing the test module directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pandas as pd  # noqa: F401
except ImportError:  # pragma: no cover
    pd = None

from Wireless_bug_dashboard import (
    DB_NA,
    HsdBugData,
    IpsBugData,
    JiraBugData,
    generate_merged_bug_list,
    load_hsd_bugs_from_csv,
)


@unittest.skipIf(pd is None, "pandas is required for HSD CSV loading tests")
class TestHsdIntegration(unittest.TestCase):
    def _write_temp_csv(self, rows, headers):
        temp = tempfile.NamedTemporaryFile(mode="w", delete=False, newline="", suffix=".csv")
        try:
            temp.write(",".join(headers) + "\n"
            )
            for row in rows:
                temp.write(",".join(row) + "\n")
            temp.flush()
        finally:
            temp.close()
        self.addCleanup(lambda: os.remove(temp.name))
        return temp.name

    def test_load_hsd_bugs_from_csv_maps_fields(self):
        headers = [
            "id",
            "promoted bug",
            "status reason",
            "customer",
            "assigned_to",
            "subject",
            "submit_date",
            "modified",
            "platform",
        ]
        rows = [[
            "HSN-123",
            "BUG-1",
            "Investigating",
            "Dell",
            "john.doe",
            "Connectivity issue",
            "2025-12-01T10:15:00",
            "2025-12-02T08:30:00",
            "Panther Lake Platform",
        ]]
        csv_path = self._write_temp_csv(rows, headers)

        bugs = load_hsd_bugs_from_csv(csv_path)

        self.assertEqual(len(bugs), 1)
        bug = bugs[0]
        self.assertEqual(bug.hsd_id, "HSN-123")
        self.assertEqual(bug.hsd_promoted_id, "BUG-1")
        self.assertEqual(bug.hsd_status_reason, "Investigating")
        self.assertEqual(bug.hsd_customer_detail, "Dell")
        self.assertEqual(bug.hsd_owner, "john.doe")
        self.assertEqual(bug.hsd_title, "Connectivity issue")
        self.assertEqual(bug.hsd_platform, "Panther Lake")
        self.assertNotEqual(bug.hsd_submitted_date, DB_NA)
        self.assertNotEqual(bug.hsd_updated_date, DB_NA)

    def test_generate_merged_bug_list_attaches_hsd(self):
        ips_data = [
            IpsBugData(
                ips_case_number=1001,
                ips_jira_id="BUG-1",
                ips_owner_email=DB_NA,
            )
        ]

        jira_data = [
            JiraBugData(
                jira_id="BUG-1",
                jira_reporter_email="alice.smith@intel.com",
            )
        ]

        hsd_data = [
            HsdBugData(
                hsd_id="HSN-999",
                hsd_promoted_id="BUG-1",
                hsd_owner="hsd.owner",
                hsd_title="Connectivity issue",
            )
        ]

        class DummyJiraClient:
            def get_specific_bug(self, bug_id):  # pragma: no cover - not hit in this test
                return DB_NA

        with mock.patch("Wireless_bug_dashboard.DbConnector") as mock_db_connector:
            mock_db_connector.return_value.get_customers_data.return_value = []
            merged = generate_merged_bug_list(ips_data, jira_data, DummyJiraClient(), hsd_data)

        self.assertEqual(len(merged), 1)
        merged_bug = merged[0]
        self.assertEqual(merged_bug.jira_data.jira_id, "BUG-1")
        self.assertEqual(merged_bug.hsd_data.hsd_promoted_id, "BUG-1")
        self.assertEqual(merged_bug.hsd_data.hsd_owner, "hsd.owner")
        self.assertEqual(merged_bug.reporter, "Alice Smith")

    def test_generate_merged_bug_list_uses_hsd_owner_as_reporter(self):
        ips_data = [IpsBugData()]
        jira_data = [JiraBugData()]
        hsd_data = [HsdBugData(hsd_id="HSN-100", hsd_owner="yaochien")]

        class DummyJiraClient:
            def get_specific_bug(self, bug_id):  # pragma: no cover - not hit in this test
                return DB_NA

        with mock.patch("Wireless_bug_dashboard.DbConnector") as mock_db_connector:
            mock_db_connector.return_value.get_customers_data.return_value = []
            merged = generate_merged_bug_list(ips_data, jira_data, DummyJiraClient(), hsd_data)

        matched = next((bug for bug in merged if bug.hsd_data.hsd_id == "HSN-100"), None)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.reporter, "Leo Chiang")


if __name__ == "__main__":
    unittest.main()
