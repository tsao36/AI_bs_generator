"""An API for interacting with the DBaaS API, allowing backup and restore of our databases.
The DBaaS API documentation can be found at
https://github.com/intel-innersource/applications.infrastructure.dbaas.customer-documentation/
"""

import os
import sys
import json
import time
import logging
from urllib.parse import urlparse
import humanize
import requests
from requests import Response
from requests.exceptions import ChunkedEncodingError, ConnectionError as RequestsConnectionError


# pylint: disable=wrong-import-position
sys.path.append(os.path.dirname(__file__))  # Add own dir
from Sherlock import Database

# pylint: enable=wrong-import-position

log = logging.getLogger("DBDuplicator")

# Suppress URLLIB prints
urllib_logger = logging.getLogger("urllib3")
urllib_logger.setLevel(logging.NOTSET)

PROTECTED_DATABASES = [Database.database]  # Add other database names here you'd like to avoid restoring to


class DBaaS:
    """An instance of the DBaaS API.
    When creating an instance, the API is queries for the supported services on the supplied instance.

    The services (each database is a 'service') are then saved in the `services` member, which is a dictionary
    using the Database name as key.

    Args:
        instance (str): Name of the DBaaS instance (e.g. sql1312-lc-in.ger.corp.intel.com)
        api_key (str): The API Key to use for authenticating against the API.
                        Can be found/generated at: https://dbaas.intel.com/#/data/apiKeys
        verify (bool|str, optional): Whether to verify the SSL certificate of the API. Defaults to True.
                                     Uses system certificate if `pip-system-certs` is installed and set to True.
                                     Can also be a path to a pem file for private certs.
    """

    def __init__(self, instance, api_key, verify=True):
        self.headers = {"x-api-key": api_key}
        self.base_url = "https://api.dbaas.intel.com/"
        self.dlms_url = "https://dlms.dbaas.intel.com/"
        self.instance = instance
        self.api_key = api_key
        self.verify = verify
        log.info("Initializing DBaaS API Instance...")
        # Get DataService definitions
        ds_request = requests.get(
            self.base_url + "v1/dataservices", data={}, headers=self.headers, timeout=10, verify=self.verify
        )
        self._check_response(ds_request)
        found_services = ds_request.json()["data"]
        self.services = {service["data_service_name"]: service for service in found_services}
        log.info("DBaaS API Instance with %d Databases: %s", len(self.services), ", ".join(list(self.services.keys())))

    def _check_response(self, response: Response):
        if not response.ok:
            if (response_json := response.json()) and "data" in response_json:
                error_message = response_json["data"]
            else:
                error_message = response.text
            log.error("ERROR: %s", error_message)
            response.raise_for_status()

    def _validate_db_name(self, db_name):
        """Validates that the given DB name exists in this instance.
        Raises a DatabaseNotFound error if it doesn't.
        """
        if db_name not in self.services:
            raise DatabaseNotFound(db_name, list(self.services.keys()))

    def _sanitize_route(self, api_route):
        """Sanitizes an API route so that it doesn't have a leading slash"""
        if api_route[:1] == "/":  # Remove leading /
            api_route = api_route[1:]
        return api_route

    def _get_token(self, db_name) -> str:
        """Get a temporary authentication token for the DLMS API used for downloading backups.

        Args:
            db_name (str): The Database to get the token for

        Returns:
            str: The token
        """
        log.info("Getting DLMS Token...")
        token_request_data = {
            "provider": urlparse(self.dlms_url).netloc,
            "server_vip_name": self.instance,
            "data_service_name": db_name,
            "data_service_id": self.services[db_name]["data_service_id"],
            "owner_email": "elad.avron@intel.com",
        }

        token_request = requests.post(
            "https://dbaasapi.intelcloud.intel.com/v2/token",
            headers=self.headers,
            data=json.dumps(token_request_data),
            timeout=30,
            verify=self.verify,
        )
        self._check_response(token_request)

        return token_request.json()["token"]

    def _dbaas_post(self, api_route, db_name, data) -> Response:
        """Post a request to self

        Args:
            api_route (str): The API to post to.
            database (str): The database to perform the operation on.
            data (dict, optional): Any additional arguments to add to the post request JSON. Defaults to {}.

        Returns:
            request.response: The response object.
        """
        data = {"server_vip_name": self.instance, "data_service_name": db_name, **data}
        api_route = self._sanitize_route(api_route)
        return requests.post(self.base_url + api_route, data=data, headers=self.headers, verify=self.verify, timeout=60)

    def _dbaas_get(self, api_route, db_name, data) -> Response:
        """Get information from self

        Args:
            api_route (str): The API to post to.
            db_name (str): The database to request information from.
            data (dict): Any additional arguments to add to the GET request.

        Returns:
            request.response: The unparsed response object.
        """
        data = {"server_vip_name": self.instance, "data_service_name": db_name, **data}
        api_route = self._sanitize_route(api_route)
        return requests.get(
            self.base_url + api_route, params=data, headers=self.headers, verify=self.verify, timeout=60
        )

    def start_backup(self, db_name, wait=True):
        """Starts a backup of the given database name.

        Args:
            db_name (str): The database name to backup.
            wait (bool, optional): Whether to wait for the backup to finish before returning. Defaults to True.
        """
        self._validate_db_name(db_name)
        backup = self._dbaas_post("v1/operations/jobs/backup", db_name, {})
        self._check_response(backup)
        log.info("Backup of %s started successfully!", db_name)
        if wait:
            self.wait_for_latest_backup(db_name)

    def get_latest_backup(self, db_name):
        """Get the latest available backup for a given database.
        Since there can only be one backup at a time for each DB, this always returns the latest state.

        Args:
            db_name (str): The name of the database to query

        Returns:
            dict: The backup operation. information. {} if not found.
        """
        self._validate_db_name(db_name)
        backups_request = self._dbaas_get("v1/operations/jobs/backup", db_name, {})
        self._check_response(backups_request)
        if not (data := backups_request.json()["data"]):
            return {}
        return data[0]

    def wait_for_latest_backup(self, db_name, timeout: int = 60 * 30):
        """Wait for the latest backup of the given database finish running.

        Args:
            db_name (str): The name of the database to backup
            timeout (int, optional): Time (in seconds) to wait for the backup to end. Defaults to 60*30 (30 minutes).

        Raises:
            TimeoutError: If the backup did not finish within the defined `timeout`.
            BackupFailed: If the backup failed for some reason.
        """
        self._validate_db_name(db_name)
        log.info("Waiting for backup of %s to complete...", db_name)
        total_time = 0
        while not (
            (latest_backup := self.get_latest_backup(db_name))
            and latest_backup["database_activity_status_code"] == "COMPLETED"
        ):
            if total_time > timeout:
                raise TimeoutError(f"Backup didn't complete within {int(timeout / 60)} minutes, assuming it failed.")

            if latest_backup["database_activity_status_code"] == "failed":
                raise BackupFailed(latest_backup["data_service_name"], latest_backup["status_message"])

            log.debug("Waiting 15 seconds and checking again...")
            time.sleep(15)
            total_time += 15

        log.info("Backup of %s completed successfully!", db_name)

    def download_latest_backup(self, db_name, target_folder, max_retries=3):
        """Downloads the latest backup of the supplied database to the given folder.
        The file name of the original backup will remain (it's usually in the format: <DB_Name>_<Timestamp>_<Num>.bak)

        Args:
            db_name (str): Name of the database whose backup you want to download.
            target_folder (str): Path to the folder where you want to download the backup to. Must already exist!
            verbose (bool): Print progress to console. Not suitable for Jenkins as it prints every single chunk of 1024
                            bytes in the same line, which Jenkins does not support and will turn into spam prints.
            max_retries (int): Maximum number of retry attempts for the download. Defaults to 3.

        Raises:
            NotADirectoryError: If the supplied target folder path doesn't exist or isn't a folder.
        """
        self._validate_db_name(db_name)

        if not os.path.isdir(os.path.realpath(target_folder)):
            raise NotADirectoryError(f"The supplied path {target_folder} either doesn't exist or is not a directory!")

        token = self._get_token(db_name)
        ds_id = self.services[db_name]["data_service_id"]
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        path_request = requests.get(f"{self.dlms_url}v1/info/{ds_id}", headers=headers, verify=self.verify, timeout=30)
        self._check_response(path_request)

        path_data = path_request.json()
        log.debug("Backup found:\n%s", json.dumps(path_data, indent=4))

        download_data = {
            "owner_email": "sys_windrvbuild@intel.com",  # TODO: Change or pass
            "mount_path": path_data["mount_path"],
            "file_name": path_data["file_name"],
        }

        target_path = os.path.join(target_folder, path_data["display_name"])
        file_size = path_data["file_size"]
        file_size_str = humanize.naturalsize(file_size)
        temp_target_path = target_path + ".tmp"

        # Retry logic for handling network interruptions
        for attempt in range(1, max_retries + 1):
            try:
                log.info(
                    "Downloading %s (This may take a while) ... %s",
                    path_data["display_name"],
                    f"(Attempt {attempt}/{max_retries})" if attempt > 1 else "",
                )

                # Downloads can take a while, setting a 45 minute timeout with keep-alive
                download_request = requests.post(
                    f"{self.dlms_url}v1/download/{ds_id}",
                    json=download_data,
                    headers=headers,
                    verify=self.verify,
                    timeout=(30, 60 * 45),  # (connect timeout, read timeout)
                    stream=True,
                )
                download_request.raise_for_status()

                # Write to a temporary file first, then rename on success
                with open(temp_target_path, mode="wb") as file_writer:
                    downloaded = 0
                    last_log_time = time.time()
                    for chunk in download_request.iter_content(chunk_size=8192):
                        downloaded += len(chunk)
                        file_writer.write(chunk)
                        percent = int(100 * downloaded / file_size)

                        # Log progress every 5 minutes to show activity
                        current_time = time.time()
                        if current_time - last_log_time >= 5 * 60:
                            log.info(
                                "Progress: %s / %s (%d%%)",
                                humanize.naturalsize(downloaded),
                                file_size_str,
                                percent,
                            )
                            last_log_time = current_time

                if os.path.exists(target_path):
                    os.remove(target_path)
                os.rename(temp_target_path, target_path)

                log.info("Done! Successfully downloaded %s", humanize.naturalsize(downloaded))
                return target_path

            except (ChunkedEncodingError, RequestsConnectionError) as err:
                log.warning("Download attempt %d/%d failed: %s", attempt, max_retries, str(err))

                # Clean up partial download
                if os.path.exists(temp_target_path):
                    os.remove(temp_target_path)
                    log.debug("Cleaned up partial download file")

                if attempt < max_retries:
                    wait_time = 30 * (2 ** (attempt - 1))
                    log.info("Retrying in %d seconds...", wait_time)
                    time.sleep(wait_time)

                    # Get a fresh token for retry
                    log.debug("Refreshing authentication token...")
                    token = self._get_token(db_name)
                    headers["Authorization"] = f"Bearer {token}"
                else:
                    log.error("All download attempts failed after %d retries", max_retries)
                    raise

        # This should never be reached, but just in case
        raise RuntimeError("Download failed after all retry attempts")

    def start_restore(self, source_db, target_db, wait=True):
        """Start restoring a backup made to any database into the target database.

        Args:
            source_db (str): The name of the database to get a backup from (must already exist)
            target_db (str): The name of the database to restore to. Can not be one of the DBs in `PROTECTED_DATABASES`.
            wait (bool, optional): Wait for the restore to finish before returning. Defaults to True.

        Raises:
            ProtectedDatabaseException: If the database we tried to restore to is protected.
        """
        self._validate_db_name(source_db)
        self._validate_db_name(target_db)
        if target_db in PROTECTED_DATABASES:
            raise ProtectedDatabaseException(target_db)

        log.info("Restoring backup of %s into %s ...", source_db, target_db)
        latest_backup = self.get_latest_backup(source_db)
        params = {
            "backup_share_location": latest_backup["activity_share_location"],
            "backup_filename": latest_backup["file_name"],
        }

        restore = self._dbaas_post("/v1/operations/jobs/restore", target_db, data=params)
        restore.raise_for_status()

        log.info("Restore of %s into %s started...", source_db, target_db)

        if wait:
            self.wait_for_latest_restore(target_db)

    def get_latest_restore(self, db_name):
        """Get the latest available restore for a given database.
        Since there can only be one restore at a time for each DB, this always returns the latest state.

        Args:
            db_name (str): The name of the database to query

        Returns:
            dict: The restore operations information. {} if not found.
        """
        self._validate_db_name(db_name)
        restore_request = self._dbaas_get("/v1/operations/jobs/restore", db_name, {})
        restore_request.raise_for_status()
        if not (data := restore_request.json()["data"]):
            return {}
        return data[0]

    def wait_for_latest_restore(self, db_name, timeout: int = 60 * 120):
        """Wait for the latest restore of the given database finish running.

        Args:
            db_name (str): The name of the database being restored.
            timeout (int, optional): Time (in seconds) to wait for the restore to end. Defaults to 60*120 (120 minutes).

        Raises:
            TimeoutError: If the restore did not finish within the defined `timeout`.
            RestoreFailed: If the restore failed for some reason.
        """
        total_time = 0
        while not (
            (latest_restore := self.get_latest_restore(db_name))
            and latest_restore["database_activity_status_code"] == "COMPLETED"
        ):
            if total_time > timeout:
                raise TimeoutError(f"Restore didn't complete within {int(timeout / 60)} minutes, assuming it failed.")

            if latest_restore["database_activity_status_code"] == "failed":
                raise RestoreFailed(latest_restore["data_service_name"], latest_restore["status_message"])

            log.debug("Waiting 60 seconds and checking again...")
            time.sleep(60)
            total_time += 60
        log.info("Restoring into %s completed successfully!", db_name)


class ProtectedDatabaseException(Exception):
    """Exception when trying to delete a non-empty folder with recursive = True"""

    def __init__(self, db_name):
        self.db_name = db_name
        super().__init__(f"{db_name} is a protected database and can not be restored to!")


class DatabaseNotFound(Exception):
    """Exception when trying to get from or post to a database that doesn't exist in this instance"""

    def __init__(self, db_name, available_names):
        self.db_name = db_name
        self.available_names = available_names
        super().__init__(
            f"{db_name} is not available in this instance. Available instances are: {', '.join(available_names)}"
        )


class BackupFailed(Exception):
    """Exception when the backup operation fails."""

    def __init__(self, db_name, error_message):
        self.db_name = db_name
        self.error = error_message
        super().__init__(f"Backup of {db_name} failed: {error_message}")


class RestoreFailed(Exception):
    """Exception when the restore operation fails."""

    def __init__(self, db_name, error_message):
        self.db_name = db_name
        self.error = error_message
        super().__init__(f"Restore of {db_name} failed: {error_message}")
