""" Tests for LDAP related functions """

from pytest import fixture
from GerritAPI import Gerrit


@fixture(name="gerrit_instance")
def fixture_gerrit_instance(mocker):
    """A gerrit instance for use in tests with pre-mocked functions"""
    gerrit = Gerrit("foo.intel.com/gerrit/", "foo_user", "foo_pass")
    mocker.patch.object(gerrit.rest, "get")
    return gerrit


def test_get_user_sys_windrvbuild_fix(gerrit_instance):
    """Assert that the get user function fixes the sys_windrvbuild username"""
    gerrit_instance.get_user("sys_windrvbuild@intel.com")
    assert gerrit_instance.rest.get.called
    assert gerrit_instance.rest.get.mock_calls[0].args[0] == "accounts/windrvbuild@intel.com/detail"
