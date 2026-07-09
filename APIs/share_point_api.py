"""
SharePoint API to access MSFT SharePoint files and directories
Requires "Office365-REST-Python-Client" module to be installed
"""

import logging
import os
import re
from office365.runtime.auth.client_credential import ClientCredential
from office365.sharepoint.client_context import ClientContext

log = logging.getLogger("SHAREPOINT_API")
log.setLevel(logging.INFO)


class SharePoint:
    """
    SharePoint functionality
    """

    def __init__(self, sherlock_sharepoint_team_info: dict):
        """
        CTOR for the SharePoint class
        gets the relevant "team" from Sherlock.py SharePoint class
        """
        self.__team_info = sherlock_sharepoint_team_info
        self.__date_format = "%Y-%m-%dT%H:%M:%SZ"

        # create instance of share point
        client_credentials = ClientCredential(self.__team_info["client_id"], self.__team_info["secret"])
        self.__ctx = ClientContext(self.__team_info["site_url"]).with_credentials(client_credentials)

    def __del__(self) -> None:
        """
        DTOR for the SharePoint class
        """
        del self.__ctx

    def list_all_files_in_folder(self, folder_relative_url: str) -> list:
        """
        lists all files in specific folder
        relative URL from: /sites/{group_site_name}/Shared Documents/{folder_relative_url}
        returns list of office365 objects, to get file names, iterate and use file.name
        """
        group_site_name = self.__team_info["group_site_name"]
        relative_url = f"/sites/{group_site_name}/Shared Documents/{folder_relative_url}"

        items = self.__ctx.web.get_folder_by_server_relative_url(relative_url).files
        self.__ctx.load(items)
        self.__ctx.execute_query()

        log.info("share point folder: %s", relative_url)

        for file in items:
            log.info(file.name)

        return items

    def get_all_files_recursively(self, folder_relative_url: str, out_file_list: list, extension_filter: str) -> None:
        """
        lists all folders in specific folder
        relative URL from: /sites/{group_site_name}/Shared Documents/{folder_relative_url}
        update out_file_list, format: list of office365 objects
        """
        group_site_name = self.__team_info["group_site_name"]
        relative_url = f"/sites/{group_site_name}/Shared Documents/{folder_relative_url}"

        # list all files
        items = self.__ctx.web.get_folder_by_server_relative_url(relative_url).files
        self.__ctx.load(items)
        self.__ctx.execute_query()
        for file in items:
            if extension_filter and str(file.name).lower().endswith(extension_filter.lower()):
                log.info("adding file: %s", file.serverRelativeUrl)
                out_file_list.append(file)

        # list all folders
        items = self.__ctx.web.get_folder_by_server_relative_url(relative_url).folders
        self.__ctx.load(items)
        self.__ctx.execute_query()

        # recursive call for same func again with new folder
        for folder in items:
            relative_path = f"{folder_relative_url}/{folder.name}"
            log.info("share point folder: %s", relative_path)
            self.get_all_files_recursively(relative_path, out_file_list, extension_filter)

    def download_file(self, file_obj: object, root_folder: str) -> str:
        """
        downloads specific file to root_folder
        file is office365
        file names are preserved, but spaces are replaced by "_"
        returns the file path
        """
        # replace spaces by underscores (just since we don't like spaces as file names)
        file_name = file_obj.name.replace(" ", "_")
        file_path = os.path.join(root_folder, file_name)

        log.info("downloading: %s ..", file_name)

        with open(file_path, "wb") as local_file:
            file = self.__ctx.web.get_file_by_server_relative_url(file_obj.serverRelativeUrl)
            file.download(local_file)
            self.__ctx.execute_query()

        # download is complete
        return file_path

    def download_all_files_in_folder(self, folder_relative_url: str, regex_filter: str, root_folder: str) -> list:
        """
        downloads all files in folder to root_folder
        downloads only files whose name matches regex_filter (if no filter is provided - download all)
        returns list of files full path
        """
        # list of downloaded files
        file_path_list = []

        # get all files
        files = self.list_all_files_in_folder(folder_relative_url)

        for file in files:
            # filter
            orig_file_name = file.name
            if regex_filter and not re.match(regex_filter, orig_file_name, re.IGNORECASE):
                # file doesn't match the requested filter, skip
                log.info("ignored file due to regex: %s", orig_file_name)
                continue

            file_path_list.append(self.download_file(file, root_folder))

        return file_path_list

    def get_date_format(self) -> str:
        """
        returns the sharepoint time format, used by all DATE fields
        """
        return self.__date_format
