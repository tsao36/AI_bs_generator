"""Skynet related queries"""

from __future__ import annotations
from enum import Enum
from typing import List, Union, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from .. import DBConnector

logging.basicConfig(format="%(asctime)s [%(levelname)s][%(name)s] %(message)s")
log = logging.getLogger("DatabaseAPI.Skynet")


class PackageType(Enum):
    """Package types for Skynet and their coreDefinitions column names"""

    PERCI = "perci_package_name"
    NIGHTLY = "nightly_package_name"
    NIGHTLY_RELEASE = "nightly_release_package_name"
    ZORRO = "zorro_package_name"


class Skynet:
    """Skynet related queries"""

    def __init__(self, db: DBConnector):
        self.db = db

    def get_test_suites(
        self,
        is_active: bool = None,
        package_types: Union[PackageType, List[PackageType]] = None,
        drv_branch: str = None,
    ):
        """
        Get test suites from the database.
        If no parameters are passed, all test suites are returned.

        Args:
            is_active (bool, optional): If True, only test suites associated with active cores are returned.
                                        If False, only test suites associated with inactive cores are returned.
                                        If None, will not filter by core `is_active` value.
                                        Defaults to None.
            package_types (PackageType | List[PackageType], optional): Core package typecolumn to filter by.
                                                                       Defaults to None (all package types).
            drv_branch (str, optional): Core's drv branch to filter by.
                                        Defaults to None (don't filter by branch).

        Returns:
            list: A list of test suites that match the criteria.
        """
        if all(prop is None for prop in [is_active, package_types, drv_branch]):
            return self.db.query_table("testSuiteRequirements")

        if is_active is not None and drv_branch is not None:
            log.warning("Both is_active and drv_branch are specified - they may conflict!")

        if package_types is None:
            package_types = list(PackageType.__members__.values())  # All package types if not specified otherwise

        if isinstance(package_types, PackageType):  # Convert single PackageType to list to allow flexibility in input
            package_types = [package_types]

        total_where = []
        per_package_where = []

        if is_active is not None:
            per_package_where.append(f"is_active = {1 if is_active is True else 0}")

        if drv_branch is not None:
            per_package_where.append(f"drv_branch = '{drv_branch}'")

        for package_type in package_types:
            # Select the test suite based on the package type and other filters.
            # The test suite id is selected from the testPackage table which links it to the coreDefinitions table.
            # See https://wiki.ith.intel.com/display/WCDSherlock/PyTM+database+design for more details.
            total_where.append(
                f"""
[id] IN (
    SELECT test_suite_id FROM testPackage WHERE package_name IN (
        SELECT {package_type.value} FROM coreDefinitions
        {" WHERE " + " AND ".join(per_package_where) if per_package_where else ""}
    )
)
"""
            )

        where = " OR ".join(total_where)
        return self.db.query_table("testSuiteRequirements", where=where)

    def get_tests(self, test_suite_ids: Union[int, List[int]] = None):
        """
        Get tests associated with a test suite.

        Args:
            test_suite_id (int | List[int], optional): The ID (or ids) of the test suite(s) to get tests from.
                                                       If None, doesn't filter by test suite.

        Returns:
            list: A list of tests associated with the specified test suite(s) or all tests if no test suite specified.
        """
        if test_suite_ids is None:
            return self.db.query_table("test")

        if isinstance(test_suite_ids, int):
            test_suite_ids = [test_suite_ids]

        test_suite_ids = [str(test_suite_id) for test_suite_id in test_suite_ids]

        # Select all tests that are associated with the test suite id(s) passed in.
        # The test id is selected from the testList table which links it to the testSuiteRequirements table.
        # See https://wiki.ith.intel.com/display/WCDSherlock/PyTM+database+design for more details.
        return self.db.query_table(
            "test",
            where=f"test_id IN (SELECT test_id FROM testList WHERE test_suite_id IN ({','.join(test_suite_ids)}))",
        )
