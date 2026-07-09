""" Test the DatabaseAPI library """

from datetime import datetime
from uuid import uuid4
import pytest
import pyodbc
from pytest_mock import MockerFixture
from Sherlock import DatabaseSandbox as sbdb
from DatabaseAPI import DBConnector, DEFAULT_DRIVER, DATE_FORMAT
from DatabaseAPI.utils import argumentize_query
from DatabaseAPI.query_lib._definitions import ProgramNotFoundError, NoPreviousVersion

MOCK_SERVER = "foo_server"
MOCK_DB_NAME = "foo_db_name"
MOCK_USERNAME = "foo_username"
MOCK_PASSWORD = "foo_password"
MOCK_TABLE = "foo_table"
MOCK_DICT = {"FOO": "BAR"}

REAL_TABLE = "meta.pytest_target"


@pytest.fixture(name="cursor")
def fixture_cursor(mocker: MockerFixture):
    """Mocks the cursor object used by the db fixture"""
    cursor = mocker.MagicMock()

    def apply_results(results: dict):
        """Allows for easily setting a single result to the mocked cursor object"""
        mocker.patch.object(cursor, "fetchone", return_value=results.values())
        cursor.description = tuple((x, y) for x, y in results.items())

    cursor.apply_results = apply_results
    return cursor


@pytest.fixture(name="mock_db")
def fixture_mock_db_connector(mocker, cursor):
    """
    A mock DBConnector instance for usage in tests.
    Fully functional, but fake.
    Meant for testing things in the internal logic of the DB connection like the cursor and not necessarily results.
    """
    mock_connection = mocker.MagicMock()
    with mock_connection as connection:
        connection.cursor.return_value = cursor

    mocker.patch("pyodbc.connect", return_value=mock_connection)
    return DBConnector(MOCK_SERVER, MOCK_DB_NAME, MOCK_USERNAME, MOCK_PASSWORD)


@pytest.fixture(name="real_db")
def fixture_real_db_connector():
    """
    A REAL DBConnector instance for usage in tests.
    Uses the Sandbox just in case.
    Meant to test things like full flows, including results.
    Best used againt the table [meta.pytest_target]
    """
    return DBConnector(sbdb.server, sbdb.database, sbdb.username, sbdb.password)


@pytest.fixture(name="target_table")
def fixture_target_table_dict(real_db: DBConnector):
    """Returns the real table as it appears in the database"""
    return real_db.query_table(REAL_TABLE)


def test_argumentizer(cursor, mock_db):
    """Test that the argumentizer works as expected"""

    # Test that a query that doesn't require replacement stays as is
    query = f"FROM [{MOCK_TABLE}] SELECT * WHERE foo = 0"
    assert argumentize_query(query) == (query, [])

    # Test simple replacement
    query = f"FROM [{MOCK_TABLE}] SELECT * WHERE foo = 'bar'"
    expected_result = (f"FROM [{MOCK_TABLE}] SELECT * WHERE foo = ?", ["bar"])
    assert argumentize_query(query) == expected_result

    # Test multiple replacements
    query = f"FROM [{MOCK_TABLE}] SELECT * WHERE foo = 'bar' AND lorem = 'ipsum'"
    expected_result = (f"FROM [{MOCK_TABLE}] SELECT * WHERE foo = ? AND lorem = ?", ["bar", "ipsum"])
    assert argumentize_query(query) == expected_result

    # Test invididual methods
    mock_key = "foo_key"
    mock_value = "foo_value"
    mock_where = f"[{mock_key}]='{mock_value}'"
    mock_query = f"SELECT * FROM [{MOCK_TABLE}] WHERE {mock_where}"
    argumentized = argumentize_query(mock_query)

    mock_db.query_table(MOCK_TABLE, where=mock_where)
    assert cursor.execute.call_args[0] == (argumentized[0], *argumentized[1])

    mock_db.get_single_row(MOCK_TABLE, mock_key, mock_value)
    assert cursor.execute.call_args[0] == (argumentized[0], *argumentized[1])

    mock_db.simple_query(mock_query)
    assert cursor.execute.call_args[0] == (argumentized[0], *argumentized[1])

    mock_query = f"SELECT COUNT(*) FROM [{MOCK_TABLE}] WHERE {mock_where}"
    mock_db.get_row_count(MOCK_TABLE, mock_where)
    argumentized = argumentize_query(mock_query)
    assert cursor.execute.call_args[0] == (argumentized[0], *argumentized[1])

    mock_query = f"SELECT {mock_key} FROM [{MOCK_TABLE}] WHERE {mock_where}"
    mock_db.get_column_values(MOCK_TABLE, mock_key, where=mock_where)
    argumentized = argumentize_query(mock_query)
    assert cursor.execute.call_args[0] == (argumentized[0], *argumentized[1])


def test_driver_override():
    """Test that overriding the driver works"""
    connector = DBConnector(MOCK_SERVER, MOCK_DB_NAME, MOCK_USERNAME, MOCK_PASSWORD, "{foo_driver}")
    assert "DRIVER={foo_driver}" in connector.connection_string


def test_default_connection_string():
    """Test that the default connection string is correct"""
    connector = DBConnector(MOCK_SERVER, MOCK_DB_NAME, MOCK_USERNAME, MOCK_PASSWORD)
    assert f"DRIVER={DEFAULT_DRIVER}" in connector.connection_string


def test_query_table(real_db: DBConnector, target_table):
    """Validate the query_table function"""

    # Default
    assert real_db.query_table(REAL_TABLE) == target_table

    # Select id
    table_id = [{"id": x["id"]} for x in target_table]
    assert real_db.query_table(REAL_TABLE, select="id") == table_id

    # Where
    assert real_db.query_table(REAL_TABLE, where="id='Lorem'") == [target_table[1]]

    # Sorted
    reversed_table = target_table.copy()
    reversed_table.reverse()
    assert real_db.query_table(REAL_TABLE, order_by="id") == reversed_table


def test_get_single_row(real_db: DBConnector, target_table):
    """Test that querying for a single row works as expected"""

    # Test getting without filtering
    results = real_db.get_single_row(REAL_TABLE)
    assert results == target_table[0]

    # Test getting without filtering but with reverse order
    result = real_db.get_single_row(REAL_TABLE, order_by="id", order_dir="DESC")
    assert result == target_table[1]

    # Test getting with key/id
    results = real_db.get_single_row(REAL_TABLE, "id", "Foo")
    assert results == target_table[0]

    # Test getting with "WHERE"
    results = real_db.get_single_row(REAL_TABLE, where="some_string like '%psum%'")
    assert results == target_table[1]

    # Test bad filter combos
    with pytest.raises(ValueError):
        real_db.get_single_row(REAL_TABLE, "id")

    with pytest.raises(ValueError):
        real_db.get_single_row(REAL_TABLE, None, "Foo")

    # Test no results return an empty dict
    results = real_db.get_single_row(REAL_TABLE, "id", "No Such Value")
    assert results == {}


def test_simple_query(real_db: DBConnector, target_table):
    """Test that the simple query function works as expected"""

    query = f"SELECT * FROM [{REAL_TABLE}] WHERE id = 'Lorem'"

    # Regaulr Results
    assert real_db.simple_query(query) == [list(target_table[1].values())]

    # Regular Results as list (explicit)
    assert real_db.simple_query(query, return_type=list) == [list(target_table[1].values())]

    # Regular results as dict
    assert real_db.simple_query(query, return_type=dict) == [target_table[1]]

    # Unsupported Type (but is a type)
    with pytest.raises(ValueError):
        real_db.simple_query(query, return_type=str)

    # Not a type
    with pytest.raises(TypeError):
        real_db.simple_query(query, "foo")
    with pytest.raises(TypeError):
        real_db.simple_query(query, 5)
    with pytest.raises(TypeError):
        real_db.simple_query(query, None)

    # No Results
    assert real_db.simple_query(f"SELECT * FROM [{REAL_TABLE}] WHERE id = 'Mock Value'", return_type=dict) == []


def test_column_names(real_db: DBConnector, target_table):
    """Test that column names are received as expected.
    Doesn't validate content.
    Consider adding a table with the purpose of serving as a pytest target"""
    names = real_db.get_column_names(REAL_TABLE)
    assert isinstance(names, list)
    assert names == list(target_table[0].keys())


def test_get_row_count(real_db: DBConnector):
    """Test that row count works as expected"""

    # Regular
    assert real_db.get_row_count(REAL_TABLE) == 2

    # Where
    assert real_db.get_row_count(REAL_TABLE, where="id='Foo'") == 1
    assert real_db.get_row_count(REAL_TABLE, where="id='No Such Result'") == 0


def test_get_column_values(real_db: DBConnector, target_table):
    """ " Test that column value retrieval works"""
    for name in real_db.get_column_names(REAL_TABLE):
        assert real_db.get_column_values(REAL_TABLE, name) == [x[name] for x in target_table]


def test_get_primary_key(real_db: DBConnector):
    """Tests the functionality of the Get Primary Key function"""
    assert real_db.get_primary_key(REAL_TABLE) == "id"


def test_insert_and_delete_real(real_db: DBConnector):
    """Tests inserting value to the database works"""
    real_new_row = {
        "id": str(uuid4()),
        "some_string": "foo",
        "some_bit": 1,
        "some_date": datetime.now().replace(microsecond=0),
    }
    real_db.insert_to_table(REAL_TABLE, real_new_row)
    found_row = real_db.get_single_row(REAL_TABLE, "id", real_new_row["id"])
    assert found_row == real_new_row

    real_db.delete_row(REAL_TABLE, "id", real_new_row["id"])
    found_row = real_db.get_single_row(REAL_TABLE, "id", real_new_row["id"])
    assert found_row == {}


def test_update_table_real(real_db: DBConnector):
    """Test that updating a table works"""
    now = datetime.now().replace(microsecond=0)  # DB doesn't save microseconds
    initial = {"id": "Foo", "some_string": "Bar", "some_bit": 1, "some_date": now.replace(year=now.year - 4)}

    # Set original
    real_db.update_table(REAL_TABLE, "Foo", initial)

    # Save original
    pre_update = real_db.get_single_row(REAL_TABLE, "id", "Foo")

    updated_values = {"some_string": "New Bar", "some_bit": 0, "some_date": now}

    # Update
    real_db.update_table(REAL_TABLE, "Foo", updated_values)
    expected_value = {**pre_update, **updated_values}

    # Validate that the values changed
    assert real_db.get_single_row(REAL_TABLE, "id", "foo") == expected_value


def test_update_table_fake(mock_db: DBConnector):
    """Test update_table functions that don't require a real DB, mostly validation tests"""
    # Test type validation
    mock_db.update_table(REAL_TABLE, "FOO", {"FOO": "Bar"})
    mock_db.update_table(REAL_TABLE, 5, {"FOO": "Bar"})
    mock_db.update_table(REAL_TABLE, ("FOO", "BAR"), {"FOO": "Bar"})
    with pytest.raises(TypeError):
        mock_db.update_table(MOCK_TABLE, datetime.now(), MOCK_DICT)

    # Test either WHERE or key supplied
    with pytest.raises(ValueError):
        mock_db.update_table(REAL_TABLE, None, MOCK_DICT)

    # Test update_dict is not empty
    with pytest.raises(ValueError):
        mock_db.update_table(REAL_TABLE, "Foo", {}, fail_if_no_effect=True)
    mock_db.update_table(REAL_TABLE, "Foo", {}, fail_if_no_effect=False)


def test_get_driver_build(real_db: DBConnector):
    """Test that querying for a driver build works"""
    drv_build = real_db.builds.add_reports(real_db.get_single_row("driverBuild"))
    drv_build["table"] = "driverBuild"
    build_id = drv_build["build_id"]
    build = real_db.builds.get_driver_build(build_id)
    assert build == drv_build


def test_smart_execute(real_db: DBConnector, cursor: MockerFixture, mocker: MockerFixture):
    """
    Tests that the Smart Execute function works as expected
    To test the amound of retries, we see how many times we called "get_connection" in each iteration, since the cursor
    object keeps changing.
    """
    row = real_db.get_single_row("msiBuild")

    mocker.patch("time.sleep")  # Make sure we don't wait
    get_connection_mocker = mocker.patch.object(real_db, "get_connection", return_value=cursor.connection())

    # Test happy path - no issue, called only once
    cursor = real_db.smart_execute(cursor, f"SELECT * FROM msiBuild WHERE build_id = '{row['build_id']}'")
    assert get_connection_mocker.call_count == 0
    get_connection_mocker.reset_mock()

    # Test connection error and success on retry
    cursor.execute.side_effect = [pyodbc.Error("08S01"), row]
    cursor = real_db.smart_execute(cursor, f"SELECT * FROM msiBuild WHERE build_id = '{row['build_id']}'")
    assert get_connection_mocker.call_count == 1
    get_connection_mocker.reset_mock()

    # Test connection error - make sure it ends up throwing the exception after N retries
    cursor.execute.side_effect = pyodbc.Error("08S01")
    with pytest.raises(pyodbc.Error):
        real_db.smart_execute(cursor, f"SELECT * FROM msiBuild WHERE build_id = '{row['build_id']}'")
    assert get_connection_mocker.call_count == (real_db.max_retry - 1)  # Last attempt won't call it


def test_get_any_build(real_db: DBConnector):
    """Test that querying for a a non-specific build in the generic query works"""
    msi_build = real_db.builds.add_reports(real_db.get_single_row("msiBuild"))
    msi_build["attestation_layout"] = None
    msi_build["table"] = "msiBuild"
    build_id = msi_build["build_id"]
    build = real_db.builds.get_build(build_id)
    assert build == msi_build

    wapi_build = real_db.builds.add_reports(real_db.get_single_row("wapiBuild"))
    msi_build["attestation_layout"] = None
    wapi_build["table"] = "wapiBuild"
    build_id = wapi_build["build_id"]
    build = real_db.builds.get_build(build_id)
    assert build == wapi_build

    usc_build = real_db.builds.add_reports(real_db.get_single_row("uscBuild"))
    msi_build["attestation_layout"] = None
    usc_build["table"] = "uscBuild"
    build_id = usc_build["build_id"]
    build = real_db.builds.get_build(build_id)
    assert build == usc_build

    driver_build = real_db.builds.add_reports(real_db.get_single_row("driverBuild"))
    msi_build["attestation_layout"] = None
    driver_build["table"] = "driverBuild"
    build_id = driver_build["build_id"]
    build = real_db.builds.get_build(build_id)
    assert build == driver_build


def test_date_range(cursor, mock_db: DBConnector):
    """Tests that the date range function handles arguments correctly"""

    # Assert that passing neither start nor end gives an exception
    with pytest.raises(AttributeError):
        mock_db.builds.get_builds_in_date_range(None, None)

    now = datetime.now()
    from_date = datetime.now().replace(year=now.year - 8)
    to_date = datetime.now().replace(year=now.year - 4)

    from_date_string = from_date.strftime(DATE_FORMAT)
    to_date_string = to_date.strftime(DATE_FORMAT)

    # Test only start date
    mock_db.builds.get_builds_in_date_range(start_date=from_date)
    command, query_start_date, _, _ = cursor.execute.call_args[0]
    assert query_start_date == from_date_string
    assert "WHERE submission_date >=" in command
    assert "AND submission_date" not in command

    # Test only end date
    mock_db.builds.get_builds_in_date_range(end_date=to_date)
    command, query_end_date, _, _ = cursor.execute.call_args[0]
    assert query_end_date == to_date_string
    assert "WHERE submission_date <=" in command
    assert "AND submission_date" not in command

    # Test both
    mock_db.builds.get_builds_in_date_range(start_date=from_date, end_date=to_date)
    command, query_start_date, query_end_date, _, _ = cursor.execute.call_args[0]
    assert query_start_date == from_date_string
    assert query_end_date == to_date_string
    assert "WHERE submission_date >=" in command
    assert "AND submission_date <=" in command


def test_eng_to_msi(mock_db: DBConnector, mocker: MockerFixture):
    """Test the ENG to MSI query builder"""
    mock_eng = "SBHWFW12345_99.88.77.66"
    mock_reports = [
        {"report_url": f"https://foobar/query?TIC={mock_eng}", "internal_id": "TIC1"},
        {"report_url": f"https://foobar/query?TIC={mock_eng}", "internal_id": "TIC2"},
    ]
    query_table_mocker = mocker.patch.object(mock_db, "query_table", return_value=mock_reports)
    # Pattern isn't ENG, no operator
    return_value = mock_db.builds.msi_to_eng_query_builder("foo")
    assert query_table_mocker.called is False
    assert return_value == "TRUE"
    query_table_mocker.reset_mock()

    # Pattern isn't ENG, operator
    return_value = mock_db.builds.msi_to_eng_query_builder("foo", "OR")
    assert query_table_mocker.called is False
    assert return_value == ""
    query_table_mocker.reset_mock()

    # Pattern IS ENG, no operator
    return_value = mock_db.builds.msi_to_eng_query_builder(mock_eng)
    assert query_table_mocker.called is True
    assert return_value == "build_id IN ('TIC1','TIC2')"
    query_table_mocker.reset_mock()

    # Pattern IS ENG, operator supplied
    return_value = mock_db.builds.msi_to_eng_query_builder(mock_eng, "OR")
    assert query_table_mocker.called is True
    assert return_value == " OR build_id IN ('TIC1','TIC2')"


def test_get_previous_version(mock_db: DBConnector, mocker: MockerFixture):
    """Test the functionality of the Get Previous Version function"""

    # Make sure the mock programs are in descending order and always have ".0"
    mock_programs = ["99.0.84", "99.0.83", "23.10.1", "23.10.0", "23.0.3", "23.0.2", "23.0.1", "23.0.0", "22.220.5"]
    mocker.patch.object(mock_db, "get_column_values", return_value=mock_programs)

    # Happy Path
    return_value = mock_db.definitions.get_previous_version("23.0.2")
    assert return_value == "23.0.1"

    # Happy Path, major only
    return_value = mock_db.definitions.get_previous_version("23.0.2", major_only=True)
    assert return_value == "22.220.5"

    # Happy Path, major only ignored for 99 range
    return_value = mock_db.definitions.get_previous_version("99.0.84", major_only=True)
    assert return_value == "99.0.83"

    # Test bad program
    with pytest.raises(ProgramNotFoundError):
        mock_db.definitions.get_previous_version("19.13.12")

    with pytest.raises(NoPreviousVersion):
        mock_db.definitions.get_previous_version("22.220.5")

    with pytest.raises(NoPreviousVersion):
        mock_db.definitions.get_previous_version("99.2.3")

    with pytest.raises(ValueError):
        mock_db.definitions.get_previous_version("foobar")
