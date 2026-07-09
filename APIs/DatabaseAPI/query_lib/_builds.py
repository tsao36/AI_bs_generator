""" Build related queries """

import os
import re
from datetime import datetime
from typing import TYPE_CHECKING, List
from .._exceptions import NoResultsForQuery
from ._definitions import CoreNotFoundError

if TYPE_CHECKING:
    from .. import DBConnector

BUILD_TABLES = ["msiBuild", "driverBuild", "wapiBuild", "uscBuild", "attestationBuild", "devopsBuild"]
NIGHTLY_PURPOSES = ["nightly", "nightly_release", "pv_build_nightly"]
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

"""A common WHERE query used to identify nightly build (without filtering the state)"""
NIGHTLY_WHERE_ALL_STATES = (
    "build_purpose IN (" + ",".join([f"'{purpose}'" for purpose in NIGHTLY_PURPOSES]) + ") AND hide_from_tracker = 0"
)

"""A common WHERE query used to identify successful or unstable nightly builds"""
NIGHTLY_WHERE = NIGHTLY_WHERE_ALL_STATES + " AND state in ('SUCCESS', 'UNSTABLE')"


class Builds:
    """Builds related queries"""

    def __init__(self, db: "DBConnector"):
        self.db = db

    def get_build(self, build_id: str, build_table=None) -> dict:
        """Returns the DB row associated with the provided driver build ID as a dictionary.

        Args:
            build_id (str): The build ID
            build_table (str|list): The table (or tables) to look in.

        Returns:
            dict: The build details.

        Raises:
            BuildNotFoundException if the driver was not found.
        """
        if not build_table:
            build_table = BUILD_TABLES

        build_data = self.db.get_single_row(table=build_table, primary_key="build_id", row_id=build_id)
        if not build_data:
            raise BuildNotFoundException(build_id)

        # Addons
        ## Reports
        build_data = self.add_reports(build_data)

        ## Attestation
        if build_data.get("build_type") == "msi":
            attestation_layouts = self.db.query_table(
                "attestationBuild", "zip_listener_path", f"msi_tic = '{build_id}' AND state = 'SUCCESS'"
            )
            build_data["attestation_layout"] = (
                ",".join(x["zip_listener_path"] for x in attestation_layouts) if attestation_layouts else None
            )

        # Adding the table name
        build_data["table"] = Builds.get_table_from_build_type(build_data["build_type"])
        return self.add_reports(build_data)

    def get_core_from_build(self, build_id: str) -> str:
        """Extracting the core number of a build.

        Args:
            build_id (str): The build ID to search

        Returns:
            str: The core number of the build.

        Raises:
            CoreNotFoundError if the core was not found.
        """
        build_row = self.db.builds.get_build(build_id)
        core_def = self.db.definitions.get_core_definition(build_row["drv_branch"])
        if not core_def:
            raise CoreNotFoundError(core_name=build_row["drv_branch"], message=f"Core not found for build {build_id}")
        drv_branch = core_def["drv_branch"]
        if drv_branch == "master":
            return drv_branch
        match = re.search(r"^CoreCycle(\d+)", drv_branch)
        if match:
            return match.group(1)
        raise CoreNotFoundError(
            core_name=drv_branch, message=f"Core number not found for build {build_id} and branch {drv_branch}"
        )

    def add_reports(self, build_data: dict) -> dict:
        """Returns a version of the build data dict with reports.
        Reports will appear as nested dictionary under the `reports` key, e.g.:

        ```json
        {
            //...
            "build_id": "foo...",
            "reports": {"protex": "...", "coverity": "..."}
            //...
        }
        ```


        Args:
            build_data (dict): The build data without reports. If the build data already has reports in it, they will be
                               overwritten.

        Returns:
            dict: The build data with reports.
        """
        reports = self.db.get_single_row("build.reports", "build_id", build_data["build_id"])

        # Reports Transform
        # After migration to reports table is complete, replace with:
        # build_data["reports"] = reports
        build_data["reports"] = {
            "coverity": reports.get("coverity"),
            "protex": reports.get("protex") or build_data.get("protex_report"),
        }
        return build_data

    def get_driver_build(self, driver_build_id: str) -> dict:
        """Forwarder for `get_build` with just the driver_build table"""
        return self.get_build(driver_build_id, "driverBuild")

    def get_latest_drv_nightly(self, branch="master", release=False, is_filtered_by_states=True, order_by=None):
        """Get the latest successful or unstable nightly driver build.

        Args:
            branch (str, optional): The branch to get the nightly from. Defaults to "master".
            release (bool): Only search release nightlies. Defaults to False.
            is_filtered_by_state (bool, optional): If true, only show nightlies which are successful or unstable.
                                                   Defaults to True.
            order_by (str, optional): How to order the nightlies to determine which is the "latest".
                                      Defaults to `end_date` if `is_filtered_by_states` is `True`,
                                      or to `submission_date` if it's `False` (since `end_date` might not exist if the
                                      build is not yet finished).

        Returns:
            dict: The nightly results row.
        """

        drv_build_table = "driverBuild"
        if not order_by:
            order_by = "end_date" if is_filtered_by_states else "submission_date"

        release_operator = "=" if release else "<>"
        nightly_where = NIGHTLY_WHERE if is_filtered_by_states else NIGHTLY_WHERE_ALL_STATES
        query = f"drv_branch = '{branch}' AND {nightly_where} AND build_purpose {release_operator} 'nightly_release'"

        # Get latest build matching the query
        res = self.db.query_table(drv_build_table, order_by=order_by, where=query, limit=1)
        if not res:
            raise NightlyNotFound(branch)
        return self.add_reports(res[0])

    def get_previous_nightly(self, base_nightly_id, release=False):
        """For any given nightly build, return the one before it from the same branch.

        Args:
            base_nightly_id (str): The Build ID of the nightly to get the previous build for
            release (bool): Only search release nightlies. Defaults to False.

        Raises:
            NotANightly: If the given build ID does not belong to a nightly
            NightlyNotFound: If no nightlies are found for some reason

        Returns:
            dict: The nightly build row from the DB
        """
        drv_build_table = "driverBuild"
        base_nightly_build = self.get_build(base_nightly_id)
        if base_nightly_build.get("build_purpose") not in NIGHTLY_PURPOSES:
            raise NotANightly(base_nightly_id, base_nightly_build.get("build_purpose"))

        branch = base_nightly_build["drv_branch"]

        current_nightly_date = base_nightly_build.get("submission_date")

        query = f"drv_branch = '{branch}' AND {NIGHTLY_WHERE} AND submission_date < '{current_nightly_date}'"

        release_operator = "=" if release else "<>"
        query += f" AND build_purpose {release_operator} 'nightly_release'"

        # get latest build matching the query
        res = self.db.query_table(drv_build_table, order_by="end_date", where=query, limit=1)
        if not res:
            # No results from query
            raise NightlyNotFound(branch)
        return res[0]  # The first result will always be the previous nightly

    def get_previous_nightly_coverage_directory(self, current_build_id):
        """Get the previous nightly build's code coverage directory path.
        To differentiate "light" nighties (e.g. Cobalt), only nighties that ran UT are selected.

        Args:
            current_build_id (str): The build_id to ignore.

        Returns:
            code_coverage_path (str): the path to the previous nightly build's code coverage directory path
        """

        previous_nightly = self.get_previous_nightly(current_build_id, release=False)
        if not previous_nightly["ut_layout"]:
            # The recent Nightly does not have the "ut_layout" path in database
            raise CodeCoverageReportNotFound(previous_nightly["build_id"])
        code_coverage_path = os.path.join(previous_nightly["ut_layout"], "CodeCoverage")
        if not os.path.exists(code_coverage_path):
            # There is no valid path to the code coverage report of the last Nightly build
            raise CodeCoverageReportNotFound(previous_nightly["build_id"])
        return code_coverage_path

    def get_nightlies_from_fw(self, drv_branch: str, fw_sha1: str) -> List[dict]:
        """Given a branch and a FW SHA1, get DRV SHA1 (one or more) that ran with that FW SHA1.

        Args:
            drv_branch (str): The name of the driver branch the nightly ran for
            fw_sha1 (str): The FW SHA1 the nightly ran with

        Raises:
            NightlyNotFound: In case there is no nightly in the supplied branch tagged with the supplied FW SHA1.

        Returns:
            list[dict]: The list of nightly entries that match the given branch FW SHA1.
        """
        query = f"drv_branch = '{drv_branch}' AND fw_sha1 = '{fw_sha1}'"
        results = self.db.query_table("nightly.sha1_matching", where=query, order_by="timestamp", order_dir="DESC")
        if not results:
            raise NightlyNotFound(drv_branch, fw_sha1)
        return results

    def get_driver_build_property(self, driver_build_id: str, property_id):
        """Returns the value of a specific property (column) in a driver build

        Args:
            driver_build_id (str): The Build ID
            property_id (str|list): The name of the column (property) to return

        Raises:
            BuildNotFoundException if the driver was not found.
            KeyError: If the column ID doesn't exist in the driver properties.

        Returns:
            any | list: Either the value of the property if a single ID was provided or a list of them if multiple were.
        """
        driver = self.get_driver_build(driver_build_id)
        if isinstance(property_id, str):
            return driver[property_id]
        if isinstance(property_id, list):
            return [driver[x] for x in property_id]
        raise TypeError(f"property_id must be a string or list, not {type(property_id).__name__}")

    def get_build_sha1(self, build_id: str, build_table=None) -> str:
        """Returns the build sha1 for a certain build.

        Args:
            build_id (str): The build ID
            build_table (str|list): The table (or tables) to look in.

        Returns:
            str: The build sha1.

        Raises:
            Exception if the build is an msi build.
        """
        build_data = self.db.builds.get_build(build_id, build_table)

        match build_data["build_type"]:
            case "driver" | "cat_spin":
                return build_data["drv_sha1"]
            case "msi":
                raise Exception("There is no sha1 for MSI build")
            case "wapi":
                return build_data["wapi_sha1"]
            case "usc":
                return build_data["usc_sha1"]
            case _:
                raise NotSupportedBuildType("There is no such a build type!")

    def get_supported_hw_by_build(self, build_id):
        """Return a list of all supported hardwares based on build ID

        Args:
            build_id (str): The build ID to search

        Returns:
            list: List of supported hardwares
        """
        query = (
            "select supported_hw from coreDefinitions where drv_branch like "
            + f"(select drv_branch from driverBuild where build_id like '{build_id}')"
        )
        result = self.db.simple_query(query)
        if not result:
            raise NoResultsForQuery(query)
        return result[0][0].split(",")

    def get_retention_policy(self, build_id):
        """
        Gets the retention policy for the build ID.

        Args:
            build_id (str): The Build ID to query retention policy for

        Raises:
            RetentionPolicyNotDefined: If no policy was defined for this build in DB

        Returns:
            int: The retention policy

        """
        default_retention_policy_days = self.db.builds.get_default_retention_policy("default")
        retention_policy = (
            self.get_build(build_id).get("retention_policy", default_retention_policy_days)
            or default_retention_policy_days
        )
        if not retention_policy:
            raise RetentionPolicyNotDefined(build_id)
        return retention_policy

    def get_default_retention_policy(self, build_purpose):
        """
        Gets the default retention policy for the build purpose.

        Args:
            build_purpose (str): The Build purpose to query the default retention policy for

        Raises:
            BuildPurposeNotFound: If the retention policy specified does not exist in the database.

        Returns:
            int: The retention policy
        """
        retention_policy = self.db.get_single_row("build.build_purpose", "build_purpose", build_purpose)
        if not retention_policy:
            raise BuildPurposeNotFound(build_purpose)
        return retention_policy["art_retention_policy"]

    def get_tests_for_build(self, driver_build_id):
        """Get a list of UT test results for a given Build ID"""
        return self.db.simple_query(f"SELECT * FROM utResults WHERE build_id = '{driver_build_id}'", return_type=dict)

    def get_builds_in_date_range(
        self,
        start_date: datetime = None,
        end_date: datetime = None,
        build_purpose: str = None,
        only_finished: bool = True,
        branch: str = None,
    ):
        """Get all the PerCI builds in a given date range.

        Args:
            start_date (datetime): From when to query.
                                   Defaults to None, which means beginning of time.
                                   If not supplied, end_date must be supplied.
            end_date (datetime, optional): Until when to query.
                                           Defaults to none, which means the heat death of the universe.
                                           If not supplied, start_date must be supplied.
            build_purpose (str): Filter this build_purpose.
                                 Only supports one build purpose at a time.
                                 Defaults to None.
            only_finished (bool): Only show finished builds. Defaults to True.
            branch (str): Only display builds from this branch.
                          Currently only supports one branch at a time.
                          Defaults to None (no filter, show all brances).

        Returns:
            list: A list of dicts (each a build entry) matching the query.
        """
        if not any((start_date, end_date)):
            raise AttributeError("Either start_date, end_date, or both must be supplied!")
        where = ""
        if start_date:
            where += f"submission_date >= '{start_date.strftime(DATE_FORMAT)}'"
        if end_date:
            where += (" AND " if where else "") + f"submission_date <= '{end_date.strftime(DATE_FORMAT)}'"
        if branch:
            where += f" AND drv_branch = '{branch}'"
        if build_purpose:
            where += f" AND build_purpose = '{build_purpose}'"
        if only_finished:
            where += " AND state not in ('RUNNING', 'QUEUED')"
        where += "AND hide_from_tracker = 0"
        return self.db.query_table("driverBuild", where=where)

    def get_msi_from_eng(self, eng_tic):
        """Get an MSI Build based on an ENG tic

        Args:
            eng_tic (str): The ENG Tic to search

        Returns:
            dict: The build details
        """
        where = f"build_id in (SELECT internal_id FROM msiToEng WHERE [report_url] like '%{eng_tic}%')"
        msi_build = self.db.get_single_row("msiBuild", where=where)
        if not msi_build:
            raise BuildNotFoundException(eng_tic)
        return msi_build

    def msi_to_eng_query_builder(self, query, operator=None):
        """Generates the MSI TICs matching a given ENG query to a WHERE search

        Usage Examples:
        ```
        where = db.builds.msi_to_eng_query_builder(query)
        where += db.builds.msi_to_eng_query_builder(query, "OR")
        ```

        Args:
            query (str): The query as given by the user
            operator (str): "Whether to add using "AND" or "OR".
                            Leave blank for neither.

        Raises:
            ValueError: If the operator is neither "AND", "OR", or None

        Returns:
            str: The relevant query part -
                If no operator was supplied and a match was found, there will be no tailing or leading spaces. e.g.:
                `build_id in ('TIC1', 'TIC2')`
                 If an operator was specific, a leading space will be added. e.g.:
                 ` OR build_id in ('TIC1', TIC2')` but again no tailing space.
                If no match was found and no operator was specified, an always true statement will be returned (`TRUE`)
                If no match was found and an operator was specified, an empty string will be returned.
        """
        if re.match(r"(\w+WFW)?\d{4,5}", query) or re.match(r"\d{1,4}\.\d{1,4}\.\d{1,4}\.\d{1,4}", query):
            # Search MSI from Eng
            eng_results = self.db.query_table(table="msiToEng", where=f"report_url LIKE '%{query}%'")
            tics = []
            if not operator:
                operator = ""
            elif operator not in ["AND", "OR"]:
                raise ValueError(f'Operator can only be "AND", "OR", or None, not "{operator}"')
            else:
                operator = f" {operator} "  # Add space to operator
            for result in eng_results:
                if (match := re.search(r"TIC=(\S+)", result["report_url"])) and query in match.group(1):
                    tics.append(result["internal_id"])
            if tics:
                tics = [f"'{x}'" for x in tics]  # Wrap all values in single quotes
                return f"{operator}build_id IN ({','.join(tics)})"
        if not operator:
            return "TRUE"  # Need to add an empty condition or outer queries will become invalid
        return ""

    @staticmethod
    def get_table_from_build_type(build_type):
        """
        Args:
            build_type (str): The build type of the build.

        Raises:
            NotSupportedBuildType: In case the build type does not exist.

        Returns:
            str: The name of the table that the build type is mapped to.
        """
        match (build_type):
            case "driver" | "catspin":
                return "driverBuild"
            case "msi":
                return "msiBuild"
            case "attestation":
                return "attestationBuild"
            case "wapi":
                return "wapiBuild"
            case "usc":
                return "uscBuild"
            case "devops":
                return "devopsBuild"
            case _:
                raise NotSupportedBuildType(build_type)


class BuildNotFoundException(Exception):
    """Exception when trying to query information about a build that doesn't exist"""

    def __init__(self, build_id):
        self.build_id = build_id
        super().__init__(f'No build found that matches the query "{build_id}"')


class NightlyNotFound(Exception):
    """Exception when trying to query information about a nightly that doesn't exist"""

    def __init__(self, branch, fw_sha1=None):
        self.branch = branch
        self.fw_sha1 = fw_sha1
        fw_sha1_message = f'and FW SHA1 "{fw_sha1}" ' if fw_sha1 else ""
        super().__init__(f'Nightly for branch "{branch}" {fw_sha1_message}was not found in the database!')


class NotANightly(Exception):
    """Exception when trying to query information about a nightly but the given build that isn't a nightly"""

    def __init__(self, build_id, build_purpose):
        self.build_id = build_id
        self.build_purpose = build_purpose
        super().__init__(
            f"Build purpose of {build_id} is not one of the valid build-purposes for nightlies "
            f'({",".join(NIGHTLY_PURPOSES)}). It is "{build_purpose}"!'
        )


class CodeCoverageReportNotFound(Exception):
    """Exception when a build's code coverage report doesn't exist"""

    def __init__(self, build_id):
        self.build_id = build_id
        super().__init__(f'Code coverage report for build id "{build_id}" was not found!')


class BuildPurposeNotFound(Exception):
    """Exception when a retention policy was not found for the build purpose in the build.build_purpose table"""

    def __init__(self, build_purpose):
        self.build_purpose = build_purpose
        super().__init__(f"Couldn't find a retention policy for build purpose: {build_purpose}!")


class RetentionPolicyNotDefined(Exception):
    """Exception when a retention policy was not found for the build id"""

    def __init__(self, build_id):
        self.build_id = build_id
        super().__init__(f"Couldn't find a retention policy for build id: {build_id}!")


class NotSupportedBuildType(Exception):
    """Exception for when trying to set a build purpose for an unsupported build type"""

    def __init__(self, build_type):
        self.build_type = build_type
        self.message = f"Builds of type {build_type} are not supported!"
        super().__init__(self.message)
