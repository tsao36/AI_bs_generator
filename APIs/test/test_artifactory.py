""" Test the ArtifactoryAPI library """

import json
import os
import pytest
from pytest_mock import MockerFixture
from ArtifactoryAPI import (
    ArtifactAlreadyExists,
    ArtifactDoesNotExist,
    ArtifactHasNoProps,
    Artifactory,
    DirNotEmpty,
    NoMatchingSha1,
    NoMatchingProps,
    NoMatchingArtifact,
)

MOCK_SERVER = "https://foo.intel.com/artifactory/"
MOCK_KEY = "foo_key"
MOCK_REPO = "wcd-binaries-local"
MOCK_DIRNAME = "path/to/foo"
MOCK_SHA1 = "1234567890AABBCCDDEE"
MOCK_FILE_NAME = "WCD_FW_BUILD_123456_SHA1_12345678_L_BIN_WIN.tar.gz"
MOCK_PATH = f"{MOCK_REPO}/{MOCK_DIRNAME}/{MOCK_FILE_NAME}"
MOCK_PROPS_NO_RETENTION_DAYS = {
    "fw_full_sha1": [MOCK_SHA1],
    "sign_remotely": ["true"],
    "firmware_cfg_selection_option": ["all"],
    "mac_address": ["NA"],
}
MOCK_PROPS_WITH_DEFAULT_RETENTION_DAYS = {"retention.days": "365", **(MOCK_PROPS_NO_RETENTION_DAYS)}
MOCK_PROPS_WITH_NON_DEFAULT_RETENTION_DAYS = {"retention.days": "100", **(MOCK_PROPS_NO_RETENTION_DAYS)}

MOCK_QUERY_RESULTS = [
    {
        "created_by": "sys_windrvbuild",
        "created": "2021-08-08T13:46:59.262+03:00",
        "modified": "2021-08-08T13:46:54.894+03:00",
        "modified_by": "sys_windrvbuild",
        "name": MOCK_FILE_NAME,
        "path": MOCK_DIRNAME,
        "repo": MOCK_REPO,
        "size": 205159110,
        "type": "file",
        "updated": "2021-08-08T13:46:59.263+03:00",
    }
]
# Test that in case of multiple matches, take the latest
MOCK_LATEST_RESULT = MOCK_QUERY_RESULTS[0].copy()
MOCK_LATEST_RESULT["created"] = "2022-08-08T13:46:59.262+03:00"
MOCK_LATEST_RESULT["name"] = "latest.tar.gz"
MOCK_MULTIPLE_RESULTS = MOCK_QUERY_RESULTS + [MOCK_LATEST_RESULT]
MOCK_LATEST_PATH = f"{MOCK_REPO}/{MOCK_DIRNAME}/latest.tar.gz"


@pytest.fixture(name="artifactory")
def fixture_artifactory(mocker, mock_artifact):
    """
    A mock Artifactory instance for usage in tests.
    Fully functional, but fake.
    """
    mocker.patch("os.path.isfile", return_value=True)
    mocker.patch("builtins.open", mocker.mock_open(read_data=MOCK_KEY))
    mocker.patch("artifactory.ArtifactoryPath.__new__", return_value=mock_artifact)
    return Artifactory(MOCK_SERVER)


@pytest.fixture(name="mock_artifact")
def fixture_mock_artifact(mocker: MockerFixture):
    """A mock artifact for usage in tests."""
    mock_artifact = mocker.MagicMock()
    mock_artifact.properties = MOCK_PROPS_WITH_DEFAULT_RETENTION_DAYS.copy()
    mock_artifact.name = MOCK_FILE_NAME
    mock_artifact.repo = MOCK_REPO
    mocker.patch.object(mock_artifact, "is_dir", return_value=False)
    return mock_artifact


def test_init(artifactory):
    """Test that init is successful"""
    assert artifactory is not None


def test_pam_env_var_missing(mocker):
    """Test that if the key file isn't found an exception is raised"""
    # mocker.patch("os.path.isfile", return_value=False)
    mocker.patch.dict(os.environ, {}, clear=True)
    with pytest.raises(EnvironmentError):
        Artifactory("foo")


def test_pam_file_is_missing(mocker):
    """Test that if the key file isn't found an exception is raised"""
    mocker.patch("os.path.exists", return_value=False)
    with pytest.raises(FileNotFoundError):
        Artifactory("foo")


def test_sha1_from_path(artifactory, mocker, mock_artifact):
    """Test Path to SHA1 conversion"""
    assert artifactory.sha1_from_path("foo") == MOCK_SHA1

    # Test no "fw_full_sha1" in artifact
    del mock_artifact.properties["fw_full_sha1"]
    with pytest.raises(NoMatchingSha1):
        artifactory.sha1_from_path("foo")

    # Test no such artifact
    mocker.patch.object(mock_artifact, "exists", return_value=False)
    with pytest.raises(ArtifactDoesNotExist):
        artifactory.sha1_from_path("foo")


def test_path_from_props(artifactory, mocker, mock_artifact):
    """Test props to path conversion"""

    mock_search = {
        "$and": [
            {"@build_tag": "jenkins-FW_Build_And_Pack-123456"},  # Make sure to always test using chars `-` and `_`
            {"@foo": "bar"},
            {"name": {"$match": "*BIN_WIN*"}},
        ]
    }
    mock_props_dict = {"build_tag": "jenkins-FW_Build_And_Pack-123456", "foo": "bar"}
    mock_props_str = "build_tag=jenkins-FW_Build_And_Pack-123456;foo=bar"

    # Happy Path
    aql_mocker = mocker.patch.object(mock_artifact, "aql", return_value=MOCK_QUERY_RESULTS)
    path = artifactory.path_from_props(mock_props_str)
    assert path == MOCK_PATH
    assert aql_mocker.mock_calls[0][1][1] == mock_search

    # Test dict props
    aql_mocker = mocker.patch.object(mock_artifact, "aql", return_value=MOCK_MULTIPLE_RESULTS)
    path = artifactory.path_from_props(mock_props_dict)
    assert path == MOCK_LATEST_PATH
    assert aql_mocker.mock_calls[0][1][1] == mock_search

    # Test that in case of multiple matches, take the latest
    aql_mocker = mocker.patch.object(mock_artifact, "aql", return_value=MOCK_MULTIPLE_RESULTS)
    path = artifactory.path_from_props(mock_props_str)
    assert path == MOCK_LATEST_PATH
    assert aql_mocker.mock_calls[0][1][1] == mock_search

    # Test No Matching Props
    mocker.patch.object(mock_artifact, "aql", return_value=[])
    with pytest.raises(NoMatchingProps):
        artifactory.path_from_props(mock_props_str)

    # Test bad prop string
    with pytest.raises(ValueError):
        artifactory.path_from_props("Foobar")

    with pytest.raises(ValueError):
        artifactory.path_from_props("Foo=bar=lorem")

    # Test bad dict
    with pytest.raises(TypeError):
        artifactory.path_from_props({"Foo": 4, "Bar": {"a": "b"}, "Lorem": [1, 2, 3]})


def test_path_from_sha1(artifactory: Artifactory, mocker: MockerFixture, mock_artifact):
    """Test SHA1 to Path conversion"""

    original_query_dict = {
        "$and": [
            {"@fw_full_sha1": MOCK_SHA1},
            {"name": {"$match": "*BIN_WIN*"}},
        ]
    }

    # Set up mocking
    aql_mocker = mocker.patch.object(mock_artifact, "aql")

    # Happy Path
    query_dict = json.loads(json.dumps(original_query_dict))  # Reset the query dict
    aql_mocker.return_value = MOCK_QUERY_RESULTS
    path = artifactory.path_from_sha1(MOCK_SHA1)
    assert path == MOCK_PATH
    assert json.dumps(aql_mocker.call_args_list[0][0][1]) == json.dumps(query_dict)
    assert aql_mocker.call_count == 1
    aql_mocker.reset_mock()

    # Test using additional arguments
    aql_mocker.return_value = MOCK_QUERY_RESULTS
    with pytest.raises(TypeError):
        artifactory.path_from_sha1(MOCK_SHA1, {"foo": "bar"})

    with pytest.raises(TypeError):
        artifactory.path_from_sha1(MOCK_SHA1, ["foo", "bar"])

    with pytest.raises(TypeError):
        artifactory.path_from_sha1(MOCK_SHA1, [{"foo": "bar"}, "bar"])

    query_dict = json.loads(json.dumps(original_query_dict))  # Reset the query dict
    query_dict["$and"].append({"foo": "bar"})
    path = artifactory.path_from_sha1(MOCK_SHA1, [{"foo": "bar"}])
    assert path == MOCK_PATH
    assert json.dumps(aql_mocker.call_args_list[0][0][1]) == json.dumps(query_dict)
    assert aql_mocker.call_count == 1
    aql_mocker.reset_mock()

    # Test No Matching Artifact
    aql_mocker.return_value = []
    with pytest.raises(NoMatchingArtifact):
        artifactory.path_from_sha1(MOCK_SHA1)

    # Test multiple matches, take the latest
    aql_mocker.return_value = MOCK_MULTIPLE_RESULTS
    path = artifactory.path_from_sha1(MOCK_SHA1)
    assert path == MOCK_LATEST_PATH

    # Test fallback query
    query_dict = json.loads(json.dumps(original_query_dict))  # Reset the query dict
    fallback_query = [{"fallback_foo": "fallback_bar"}]
    query_dict["$and"] += fallback_query
    aql_mocker.reset_mock()
    aql_mocker.return_value = None  # So that it takes side effect
    aql_mocker.side_effect = [[], MOCK_QUERY_RESULTS]
    path = artifactory.path_from_sha1(MOCK_SHA1, fallback_query=fallback_query)
    assert json.dumps(aql_mocker.mock_calls[1].args[1]) == json.dumps(query_dict)
    assert aql_mocker.call_count == 2


def test_deploy_with_retention_days_in_props(artifactory, mocker, mock_artifact):
    """Test deploying artifacts - with obviously mocking the actual deployment ('retention.days' is given)"""
    # Test happy path - dir doesn't exist
    # Side effects: pre-deploy target_dir_instance, post-deploy target_file_instance
    exist_mocker = mocker.patch.object(mock_artifact, "exists", side_effect=[False, True])
    mkdir_mocker = mocker.patch.object(mock_artifact, "mkdir")
    artifactory.deploy_artifact(MOCK_FILE_NAME, MOCK_DIRNAME, MOCK_PROPS_WITH_NON_DEFAULT_RETENTION_DAYS)
    assert mock_artifact.properties == MOCK_PROPS_WITH_NON_DEFAULT_RETENTION_DAYS
    assert mkdir_mocker.called
    assert exist_mocker.call_count == 2

    # Test happy path - dir exists, artifact not
    # Side effects: pre-deploy target_dir_instance, pre-deploy target_file_instance, post-deploy target_file_instance
    mkdir_mocker = mocker.patch.object(mock_artifact, "mkdir")  # We only mock it to test that it didn't get called
    exist_mocker = mocker.patch.object(mock_artifact, "exists", side_effect=[True, False, True])
    artifactory.deploy_artifact(MOCK_FILE_NAME, MOCK_DIRNAME, MOCK_PROPS_WITH_NON_DEFAULT_RETENTION_DAYS)
    assert mock_artifact.properties == MOCK_PROPS_WITH_NON_DEFAULT_RETENTION_DAYS
    assert not mkdir_mocker.called
    assert exist_mocker.call_count == 3

    # Test happy path - file artifact already exists with overwrite
    # Side effects: pre-deploy target_dir_instance, pre-deploy target_file_instance, post-deploy target_file_instance
    exist_mocker = mocker.patch.object(mock_artifact, "exists", side_effect=[True, True, True])
    artifactory.deploy_artifact(
        MOCK_FILE_NAME, MOCK_DIRNAME, MOCK_PROPS_WITH_NON_DEFAULT_RETENTION_DAYS, overwrite=True
    )
    assert mock_artifact.properties == MOCK_PROPS_WITH_NON_DEFAULT_RETENTION_DAYS
    assert exist_mocker.call_count == 3

    # Test artifact already exist - no override
    # Side effects: pre-deploy target_dir_instance, pre-deploy target_file_instance
    exist_mocker = mocker.patch.object(mock_artifact, "exists", side_effect=[True, True])
    with pytest.raises(ArtifactAlreadyExists):
        artifactory.deploy_artifact(
            MOCK_FILE_NAME, MOCK_DIRNAME, MOCK_PROPS_WITH_NON_DEFAULT_RETENTION_DAYS, overwrite=False
        )
        assert exist_mocker.call_count == 2

    # Test deploy failed
    # Side effects: pre-deploy target_dir_instance, pre-deploy target_file_instance, post-deploy target_file_instance
    mkdir_mocker = mocker.patch.object(mock_artifact, "mkdir")  # We only mock it to test that it didn't get called
    exist_mocker = mocker.patch.object(mock_artifact, "exists", side_effect=[True, False, False])
    with pytest.raises(FileNotFoundError):
        artifactory.deploy_artifact(
            MOCK_FILE_NAME, MOCK_DIRNAME, MOCK_PROPS_WITH_NON_DEFAULT_RETENTION_DAYS, overwrite=False
        )
        assert exist_mocker.call_count == 3


def test_deploy_no_retention_days_in_props(artifactory, mocker, mock_artifact):
    """Test deploying artifacts - with obviously mocking the actual deployment ('retention.days' is not given)"""

    # Test happy path - dir doesn't exist
    # Side effects: pre-deploy target_dir_instance, post-deploy target_file_instance
    exist_mocker = mocker.patch.object(mock_artifact, "exists", side_effect=[False, True])
    mkdir_mocker = mocker.patch.object(mock_artifact, "mkdir")
    artifactory.deploy_artifact(MOCK_FILE_NAME, MOCK_DIRNAME, MOCK_PROPS_NO_RETENTION_DAYS)
    assert mock_artifact.properties == MOCK_PROPS_WITH_DEFAULT_RETENTION_DAYS
    assert mkdir_mocker.called
    assert exist_mocker.call_count == 2

    # Test happy path - dir exists, artifact not
    # Side effects: pre-deploy target_dir_instance, pre-deploy target_file_instance, post-deploy target_file_instance
    mkdir_mocker = mocker.patch.object(mock_artifact, "mkdir")  # We only mock it to test that it didn't get called
    exist_mocker = mocker.patch.object(mock_artifact, "exists", side_effect=[True, False, True])
    artifactory.deploy_artifact(MOCK_FILE_NAME, MOCK_DIRNAME, MOCK_PROPS_NO_RETENTION_DAYS)
    assert mock_artifact.properties == MOCK_PROPS_WITH_DEFAULT_RETENTION_DAYS
    assert not mkdir_mocker.called
    assert exist_mocker.call_count == 3

    # Test happy path - file artifact already exists with overwrite
    # Side effects: pre-deploy target_dir_instance, pre-deploy target_file_instance, post-deploy target_file_instance
    exist_mocker = mocker.patch.object(mock_artifact, "exists", side_effect=[True, True, True])
    artifactory.deploy_artifact(MOCK_FILE_NAME, MOCK_DIRNAME, MOCK_PROPS_NO_RETENTION_DAYS, overwrite=True)
    assert mock_artifact.properties == MOCK_PROPS_WITH_DEFAULT_RETENTION_DAYS
    assert exist_mocker.call_count == 3

    # Test happy path - dir exists, artifact not.
    # no properties are given
    mkdir_mocker = mocker.patch.object(mock_artifact, "mkdir")  # We only mock it to test that it didn't get called
    exist_mocker = mocker.patch.object(mock_artifact, "exists", side_effect=[True, False, True])
    artifactory.deploy_artifact(MOCK_FILE_NAME, MOCK_DIRNAME, {}, overwrite=True)
    assert mock_artifact.properties == {"retention.days": "365"}


def test_set_property(artifactory, mock_artifact):
    """Test that setting a new property for the artifact works"""
    artifactory.set_property(MOCK_PATH, "new_foo", "new_bar")
    assert mock_artifact.properties.get("new_foo") == "new_bar"


def test_create_folder(artifactory, mocker, mock_artifact):
    """Testing the makedir command is only called if dir doesn't exist"""

    # Happy path
    mkdir_mocker = mocker.patch.object(mock_artifact, "mkdir")
    exist_mocker = mocker.patch.object(mock_artifact, "exists", return_value=False)
    artifactory.create_folder("foobar")
    assert mkdir_mocker.called
    assert exist_mocker.called

    # Dir already exists
    exist_mocker = mocker.patch.object(mock_artifact, "exists", return_value=True)
    mkdir_mocker = mocker.patch.object(mock_artifact, "mkdir")
    artifactory.create_folder("foobar")
    assert not mkdir_mocker.called


def test_delete_folder(artifactory, mocker, mock_artifact):
    """Test deleting a directory logic"""

    # Happy Path - Empty folder
    mocker.patch.object(mock_artifact, "exists", return_value=True)
    mocker.patch.object(mock_artifact, "iter_dir", return_value=[])
    rmdir_mocker = mocker.patch.object(mock_artifact, "rmdir")
    artifactory.delete_folder("foobar")
    assert rmdir_mocker.called

    # Happy Path - Non empty + recursive
    mocker.patch.object(mock_artifact, "exists", return_value=True)
    mocker.patch.object(mock_artifact, "iterdir", return_value=["foo"])
    rmdir_mocker = mocker.patch.object(mock_artifact, "rmdir")
    artifactory.delete_folder("foobar", recursive=True)
    assert rmdir_mocker.called

    # Folder doesn't exist
    mocker.patch.object(mock_artifact, "exists", return_value=False)
    rmdir_mocker = mocker.patch.object(mock_artifact, "rmdir")
    artifactory.delete_folder("foobar")
    assert not rmdir_mocker.called

    # Folder exist - not empty - no recursive
    mocker.patch.object(mock_artifact, "exists", return_value=True)
    mocker.patch.object(mock_artifact, "iterdir", return_value=["foo"])
    rmdir_mocker = mocker.patch.object(mock_artifact, "rmdir")
    with pytest.raises(DirNotEmpty):
        artifactory.delete_folder("foobar")
        assert not rmdir_mocker.called


def test_get_instance(artifactory, mocker):
    """Test path sanitation in instance resolving"""

    def mock_init(_, target_instance, token):
        mock_object = mocker.MagicMock()
        mock_object.token = token
        mock_object.path = target_instance
        return mock_object

    mocker.patch("artifactory.ArtifactoryPath.__new__", new=mock_init)

    # Happy Path
    path = artifactory.get_instance(MOCK_PATH)
    assert path.path == MOCK_SERVER + MOCK_PATH

    # Test that Leading and trailing / is removed
    path = artifactory.get_instance("/" + MOCK_PATH + "/")
    assert path.path == MOCK_SERVER + MOCK_PATH


def test_artifact_validator(artifactory: Artifactory, mock_artifact, mocker: MockerFixture):
    """Test that the artifact validator works as expected"""
    # Happy Path
    expected_validity = {
        "signed_remotely": True,
        "full_fw_build": True,
        "supported_repo": True,
        "windows_binary": True,
        "mac_address": True,
    }
    assert expected_validity == artifactory.validate_artifact_props(MOCK_PATH)

    # Validate "0x" is also excluded
    MOCK_PROPS_NO_RETENTION_DAYS["mac_address"] = ["0x"]
    assert expected_validity == artifactory.validate_artifact_props(MOCK_PATH)

    # Test partial build
    mock_artifact.properties["firmware_cfg_selection_option"] = ["Partial"]
    expected_validity["full_fw_build"] = False
    assert expected_validity == artifactory.validate_artifact_props(MOCK_PATH)

    # Test unsigned build
    mock_artifact.properties["sign_remotely"] = ["False"]
    expected_validity["signed_remotely"] = False
    assert expected_validity == artifactory.validate_artifact_props(MOCK_PATH)

    # Test unsupported repo
    mock_artifact.repo = "FooRepo"
    expected_validity["supported_repo"] = False
    assert expected_validity == artifactory.validate_artifact_props(MOCK_PATH)

    # Test Not Windows Binary
    mock_artifact.name = "FooName"
    expected_validity["windows_binary"] = False
    assert expected_validity == artifactory.validate_artifact_props(MOCK_PATH)

    # Test locked MAC Address
    mock_artifact.properties["mac_address"] = "0x12345"
    expected_validity["mac_address"] = False
    assert expected_validity == artifactory.validate_artifact_props(MOCK_PATH)

    # Test invalid props
    mock_artifact.properties = None
    with pytest.raises(ArtifactHasNoProps):
        artifactory.validate_artifact_props(MOCK_PATH)
    mock_artifact.properties = MOCK_PROPS_NO_RETENTION_DAYS

    # Test Artifact Is A Directory
    mocker.patch.object(mock_artifact, "is_dir", return_value=True)
    with pytest.raises(IsADirectoryError):
        artifactory.validate_artifact_props(MOCK_PATH)
    mocker.patch.object(mock_artifact, "is_dir", return_value=False)

    # Test Artifact Does Not Exist
    mocker.patch.object(mock_artifact, "exists", return_value=False)
    with pytest.raises(ArtifactDoesNotExist):
        artifactory.validate_artifact_props(MOCK_PATH)
