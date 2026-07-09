"""Zebra API module to interact with Zebra server"""

import logging
import os
import requests
from requests.exceptions import HTTPError
from requests_ntlm import HttpNtlmAuth
from Sherlock import Zebra, IAMWS


logging.basicConfig(format="%(asctime)s [%(levelname)s][%(name)s] %(message)s")
log = logging.getLogger("ZebraAPI")
log.setLevel(logging.INFO)


class ZebraAPI:
    """
    ZebraAPI class to manage Zebra server operations
    """

    def __init__(self):
        self.server_name = Zebra.server
        self.ssl_verify = os.path.realpath(os.environ["PF_CERT"]) if os.environ.get("PF_CERT") else False
        self.auth = HttpNtlmAuth(IAMWS.username, IAMWS.password)

    def get_latest_nightly_wrt(self):
        """
        Get the latest nightly WRT build from Zebra server
        """
        request = (
            rf"{self.server_name}odata/wt_Generic?$filter=TIC_Prefix%20eq%20%27WRT_2G_Windows%27%20and%20"
            r"substringof(%27Nightly%27,Comment)%20and%20substringof(%27master%27,Comment)"
            r"%20and%20substringof(%27PYTM%27,Comment)&$orderby=Tic%20desc&$top=1"
        )
        try:
            response = requests.get(request, verify=self.ssl_verify, timeout=10, auth=self.auth)

            # Check if the request was successful
            if response.status_code == 200:
                # Parse the response content
                data = response.json()  # Assuming the response is in JSON format
                log.info("Data of request response: %s", data)
            else:
                raise HTTPError(f"Failed to get latest nightly WRT build. HTTP Status Code: {response.status_code}")

        except Exception as ex:
            log.error("Failed to get latest nightly WRT build: %s", ex)
            return None
        return data["value"][0]["Tic"] if data["value"] else None

    def get_wrt_artifact_location(self, tic, artifactory=True):
        """
        Get the artifact location for a given WRT TIC from Zebra server.

        Args:
            tic (str): Tic identifier of the WRT build.
            artifactory (bool): If True use Artifactory (ArtifactTypeID 9), otherwise use internal
            location (ArtifactTypeID 2).

        # Returns:
        #     str | None: Location string if found, otherwise None.
        """
        choose_location = 9 if artifactory else 2
        request = (
            rf"{self.server_name}odata/wt_ArtifactsLocation?"
            rf"$filter=Tic%20eq%20%27{tic}%27%20and%20ArtifactTypeID%20eq%20{choose_location}&$select=Location"
        )
        try:
            response = requests.get(request, verify=self.ssl_verify, timeout=10, auth=self.auth)

            # Check if the request was successful
            if response.status_code == 200:
                # Parse the response content
                data = response.json()  # Assuming the response is in JSON format
                log.info("Data of request response: %s", data)
            else:
                raise HTTPError(f"Failed to get WRT build location. HTTP Status Code: {response.status_code}")

        except Exception as ex:
            log.error("Failed to get WRT build location: %s", ex)
            return None
        return data["value"][0]["Location"] if data["value"] else None
