""" Tests for LDAP related functions """

from pytest import fixture
from LdapAPI import query_ldap, display_name_to_first_last


@fixture(name="mock_connection")
def fixture_mock_connection(mocker):
    """Fake "connection" object with pre-mocked entry results"""
    mock_connection = mocker.MagicMock()
    mock_entry = mocker.MagicMock()
    mock_entry.mail = "foo.bar@intel.com"
    mock_entry.displayName = "Bar, Foo"
    mock_entry.sAMAccountName = "foobar"
    mock_connection.entries = [mock_entry]
    return mock_connection


def test_real_data():
    """Test that real data is queries from LDAP correctly"""

    sys_windrvbuild = query_ldap("windrvbuild@intel.com")
    assert bool(sys_windrvbuild)
    assert sys_windrvbuild[0].get("email") == "sys_windrvbuild@intel.com"

    sys_windrvbuild = query_ldap("sys_windrvbuild@intel.com")
    assert bool(sys_windrvbuild)
    assert sys_windrvbuild[0].get("email") == "sys_windrvbuild@intel.com"

    saar_data = {"user_name": "saarbare", "email": "saar.barel@intel.com", "display_name": "Barel, Saar"}

    results = query_ldap(saar_data["email"])
    assert bool(results)
    assert results[0] == saar_data

    results = query_ldap(saar_data["display_name"])
    assert bool(results)
    assert results[0] == saar_data

    results = query_ldap(saar_data["user_name"])
    assert bool(results)
    assert results[0] == saar_data


def test_query(mocker, mock_connection):
    """Test that queries are passed and returned as expected"""

    mocker.patch("ldap3.Connection.__new__", return_value=mock_connection)
    mock_search = mocker.MagicMock()
    mock_connection.search = mock_search
    query_results = query_ldap("foobar")
    assert query_results == [{"display_name": "Bar, Foo", "email": "foo.bar@intel.com", "user_name": "foobar"}]

    for query in ["(displayName=foobar*)", "(givenName=foobar*)", "(sAMAccountName=foobar*)"]:
        assert query in mock_search.mock_calls[0].kwargs["search_filter"]


def test_sys_windrvbuild_fix(mocker, mock_connection):
    """Test that sys_windrvbuild email is normalized to LDAP's valid form"""
    mocker.patch("ldap3.Connection.__new__", return_value=mock_connection)
    mock_search = mocker.MagicMock()
    mock_connection.search = mock_search
    query_ldap("windrvbuild@intel.com")
    assert "(mail=sys_windrvbuild@intel.com)" in mock_search.mock_calls[0].kwargs["search_filter"]

    query_ldap("sys_windrvbuild@intel.com")
    assert "(mail=sys_windrvbuild@intel.com)" in mock_search.mock_calls[0].kwargs["search_filter"]


def test_name_normalizer():
    """Test that the name normalizer fixes all types of names"""
    assert display_name_to_first_last("Foo Bar") == "Foo Bar"
    assert display_name_to_first_last("Bar, Foo") == "Foo Bar"
    assert display_name_to_first_last("Bar Johnson, Foo") == "Foo Bar Johnson"
    assert display_name_to_first_last("Bar, Foo Johnson") == "Foo Johnson Bar"
