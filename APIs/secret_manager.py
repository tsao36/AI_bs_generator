"""Secret Management APIs"""

import os
import sys
import time
import logging
import requests

# pylint: disable=wrong-import-position
sys.path.append(os.path.join(os.path.dirname(__file__)))
from Sherlock import PAM

# pylint: enable=wrong-import-position

log = logging.getLogger("PAM")


class SecretManager:
    """A Secret Manager instance

    Args:
        app_id (str): The PAM AppID. e.g. 12345-PR-CERT
        safe_name (str): The PAM Safe Name. e.g. AAM-PR-MYAPP-12345
        certificate (str): Path to the PAM Certificate to use for authentication
                           Instructions on acquiring the certificate can be found at
                           https://wiki.ith.intel.com/display/WCDSherlock/Secret+Manager
    """

    def __init__(self, app_id, safe_name, certificate):
        self.app_id = app_id
        self.safe_name = safe_name
        self.cert = certificate

        if not os.path.exists(self.cert):
            raise FileNotFoundError(
                f"""Certificate file not found at {self.cert},
                Please refer to the wiki for instructions on acquiring the certificate:
                https://wiki.ith.intel.com/display/WCDSherlock/Secret+Manager#SecretManager-GettingthePAMCertificate
                """
            )

    def get_secret(self, username, service, max_retry=3):
        """Get a secret from the PotatoFarm Vault

        Args:
            username (str): The username to get the secret for.
            service (str): The service this username is for.
                           Matches the `Address` field in PAM.
            max_retry (int, optional): Number of retries in case of timeout. Defaults to 3 retries.
                                   Between each retry, there is an exponential sleep time (n*5 seconds).

        Raises:
            UserNotFound: If the specified combination of username and service doesn't exist in the safe.

        Returns:
            str: The secret
        """
        params = {"AppID": self.app_id, "Safe": self.safe_name, "UserName": username, "Address": service}
        log.info("Using certificate: %s", self.cert)
        log.info('Getting secret for user "%s" to service "%s" in safe "%s" ...', username, service, self.safe_name)
        retry = 0
        while True:
            try:
                response = requests.get(PAM.cert_endpoint, params=params, cert=self.cert, verify=self.cert, timeout=10)
                break

            # ReadTimeout and ConnectTimeout are both caught by requests.exceptions.Timeout
            except (TimeoutError, requests.exceptions.Timeout):
                if retry >= max_retry:
                    log.error("Request timed out after %d retries. Aborting!", max_retry)
                    raise
                retry += 1
                wait = 5**retry
                log.error("Request timed out. Waiting %s seconds and retrying... (%d/%d)", wait, retry, max_retry)
                time.sleep(wait)

        if not response.ok:
            if response.status_code == 404:
                raise UserNotFound(self, username, service)
            response.raise_for_status()
        return response.json()["Content"]


class UserNotFound(Exception):
    """Exception when trying to delete a non-empty folder with recursive = True"""

    def __init__(self, instance: SecretManager, user, service):
        self.user = user
        self.service = service
        self.instance = instance
        super().__init__(f'User "{user}" was not found for the service "{service}" in the safe "{instance.safe_name}"!')
