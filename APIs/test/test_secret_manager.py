"""Test functionality of the secrets manager"""

import os
import pytest
from pytest import fixture
from pytest_mock import MockerFixture
from requests.exceptions import ReadTimeout, ConnectTimeout, Timeout
from secret_manager import SecretManager, UserNotFound

FAKE_APP_ID = "12345-PR-CERT"
FAKE_SAFE_NAME = "AAM-PR-MYAPP-12345"
FAKE_CERT_PATH = "fake_cert"


@fixture(name="mock_secret_manager")
def fixture_mock_secret_manager(mocker: MockerFixture) -> SecretManager:
    """Fake "connection" object with pre-mocked entry results"""
    original_path_exists = os.path.exists
    mocker.patch(
        "os.path.exists", new=lambda x: True if x == FAKE_CERT_PATH else original_path_exists(x)
    )  # Mock only for checking if cert
    return SecretManager(FAKE_APP_ID, FAKE_SAFE_NAME, FAKE_CERT_PATH)


def test_secret_manager_init(mocker: MockerFixture):
    """Test that SecretManager is correctly initialized and can retrieve secrets"""

    # Test certificate not found
    mocker.patch("os.path.exists", return_value=False)
    with pytest.raises(FileNotFoundError):
        SecretManager(FAKE_APP_ID, FAKE_SAFE_NAME, "fake_cert")


def test_get_secret(mocker: MockerFixture, mock_secret_manager: SecretManager):
    """Test that SecretManager can retrieve secrets"""

    mock_response = mocker.MagicMock()
    mock_response.json.return_value = {"Content": "fake_secret"}
    mock_response.ok = True
    mock_response.status_code = 200
    requests_mocker = mocker.patch("requests.get", return_value=mock_response)
    sleep_mocker = mocker.patch("time.sleep")

    # Happy Path
    secret = mock_secret_manager.get_secret("fake_user", "fake_service")
    assert secret == "fake_secret"
    assert requests_mocker.called is True
    assert sleep_mocker.called is False

    # UserNotFound
    requests_mocker.reset_mock()
    sleep_mocker.reset_mock()
    mock_response.ok = False
    mock_response.status_code = 404
    with pytest.raises(UserNotFound):
        mock_secret_manager.get_secret("fake_user", "fake_service")

    assert sleep_mocker.called is False
    assert requests_mocker.called is True
    assert requests_mocker.call_count == 1

    # Request Timeout without recovery
    requests_mocker.reset_mock()
    sleep_mocker.reset_mock()
    requests_mocker.side_effect = [ReadTimeout, ConnectTimeout, Timeout, TimeoutError]
    with pytest.raises(TimeoutError):  # The last will be raised
        mock_secret_manager.get_secret("fake_user", "fake_service")

    assert sleep_mocker.called is True
    assert requests_mocker.called is True
    assert requests_mocker.call_count == 4
    assert sleep_mocker.call_count == 3

    # Request Timeout with recovery
    requests_mocker.reset_mock()
    sleep_mocker.reset_mock()
    mock_response.ok = True
    mock_response.status_code = 200

    # ReadTimeout and ConnectTimeout are both catched by requests.exceptions.Timeout
    requests_mocker.side_effect = [ReadTimeout, ConnectTimeout, mock_response]
    mock_secret_manager.get_secret("fake_user", "fake_service")

    assert requests_mocker.call_count == 3
    assert sleep_mocker.call_count == 2
