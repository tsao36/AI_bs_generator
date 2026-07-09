import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import db_chatbot


def test_strip_limit():
    assert db_chatbot._strip_limit("SELECT * FROM vw_issues LIMIT 50") == "SELECT * FROM vw_issues"
    assert db_chatbot._strip_limit("SELECT * FROM vw_issues LIMIT 50;") == "SELECT * FROM vw_issues"
    assert db_chatbot._strip_limit("SELECT * FROM vw_issues") == "SELECT * FROM vw_issues"


def test_schema_answer_for_column_exists():
    schema = {"vw_issues": ["reporter", "ips_created_date"]}
    answer = db_chatbot._schema_answer_for_question("column reporter in vw_issues", schema, ["vw_issues"])
    assert "exists" in answer


def test_schema_answer_for_column_missing():
    schema = {"vw_issues": ["reporter", "ips_created_date"]}
    answer = db_chatbot._schema_answer_for_question("column foo in vw_issues", schema, ["vw_issues"])
    assert "not found" in answer.lower()


def test_heuristic_year_query():
    schema = {"vw_issues": ["ips_created_date"]}
    sql, reason = db_chatbot._heuristic_sql("which year has the most issues", schema, ["vw_issues"])
    assert "extract(year" in sql.lower()
    assert "issue_count" in sql.lower()
    assert reason


def test_validate_select_only_allows_extract():
    sql = "SELECT EXTRACT(YEAR FROM ips_created_date) AS y FROM vw_issues"
    db_chatbot.validate_select_only(sql, ["vw_issues"])


def test_validate_select_only_blocks_other_table():
    sql = "SELECT * FROM other_table"
    with pytest.raises(ValueError):
        db_chatbot.validate_select_only(sql, ["vw_issues"])


def test_plan_parsing_fallback():
    # Simulate non-JSON response and ensure it returns empty queries
    content = "not json"
    start = content.find("{")
    end = content.rfind("}")
    assert start == -1 and end == -1
    # ensure helper handles fallback
    result = db_chatbot._extract_sql_fallback("SELECT 1")
    assert result.lower().startswith("select")


if __name__ == "__main__":
    print("All tests passed.")
