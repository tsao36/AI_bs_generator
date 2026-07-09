"""
An API for communicating with Jira
"""

import logging
import enum
import json
import os
import re
import sys
import shutil
from typing import Union
from datetime import datetime
from dateutil import parser as dateparser
from jira import JIRA
from jira.resources import Issue
from jira.exceptions import JIRAError
import requests

# pylint: disable=wrong-import-position
sys.path.append(os.path.dirname(__file__))
import Sherlock
import DatabaseAPI

# pylint: enable=wrong-import-position


log = logging.getLogger("jira_api")
CA_BUNDLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jira-certificates.crt")


class Field(enum.Enum):
    """JIRA definitions of fields"""

    PROJECT = "project"
    ISSUE_TYPE = "issuetype"
    SUMMARY = "summary"
    DESCRIPTION = "description"
    COMPONENTS = "components"
    VERSIONS = "versions"
    RESOLVED = "resolved"
    UPDATED = "updated"
    FIX_VERSION = "fixVersions"  # Required for setting "Implement"
    ROOT_CAUSE = "customfield_10259"  # Required for setting "Implement"
    WHAT_CHANGE = "customfield_10306"  # Required for setting "Implement"
    FIX_IN_BUILD = "customfield_10209"  # Required for setting "Verify"
    IMPLEMENTED_DATE = "customfield_10575"
    FOUND_IN_BUILD = "customfield_10215"
    EXPOSURE = "customfield_10252"
    PLATFORM = "customfield_10242"
    TEAM = "customfield_10299"
    OS = "customfield_10277"
    HW = "customfield_10223"
    FOUND_BY = "customfield_10224"
    STATE_REASON = "customfield_10218"
    LABELS = "labels"
    PRIORITY = "priority"
    STATUS = "status"
    ASSERT_ERROR = "customfield_22500"
    CYBER_CLASSIFICATION_DEBUG = "customfield_24308"
    CYBER_CLASSIFICATION = "customfield_24103"
    ICA_ID = "customfield_25200"
    UNIT = "customfield_10553"
    PII = "customfield_23705"
    ACCUMULATION_TYPE = "customfield_24502"
    EXTERNAL_DOCUMENTATION = "customfield_24100"
    ISSUE_LINKS = "issuelinks"
    TELEMETRY_LEVEL = "customfield_23700"
    SUSPECTED_HARDWARE = "customfield_28007"
    REQUEST_TYPE = "customfield_14300"
    URGENCY = "customfield_10583"


RESOLUTION_FIELDS = [Field.RESOLVED.value, Field.IMPLEMENTED_DATE.value, Field.UPDATED.value]
# The fields that denote the resolution date of a ticket, ordered from most to least important


class FoundBy(enum.Enum):
    """JIRA definitions of 'Found By' fields"""

    PERIODIC_NIGHTLY = "Periodic Nightly"
    CCSV_PROGRAM = "CCSV Program"
    MSFT_SELF_HOSTING = "Microsoft Self Hosting"


class Platform(enum.Enum):
    """JIRA definitions of 'platform' fields"""

    AGNOSTIC = "Agnostic"
    ALDER_LAKE = "Alder Lake"
    AMD = "AMD Ryzen"
    ARROW_LAKE = "Arrow Lake"
    BROADWELL = "Broadwell"
    COFFEE_LAKE = "Coffee Lake"
    HASWELL = "Haswell"
    ICE_LAKE = "Ice Lake"
    KABY_LAKE = "Kaby Lake"
    LUNAR_LAKE = "Lunar Lake"
    METEOR_LAKE = "Meteor Lake"
    RAPTOR_LAKE = "Reptor Lake Client Platforms"
    SKY_LAKE = "Skylake"
    TIGER_LAKE = "Tiger Lake"
    WHISKEY_LAKE = "Whiskey Lake"


class Exposure(enum.Enum):
    """JIRA definitions of 'exposure' fields"""

    CRITICAL = "1-Critical"
    HIGH = "2-High"
    MEDIUM = "3-Medium"


class Os(enum.Enum):
    """JIRA definitions of 'os' fields"""

    LINUX = "Linux"
    WINDOWS10_RS1 = "Windows 10 RS1"
    WINDOWS10_RS5 = "Windows 10 RS5"
    WINDOWS10_19H1 = "Windows 10 19H1"
    WINDOWS10_19H2 = "Windows 10 19H2"
    WINDOWS10_20H1 = "Windows 10 20H1"
    WINDOWS10_20H2 = "Windows 10 20H2"
    WINDOWS10_21H1 = "Windows 10 21H1"
    WINDOWS10_21H2 = "Windows 10 21H2"
    WINDOWS10_22H2 = "Windows 10 22H2"
    WINDOWS11_21H2 = "Windows 11 21H2"
    WINDOWS11_22H2 = "Windows 11 22H2"
    WINDOWS11_23H2 = "Windows 11 SV2 Feb23"
    WINDOWS11_24H2 = "Windows 11 24H2"
    WINDOWS11_25H2 = "Windows 11 25H2"
    WINDOWS11_26H2 = "Windows 11 26H2"


class Team(enum.Enum):
    """JIRA definitions of 'team' fields"""

    DEV = "Development"
    VALIDATION_CCSV = "Validation WPIV -> CCSV”"


class Priority(enum.Enum):
    """JIRA definitions of 'priority' fields"""

    P1 = "P1-Stopper"
    P2 = "P2-High"
    P3 = "P3-Medium"
    P4 = "P4-Low"


class StateReason(enum.Enum):
    """JIRA definitions of 'state reason' fields"""

    NEW = "New"
    SIGHTING = "Open->Sighting"


class Jira:
    """JIRA class that will handle tickets"""

    def __init__(self, is_test_server=False, project="WIFI"):
        """
        initialize function

        Args:
        - is_test_server: True to work with test (staging) JIRA server
        - project: the jira project to use, e.g WIFI, BT, SKUMAP (needed for certain APIs).
                   Defaults to "WIFI".
        """
        self.__project = project
        self.__is_test_server = is_test_server
        self.__jira = None
        self.__server = None
        try:
            self.__jira, _, self.__server = self.__connect_to_jira()
        except Exception as exp:
            log.error(
                "Jira failed to connect: %s - another attempt will be made next time the API is accessed!", str(exp)
            )

    def get_jira(self):
        """Returns the JIRA instance.
        If the instance is not already created, it will create a new one.

        Returns:
            JIRA: The Jira instance
        """
        if self.__jira:
            return self.__jira
        log.info("JIRA instance not previously initialized, attempting a new connection")
        self.__jira, _, self.__server = self.__connect_to_jira()
        return self.__jira

    def __connect_to_jira(self):
        """
        Open new Jira connection

        Returns:
            JIRA, Session, str: The JIRA instance, the requests session, and the server URL
        """
        # set the SSL certificate to the update one
        jira_server = Sherlock.Jira.server if not self.__is_test_server else Sherlock.Jira.staging_server
        jira_auth = (Sherlock.Jira.username, Sherlock.Jira.password)
        jira_options = {"server": jira_server, "verify": CA_BUNDLE}
        try:
            jira = JIRA(options=jira_options, basic_auth=jira_auth, max_retries=0)  # No need to retries with lazy-load
        except JIRAError as exp:
            log.error("failed to connect to jira, code=%s, message=%s", exp.status_code, str(exp))
            raise
        else:
            log.info("authentication to JIRA succeeded")

        session = requests.Session()
        # set the SSL certificate for the session object
        session.verify = CA_BUNDLE
        # set the the username and password for requests
        session.auth = jira_auth

        return jira, session, jira_server

    @classmethod
    def _get_program_version(cls, driver_version):  # Used in BSOD Parser.
        """
        function returns program version in JIRA format
        e.g 99.0.61.4 --> REL_99.0.61
        """
        match = re.findall(r"(\d+)\.(\d+)\.(\d+).", driver_version)
        if match:
            return f"REL_{match[0][0]}.{match[0][1]}.{match[0][2]}"

        # NA was chosen since exist in the DB
        return "NA"

    @classmethod
    def _copy_logs(cls, jira_id: str, src_logs_path: str, dst_logs_path: str) -> str:
        """
        function copies logs (dumps, wpp/rlg, etc) into shared folder
        returns the destination folder
        """
        # generate new path comprising bug ID
        new_path = os.path.join(dst_logs_path, jira_id)
        # create new folder if needed
        if not os.path.exists(new_path):
            os.makedirs(new_path)

        # create a suffix in case bug will be updated several times
        suffix = datetime.now().strftime("%d_%m_%Y_%H_%M_%S_%f")

        # copy all files and return the new directory
        return shutil.copytree(src_logs_path, os.path.join(new_path, suffix))

    @classmethod
    def __get_blazar_hw_jira_str(cls, acronym):
        """
        find the right jira mapping for Blazar + acronym
        """
        acronyms_dict = {
            "FmP2": "Fillmore Peak 2 Blazar (BE201)",
            "GfP2": "Garfield Peak 2 Blazar (AX211)",
            "GfP4": "Garfield Peak 4 Blazar (AX411)",
            # there is a typo in JIRA calling "blazar" (galaxic jet) a "blazer" (jacket)
            "HrP1": "Harrison Peak 1 Blazer (AX101)",
            "HrP": "Harrison Peak 2 Blazer (AX201)",
            "HrP2": "Harrison Peak 2 Blazer (AX201)",
        }
        return acronyms_dict[acronym]

    @classmethod
    def __get_magnetar_hw_jira_str(cls, acronym):
        """
        find the right jira mapping for Magnetar + acronym
        """
        acronyms_dict = {
            "GfP2": "Garfield Peak 2 Magnetar (AX211)",
            "GfP4": "Garfield Peak 4 Magnetar (AX411)",
            "HrP1": "Harrison Peak 1 Magnetar (AX101)",
            "HrP": "Harrison Peak 2 Magnetar (AX201)",
            "HrP2": "Harrison Peak 2 Magnetar (AX201)",
        }
        return acronyms_dict[acronym]

    @classmethod
    def __get_solar_hw_jira_str(cls, acronym):
        """
        find the right jira mapping for Solar + acronym
        """
        acronyms_dict = {
            "GfP2": "Garfield Peak 2 Solar (AX211)",
            "GfP4": "Garfield Peak 4 Solar (AX411)",
            "HrP1": "Harrison Peak 1 Solar (AX101)",
            "HrP": "Harrison Peak 2 Solar (AX201)",
            "HrP2": "Harrison Peak 2 Solar (AX201)",
            "JfP1": "Jefferson Peak 1 Solar (9461)",
            "JfP": "Jefferson Peak 2 Solar (9560)",
            "JfP2": "Jefferson Peak 2 Solar (9560)",
            "JnP": "Johnson Peak 2 Solar (AX203)",
            "MsP2": "Madison Peak 2 Solar (AX204)",
        }
        return acronyms_dict[acronym]

    @classmethod
    def __get_quasar_hw_jira_str(cls, acronym):
        """
        find the right jira mapping for quasar + acronym
        """
        acronyms_dict = {
            "JfP1": "Jefferson Peak 1 Quasar (9461)",
            "JfP2": "Jefferson Peak 2 Pulsar (9560)",
            "JfP": "Jefferson Peak 2 Quasar (9560)",
            "HrP1": "Harrison Peak 1 (AX101)",
            "HrP": "Harrison Peak 2 (AX201)",
        }
        return acronyms_dict[acronym]

    @classmethod
    def __get_pulsar_hw_jira_str(cls, acronym):
        """
        find the right jira mapping for pulsar + acronym
        """
        acronyms_dict = {
            "JfP1": "Jefferson Peak 1 Pulsar (9461)",
            "JfP2": "Jefferson Peak 2 Pulsar (9560)",
            "JfP": "Jefferson Peak 2 Pulsar (9560)",
        }
        return acronyms_dict[acronym]

    @classmethod
    def __get_discrete_hw_jira_str(cls, device_id):
        """
        find the right jira mapping for discrete HW according to device id
        """
        acronyms_dict = {
            "24fb": "Sandy Peak (3168)",
            "3165": "Stone Peak 1 (3165)",
            "3166": "Stone Peak 1 (3165)",
            "095a": "Stone Peak 2 (7265)",
            "095b": "Stone Peak 2 (7265)",
            "24fd": "Windstorm Peak (8265)",
            "24f3": "Snowfield Peak (8260)",
            "24f5": "Snowfield Peak (8260)",
            "24f6": "Snowfield Peak (8260)",
            "2723": "Cyclone Peak 2 (22260)",
            "272b": "Gale Peak 2 (BE200)",
            # "272b": "Misty Peak 2 (XXXX)", cannot be added, since having same device ID as GaP
            "2526": "Thunder Peak 2 (9260)",
            "2725": "Typhoon Peak 2 (AX210)",
        }
        return acronyms_dict[device_id]

    def _get_hw(self, device_id, subsys_id):
        """
        function will return the right device to be used by Jira
        TODO: need to refactor this code. best way would be getting from JIRA
              all fields of "Hardware" list and search inside using keywords

        Args:
            device_id(string)
            subsys_id(string)
        """
        hw_str = "Unassigned"

        # open handle to SQL DB
        db_mng = DatabaseAPI.DBConnector(
            Sherlock.Database.server, Sherlock.Database.database, Sherlock.Database.username, Sherlock.Database.password
        )

        # query for the device according to the device id and subsys id
        search = f"wifi_pci_device_id = '{device_id}' AND wifi_pci_sub_system_id = '{subsys_id}'"
        select = "distinct platform_silicon, acronym, sku_type"
        data = db_mng.query_table(table="jira_sku", select=select, where=search)

        # convert to jira string
        device_id = device_id.lower()
        try:
            if data[0]["sku_type"] == "Discrete":
                hw_str = self.__get_discrete_hw_jira_str(device_id)
            elif data[0]["platform_silicon"] == "Blazar":
                hw_str = self.__get_blazar_hw_jira_str(data[0]["acronym"])
            elif data[0]["platform_silicon"] == "Magnetar":
                hw_str = self.__get_magnetar_hw_jira_str(data[0]["acronym"])
            elif data[0]["platform_silicon"] == "Solar":
                hw_str = self.__get_solar_hw_jira_str(data[0]["acronym"])
            elif data[0]["platform_silicon"] == "Quasar":
                hw_str = self.__get_quasar_hw_jira_str(data[0]["acronym"])
            elif data[0]["platform_silicon"] == "Pulsar":
                hw_str = self.__get_pulsar_hw_jira_str(data[0]["acronym"])
        except (KeyError, IndexError):
            log.info("didn't find device_id_%s subsys_id_%s, setting hw to 'Unassigned'", device_id, subsys_id)
            hw_str = "Unassigned"

        return hw_str

    @classmethod
    def _generate_logs_link(cls, logs_path: str):
        """
        function generates link in JIRA format
        """
        return f"[{logs_path}|{logs_path}]"

    @classmethod
    def _generate_json_section(cls, dict_to_upload: dict):
        """
        function generates json section in JIRA format
        """
        return f"{{code:json}}{json.dumps(dict_to_upload, indent=4)}{{code}}"

    def update_existing_bug(self, dict_to_upload: dict, src_logs_path: str, jira_id: str, dst_logs_path: str):
        """
        function updates an existing jira bug with additional debug data
        """
        ticket = self.get_jira().search_issues(f"{Field.PROJECT.value}=WIFI AND key={jira_id}")
        if ticket:
            logs_path = self._copy_logs(jira_id, src_logs_path, dst_logs_path)
            res = self.get_jira().add_comment(
                ticket[0], self._generate_logs_link(logs_path) + "\n\n" + self._generate_json_section(dict_to_upload)
            )
            if res and res.id:
                log.info("jira was updated successfully: %s", jira_id)
            else:
                log.error("failed to add comment: %s", jira_id)
        else:
            log.error("jira %s was not found", jira_id)

    def does_version_exist(self, version: str) -> bool:
        """
        returns True in case version exists in current project
        """
        version_list = self.get_jira().project_versions(self.__project)
        return any(filter(lambda v: v.name.lower() == version.lower(), version_list))

    def create_new_version(self, version: str) -> None:
        """
        creates a new version in JIRA
        """
        if not self.does_version_exist(version):
            log.info("version %s doesn't exist in JIRA, create it", version)
            self.get_jira().create_version(name=version, project=self.__project)

    def file_new_issue(self, issue_data: dict):
        """Opens a new Jira issue of type Bug/Task/Ticket.

        Args:
            issue_data (dict): A dictionary containing all the required Jira fields for opening the issue.

        Raises:
            ValueError: If the issue type is not "Bug", "Task" or "Ticket".

        Returns:
            Issue: The created issue object.
        """
        if issue_data["issuetype"]["name"] not in ("Bug", "Task", "Ticket"):
            raise ValueError("Issue type must be a 'Bug', 'Task' or 'Ticket'!")

        if issue_data["issuetype"]["name"] == "Bug":
            # make sure that "Fixed version" exists, otherwise bug creation will fail
            # if doesn't exist, create
            for ver_obj in issue_data[Field.VERSIONS.value]:
                jira_ver = ver_obj["name"]
                self.create_new_version(jira_ver)

        return self.get_jira().create_issue(issue_data)

    def post_comment(self, issue_key, message):
        """Posts a message to the comments of a JIRA key.
        Just a forwarder to the native `add_comment` API.
        See details here: https://developer.atlassian.com/cloud/jira/platform/rest/v2/api-group-issue-comments/
        Args:
            issue_key (str): The issue key to post to (e.g. WIFI-123456)
            message (str): The message to post

        Returns:
            A "Comment" object as specified by the API.
        """
        return self.get_jira().add_comment(issue_key, message)

    def get_valid_statuses(self, issue_key):
        """Get all valid statuses for the give issue.

        Args:
            issue_key (str): The issue key to get valid statuses of (e.g. WIFI-123456)

        Returns:
            dict: A dictionary where the key is the issue name and the value its ID.
                  e.g. {"Done": 4, "Pending": 3}
        """
        transitions = self.get_jira().transitions(issue_key)
        return {x["name"]: x["id"] for x in transitions}

    def get_program_version(self, driver_version):
        """
        function returns program version in JIRA format
        e.g 99.0.61.4 --> REL_99.0.61

        Args:
            driver_version(str): the driver version to convert

        Returns:
            str: the program version in JIRA format (3 nibbles)
        """
        return Jira._get_program_version(driver_version)

    def add_attachment(self, issue_key, attachment_path):
        """Add an attachment to a JIRA issue.

        Args:
            issue_key (str): The issue key to add the attachment to (e.g. WIFI-123456)
            attachment_path (str): The path to the attachment file
        """
        log.info("Adding attachment %s to issue %s", attachment_path, issue_key)
        return self.get_jira().add_attachment(issue_key, attachment_path)

    def get_issue(self, issue_key):
        """Gets an issue.
        Simply a forwarder to the issue object

        Args:
            issue_key (str): The issue key to get a status for (e.g. WIFI-123456)

        Returns:
            Issue: The requested issue if found.
        """
        return self.get_jira().issue(issue_key)

    def delete_attachment(self, attachment_id):
        """Delete an attachment from a JIRA issue."""
        return self.get_jira().delete_attachment(attachment_id)

    def get_project_issues(
        self,
        project_name=None,
        feature_id=None,
        namespace=None,
        maturity_number=None,
        current_core=None,
        custom_filter=None,
    ):
        """Giving all telemetry jira issues for the given project.

        Args:
            project_name (_type_): _description_
            feature_id (_type_): _description_

        Returns:
            _type_: _description_
        """
        if custom_filter:
            jql = f"{custom_filter}"
        else:
            if project_name:
                jql = f"project = {project_name}"
            if feature_id:
                jql += f" AND 'Feature ID' = {feature_id}"
            if namespace:
                jql += f" AND 'Namespace' = {namespace}"
            if maturity_number:
                jql += f" AND 'Maturity Number' >= {maturity_number}"
            if current_core:
                jql += f" AND ('fixVersion' < {current_core}"
                jql += " OR 'fixVersion' IS EMPTY)"

        return self.__jira.search_issues(jql, maxResults=False)

    def get_status(self, issue_key):
        """Gets an issue's status.
        Simply a forwarder to the issue's status field

        Args:
            issue_key (str): The issue key to get a status for (e.g. WIFI-123456)

        Returns:
            str: The current status of the field
        """
        return self.get_jira().issue(issue_key).fields.status.name

    def set_status(self, issue_key, status, fields=None):
        """Sets the status of the JIRA Issue.
        Just a forwarder (with a basic validator) for the native 'transition_issue' API.

        Args:
            issue_key (str): The issue key to post to (e.g. WIFI-123456)
            status (str): The status to set - by name. (e.g. "Done", "Pending", etc.)
                             Note that each issue and project might have their own statuses.
                             You can get a list of possible statuses using the `get_valid_statuses` function.
            fields (dict): Change the fields specified by this dict.
                           Note that some statuses require certain fields to be changed.

        Raises:
            StatusNotSupported: If you try to set a status that is not supported by this issue at this time.
        """
        valid_statuses = self.get_valid_statuses(issue_key)

        if self.get_status(issue_key) == status:
            log.warning("Tried to set status %s for issue %s but it was already set!", status, issue_key)
            return

        if status not in valid_statuses:
            raise StatusNotSupported(issue_key, status, list(valid_statuses.keys()))

        self.get_jira().transition_issue(issue_key, valid_statuses[status], fields=fields)

    def get_field_from_issue(self, issue: Issue, field_name: str):
        """Gets the value of a given field from a given JIRA Issue object.
        Does *not* query the API, use this when querying multiple fields from a single ticket to save time and traffic.
        Not recycling the `get_field_value` code since it also returns results that look a little different.

        Args:
            issue (Issue): The jira Issue object.
            field_name (str): The field to query.

        Returns:
            str | None: The field value or None if not found
        """
        if not hasattr(issue.fields, field_name):
            return None
        return issue.get_field(field_name)

    def get_field_value(self, issue_key, field_name):
        """Gets the value of a given field from a JIRA Ticket.
        Prompts the API for the JIRA issue each time.
        If you want to save time and run this function on an already prompted JIRA Issue, use `get_field_from_issue`.

        Args:
            issue_key (str): The issue key to get the field from (e.g. WIFI-123456)
            field_name (str): Name of the field to get the value of (e.g. customfield_12345)

        Returns:
            str: The value of the field, or None if not found or empty
        """
        issue = self.get_jira().issue(issue_key)

        if not self.field_exists(issue_key, field_name):
            return None

        field = issue.get_field(field_name)
        return field.value if field else None

    def field_exists(self, issue_key, field_name) -> bool:
        """Checks whether a field exists in a certain JIRA Ticket

        Args:
            issue_key (str): The issue key to check for (e.g. WIFI-123456)
            field_name (str): Name of the field to check (e.g. customfield_12345)

        Returns:
            bool: Whether the field exists or not
        """
        issue = self.get_jira().issue(issue_key)
        return hasattr(issue.fields, field_name)

    def get_fixed_tickets_for_version(self, version: str) -> list:
        """Returns a list of all JiraTicket objects that have a Fixed Version matching the one provided, and have a
        status of Verify, Implemented, or Closed, and a "State Reason" of "Fixed".
        Sorts results by resolution date (see `get_resolution_date` documentation for details).

        Args:
            version (str): The program version to get tickets for.
                           Accepts both `XX.YY.ZZ` and `REL_XX.YY.ZZ`.

        Raises:
            VersionDoesNotExist: If the supplied version does not exist.

        Returns:
            list: A list of all jira.resources.Issue objects (https://jira.readthedocs.io/api.html#jira.resources.Issue)
        """
        if not version.upper().startswith("REL_"):
            version = f"REL_{version}"
        if not self.does_version_exist(version):
            raise VersionDoesNotExist(version)
        query = (
            f"Project=WIFI AND fixVersion ~ {version}"
            ' AND "State reason" = Fixed AND status IN (Verify, Implemented, Closed)'
        )

        results = self.get_jira().search_issues(query, maxResults=False)
        results = sorted(results, key=self.get_resolution_date)
        return list(results)

    def get_resolution_date(self, jira_issue: Issue) -> datetime:
        """Gets the resolution date of a resolved issue.
        Attempts to get the highest importance field value if it exists, defaulting to a lesser importance field each
        time if it doesn't.

        For example, if a ticket has a "resolved" field, it will use that.
        If not, it will try "implemented on".
        If that doesn't exist either, will use the "updated" field.
        Finally, if none exist, it will raise an exception.

        This function is useful for sorting, but keep in mind that

        Args:
            jira_issue (Issue): The Jira Issue to get resolution date for

        Raises:
            NoResolutionDate: If the given JIRA issue did not have any of the expected fields.

        Returns:
            datetime: The resolution date
        """
        for field in RESOLUTION_FIELDS:
            if resolution := self.get_field_from_issue(jira_issue, field):
                return dateparser.parse(resolution)
        raise NoResolutionDate(jira_issue)

    def get_url(self, jira_issue: Union[Issue, str]) -> str:
        """Returns the browsable URL of the issue in JIRA, depending on which server the API has queried.

        Args:
            jira_issue (Issue | str): Can extract URL either from an issue object or a Jira Ticket ID

        Returns:
            str: The URL
        """
        if isinstance(jira_issue, str):
            return f"{self.__server}browse/{jira_issue}"
        return f"{self.__server}browse/{jira_issue.key}"

    def add_link(self, jira_issue: Union[str, Issue], title: str, url: str):
        """Add a link to a JIRA ticket.

        Args:
            jira_issue (str|Issue): The Jira Issue (or Issue Key) to add the link to
            title (str): The link title
            url (str): The link URL
        """
        if isinstance(jira_issue, str):
            jira_issue = self.get_issue(jira_issue)
        self.get_jira().add_remote_link(jira_issue, {"title": title, "url": url})


class NoResolutionDate(Exception):
    """A custom exception for when trying to determine a resolution date for a ticket but none of the fields exists"""

    def __init__(self, jira_issue: Issue):
        self.issue = jira_issue
        super().__init__(
            f"Couldn't determine resolution date for {jira_issue.key}! "
            f"None of the expected fields ({', '.join(RESOLUTION_FIELDS)}) exist in the ticket!"
        )


class VersionDoesNotExist(Exception):
    """A custom exception for when trying to query a version that does not exist"""

    def __init__(self, version):
        self.version = version
        super().__init__(f"Version {version} does not exist!")


class StatusNotSupported(Exception):
    """A custom exception for when trying to set a status that is not supported by the JIRA"""

    def __init__(self, jira_id, status, supported_statuses):
        self.jira_id = jira_id
        self.status = status
        self.supported_statuses = supported_statuses
        super().__init__(
            f'JIRA Issue {jira_id} does not support status "{status}" at this time! '
            f"Supported statuses are: {self.supported_statuses}"
        )
