"""Core and Program definition related queries"""

from __future__ import annotations
import os
import re
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import DBConnector

logging.basicConfig(format="%(asctime)s [%(levelname)s][%(name)s] %(message)s")
log = logging.getLogger("DatabaseAPI.Definitions")


class Definitions:
    """Core and Program definition related queries"""

    def __init__(self, db: DBConnector):
        self.db = db

    @staticmethod
    def driver_version_to_program_version(driver_version):
        """Converts a 4-nibble driver version (AA.BB.CC.DD) to a 3-nibble (AA.BB.CC) program version

        Args:
            driver_version (str): A driver version formatted AA.BB.CC.DD

        Returns:
            str: A program formatted AA.BB.CC
        """
        match = re.match(r"(\d+\.\d+\.\d+)(\.\d+)?", driver_version)  # Get only first 3 bytes
        if not match:
            raise ValueError(f"The provided driver version ({driver_version}) was not formatted as AA.BB.CC.DD!")
        return match.group(1)

    def get_core_definition(self, core_name, suppress_errors=False):
        """Get the definition of a single core.

        Args:
            core_name (str): The name of the core to query, or "latest" to get latest core.
            suppress_errors (bool): If True, an erorr will *not* appear if the specified core branch is not found.

        Raises:
            CoreNotFound: If the specified core was not found in the database and suppress_missing is False.

        Returns:
            dict: The core definition.
                  Empty dict if suppress_missing=True and the core was not found.
        """
        if core_name == "latest":
            where = "is_active = 1 AND is_legacy = 0 AND drv_branch <> 'master'"
        else:
            where = f"drv_branch LIKE '{core_name}' OR aliases LIKE '%{core_name}%'"
        results = self.db.get_single_row(table="coreDefinitions", where=where)
        if not results:
            if suppress_errors:
                return None
            raise CoreNotFoundError(core_name)
        return results

    def get_program_definition(self, program_version):
        """Get the definition for the given program

        Args:
            program_version (str): The Program Version (`XX.YY.ZZ`)

        Raises:
            ProgramNotFoundError: If the supplied program version is not found in the database

        Returns:
            dict: The program definition row
        """
        program = self.db.get_single_row(table="programs", primary_key="version", row_id=program_version)
        if not program:
            raise ProgramNotFoundError(program_version)
        return program

    def get_all_cores(self, include_inactive=False, include_legacy=False):
        """Get all the cores in the core definitions but filter based on is_active and is_legacy

        Args:
            include_inactive (bool): Include inactive cores in the results.
                                     Default to False.
            include_legacy (bool): Include legacy cores in the results.
                                   Defaults to False.

        Returns:
            dict: A dictionary with the key being the driver branch and the value being the core definition.
        """
        where = ""
        if not include_inactive:
            where += "is_active = 1"
        if not include_legacy:
            where += (" AND " if where else "") + " is_legacy = 0"
        return {core["drv_branch"]: core for core in self.db.query_table("coreDefinitions", where=where)}

    def get_core_from_version(self, version: str) -> dict:
        """
        Get the core definition from the version string.

        Args:
            version (str): The driver or program version number to get the core definition for.

        Raises:
            ValueError: If the given version string is not in the correct format (XX.YY.ZZ[.WW])
            CoreNotFoundError: If the core definition for the given version string is not found.

        Returns:
            dict: The core definition that matches the version number.
        """
        program_version = self.driver_version_to_program_version(version)
        results = self.db.get_single_row(table="coreDefinitions", where=f"program_version = '{program_version}'")
        if not results:
            raise CoreNotFoundError(None, f"No core found for version {version}")
        return results

    def get_matching_master_program(self, program_version):
        """Given a program version (e.g. 23.50.0) find it's matching master program version (e.g. 99.0.87)
        This is done by checking if core matching the supplied program has a matching driver branch or alias,
        and extracting the core number from that branch name.

        If the top driver happens to be 'master' (which will be the case for most non-standard programs), it will use
        the top driver's version directly.

        Note that irregular branch/program names *might* be inconsistent, assume this is a 'best-effort' function,
        meaning results might not be accurate but they're the most accurate we can get.

        Args:
            program_version (str): Any (valid) program version

        Returns:
            str: The matching master (99.0.X) program version.
        """
        if program_version.startswith("99.0"):
            return program_version

        top_driver = self.db.definitions.get_top_driver_from_program(program_version)
        branch = top_driver["drv_branch"]
        core = self.db.definitions.get_core_definition(branch)["drv_branch"]
        if core == "master":  # Top driver is already master
            return Definitions.driver_version_to_program_version(top_driver["version"])

        core_match = re.match(r"CoreCycle(\d+)", core, re.IGNORECASE)
        if not core_match:
            raise ValueError(
                f"Can not extract core number from branch {core} since it doesn't match the standard branch naming "
                "convention (CoreCycleXX...)"
            )
        return "99.0." + core_match[1]

    def get_driver_types(self):
        """Returns all the names of the MSI/Programs driver columns (NETw...) sorted by number.

        Returns:
            list: A list of the names of driver (`['NETwBnw04', 'NETwNw06', ...]`)
        """
        columns = self.db.get_column_names("programs")
        driver_columns = [
            column_name for column_name in columns if re.match(r"netw\S+\d{2,}", column_name, re.IGNORECASE)
        ]
        return sorted(driver_columns, key=lambda s: int(re.search(r"\d+", s).group()))

    def get_top_driver_from_msi(self, msi_build):
        """Returns the driver build with the highest driver descriptor ('NETwXnw10', 'NETwXnw08', etc) in an MSI build.

        Args:
            build_id (str): an MSI tic

        Returns:
            dict: A driver build entry or None if not found
        """
        build_data = self.db.builds.get_build(msi_build, "msiBuild")
        driver_types = self.get_driver_types()
        driver_types.reverse()  # Sort from top to bottom
        for driver_type in driver_types:
            if driver_type in build_data and build_data[driver_type]:
                driver = self.db.get_single_row("driverBuild", "build_id", build_data[driver_type])
                if driver:
                    return driver
        # If we reached here, the entire MSI build had no drivers in it. Should never ever happen.
        raise NoValidDriver(msi_build)

    def get_top_driver_from_program(self, program_version):
        """Returns the driver build with the highest driver descriptor ('NETwXnw10', 'NETwXnw08', etc) in a program
        recommendation.

        Args:
            program_version (str): A program version

        Returns:
            dict: A driver build entry or None if not found
        """
        definition = self.db.definitions.get_program_definition(program_version)
        driver_types = self.get_driver_types()
        driver_types.reverse()  # Sort from top to bottom
        for driver_type in driver_types:
            if driver_type in definition and definition[driver_type]:
                driver = self.db.get_single_row("driverBuild", "build_id", definition[driver_type])
                if driver:
                    return driver
        # If we reached here, the entire MSI build had no drivers in it. Should never ever happen.
        raise NoValidRecommendation(program_version)

    def get_latest_driver_versions(self, num_of_versions):
        """
        Get latest driver versions list
        [*] get num_of_drivers recent programs from the [programs] table
            these programs do not have legacy programs anymore, for legacy
            need to search recommendations
        [*] for every program, see if there are legacy recommendations
        [*] if yes, search for their programs by build IDs
        Args:
            num_of_drivers: number of versions to return
        Returns:
            list of driver versions, 3 parts e.g. 22.110.0, to match
            program version syntax.
            Sorted in descending order.
        """

        data = self.db.query_table(
            "programs",
            "version,NETwXnw04,NETwXnw06,NETwXnw08",
            order_by="date_of_rec",
            order_dir="DESC",
            limit=num_of_versions,
        )

        driver_versions = []

        # loop over all programs and fetch driver versions of NPI and legacy builds
        for program in data:
            # add legacy builds for this program
            # the list of indices corresponds to net04, net06, net08
            for build_id in [value for key, value in program.items() if key.startswith("NETw")]:
                if build_id:  # If build ID exists (means it's recommended)
                    full_driver_version = self.db.builds.get_driver_build_property(build_id, "version")
                    driver_versions.append(Definitions.driver_version_to_program_version(full_driver_version))

            # Add actual program version
            driver_versions.append(program["version"])

        # Convert set to a sorted list
        return sorted(set(driver_versions), reverse=True)

    def get_previous_version(self, current_program_version, major_only=False):
        """Given a program version, retrieves the previous version.
        For example, if those are the versions:
        - 22.220.5
        - 23.0.1
        - 23.0.2
        - 23.0.3
        - 23.10.0
        - 23.10.1

        `23.0.3` will return `23.0.2`
        If `major_only` is true, 23.0.3 will return 22.220.5 since 23.0.3 and 23.0.2 have the same major version (23.0).
        So for `major_only`, 23.10.1 will return 23.0.2, 23.0.2 will return 22.220.5, etc.

        Args:
            current_program_version (str): The program version (`##.###.##`) to get the previous version for

        Raises:
            ValueError: If the given string is in the wrong format.
            ProgramNotFoundError: If the supplied program does not exist
            NoPreviousVersion: If the supplied program is actually the oldest known program (why would anyone do that?)
                               or if it's in the 'special' program range (99.X where X != 0)

        Returns:
            str: The version number of the previous version.
        """
        program_version_pattern = r"(\d{1,3})\.(\d{1,3})\.(\d{1,5})"
        match = re.match(program_version_pattern, current_program_version)
        if not match:
            raise ValueError(
                f"Program versions must be in the format ##.###.##, which {current_program_version} is not!"
            )
        if match[1] == "99" and match[2] != "0":
            raise NoPreviousVersion(
                f"Program version {current_program_version} is in a special range of programs (99.X where X is not 0) "
                "that have no previous version!"
            )

        if major_only:
            # Master branch (starts with 99.XX.XX) doesn't have parent version
            # Programs that starts with 88.XX.XX are used by us for debug and don't have parent version
            if match[1] == "99" or match[2] == "88":
                log.warning("Programs starting with 99 all have the same major version, `major_only` is meaningless")
            else:
                current_program_version = f"{match[1]}.{match[2]}.0"

        programs = self.db.get_column_values(
            table="programs",
            column_name="version",
            order_by=(
                "CAST(PARSENAME([version], 3) as int) DESC, "
                "CAST(PARSENAME([version], 2) as int) DESC, "
                "CAST(PARSENAME([version], 1) as int)"
            ),
            order_type="DESC",
        )
        if current_program_version not in programs:
            raise ProgramNotFoundError(current_program_version)

        this_index = programs.index(current_program_version)
        if this_index == (len(programs) - 1):
            raise NoPreviousVersion(current_program_version)

        found_old_program_version = programs[this_index + 1]
        return found_old_program_version

    def get_mailing_list(self, mailing_list_name):
        """
        Returns (str):    mailing list's contacts (as written in database).
                    if 'mailing_list_name' not exists in database: returns None
        """
        table_name = "mailing_lists"
        where = f"mailing_list_name = '{mailing_list_name}'"
        mailing_list_row = self.db.get_single_row(table_name, where=where)
        if "mailing_list_members" in mailing_list_row:
            members_details = mailing_list_row["mailing_list_members"].split(",")
            contacts_emails_only_list = [((member_details.split("<"))[1])[:-1] for member_details in members_details]
            contacts_emails_only_string = ",".join(contacts_emails_only_list)
            return contacts_emails_only_string
        return None

    def get_file_mapping(self, file_path, fail_if_not_found=True, mappings=None) -> dict:
        """
        Gets a file's mapping to modules and domains, along with those module's and domain's reviewers.
        If file is not mapped directly in the database, will get its closest ancestor's mapping.

        Args:
            file_path (str): The path to the file whose domain you want to get
            fail_if_not_found (bool, optional): If True, will raise an exception if the file is not found in the db.
            mappings (dict, optional): the table data (optional to choose the data in advanced)

        Returns:
            dict: The database row of the domain, currently including:
            {
                file_path: The full file path (relative to the repo root, with Windows slashes (\\))
                module_name: The name of the module the file is assigned to (if any)
                domain_name: The name of the domain the module is assigned to (if any)
                file_additional_reviewers: A comma-separated list of additional reviewers for this file
                module_additional_reviewers: A comma-separated list of additional reviewers for the module
                security_needed: Whether security review is needed for this file
                module_owner: The email of the module owner
                team_leader: The email of the domain team leader
                security_champion: The email of the domain security champion
                maintainer: The email of the domain maintainer
            }
            or empty dict if not found and fail_is_not_found is False

        Raises:
            FileNotMappedToModule: If no mapping is available for the given file or its ancestors and fail_if_not_found
                                   is True.
        """
        if not mappings:
            mappings = {x["file_path"].lower(): x for x in self.db.query_table(table="codeowners.files_resolved")}
        file_path = file_path.replace("/", "\\").lower()
        if not (mapping := mappings.get(file_path, {})):  # If no direct match
            if ancestors := [mappings[x] for x in mappings if Definitions.__is_descendent(file_path, x)]:
                ancestors.sort(key=lambda x: len(x["file_path"]))
                mapping = ancestors[-1]  # Take most recent ancestor
        if not mapping and fail_if_not_found:
            raise FileNotMappedToModule(file_path)
        return mapping

    def get_module_definition_from_module_name(self, module_name: str, fail_if_not_found: bool = True) -> dict:
        """
        Gets a module definition based on its name.
        Not much logic in this one, it's just simpler to remember and implement than using `get_single_row`.

        Args:
            module_name (str): The name of the module to get the definition for.
            fail_if_not_found (bool, optional): If True, will raise an exception if the module is not found in the db.

        Returns:
            dict: The module definition of the module, or empty dict if not found and fail_if_not_found is False

        Raises:
            ModuleNotFound: If the module is not found in the database and fail_if_not_found is True
        """
        result = self.db.get_single_row("codeowners.modules", where=(f"module_name = '{module_name}'"))
        if (not result) and fail_if_not_found:
            raise ModuleNotFound(module_name)
        return result

    def get_domain_definition_from_domain_name(self, domain_name: str, fail_if_not_found: bool = True) -> dict:
        """
        Gets a domain definition based on its name.
        Not much logic in this one, it's just simpler to remember and implement than using `get_single_row`.

        Args:
            domain_name (str): The name of the domain to get the definition for.
            fail_if_not_found (bool, optional): If True, will raise an exception if the domain is not found in the db.

        Returns:
            dict: The domain definition, or empty dict if not found and fail_if_not_found is False

        Raises:
            DomainNotFound: If the domain is not found in the database and fail_if_not_found is True
        """
        result = self.db.get_single_row("codeowners.domains", where=(f"domain_name = '{domain_name}'"))
        if (not result) and fail_if_not_found:
            raise DomainNotFound(domain_name)
        return result

    def get_domain_definition_from_module_name(self, module_name, fail_if_not_found=True) -> dict:
        """
        Gets the domain definition of a module based on its name.

        Args:
            module_name (str): The name of the module to get the domain for.
            fail_if_not_found (bool, optional): If True, will raise an exception if the module is not found in the db.

        Returns:
            dict: The domain definition of the module, or None if not found and fail_if_not_found is False

        Raises:
            FileNotFoundError: If the module is not found in the database and fail_if_not_found is True
            ModuleNotFound: If the module is not found in the database and fail_if_not_found is True
            DomainNotFound: If the domain the module is mapped to is not found in the database
        """
        module = self.get_module_definition_from_module_name(module_name, fail_if_not_found)
        if not module:
            # get_module_definition_from_module_name already took care of fail_if_not_found = True at this point
            return {}
        return self.get_domain_definition_from_domain_name(module["domain_name"], fail_if_not_found)

    def get_domain_definition_from_file_path(self, file_path, fail_if_not_found=True, mappings=None) -> dict:
        """
        Returns the domain definition of a file based on its path and associate module.

        Args:
            file_path (str): The path of the file to get the domain for.
            fail_if_not_found (bool, optional): If True, will raise an exception if the file is not found in the db.
            mappings (dict, optional): The table data (optional to choose the data in advanced)

        Returns:
            dict: The domain definition of the module the file is mapped to, or empty dict if not found and
                  fail_if_not_found is False.

        Raises:
            FileNotMappedToModule: If no mapping is found for the given file or its ancestors and fail_if_not_found is
                                   True.
            ModuleNotFound: If the module the file is mapped to is not found in the database
            DomainNotFound: If the domain the module is mapped to is not found in the database
        """
        mapping = self.get_file_mapping(file_path, fail_if_not_found, mappings)
        if not mapping:
            # get_file_mapping already took care of fail_if_not_found = True at this point
            return {}

        if not (module_name := mapping.get("module_name")):
            if fail_if_not_found:
                raise FileNotMappedToModule(file_path)
            return {}

        return self.get_domain_definition_from_module_name(module_name, fail_if_not_found)

    def drv_to_fw_core(self, drv_branch, strip_prefix=False):
        """
        Translating from driver core to the matches fw core
        Args:
            drv_branch (str): The driver branch we would like to translate (CoreCycle##_stab/master)
            strip_prefix (bool, optional): Whether or not to strip the prefix "release/" for cores.
                                           Defaults to False.
        Raises:
            NoMatchingFWBranch: If the driver branch is not defined as core ot alias.
        Returns:
            The fw core matches the driver core
        """
        where = f"drv_branch LIKE '{drv_branch}' OR aliases LIKE '%{drv_branch}%' AND is_active = 1"
        results = self.db.get_single_row(table="coreDefinitions", where=where, select="fw_branch")
        if not results:
            raise NoMatchingFWBranch(drv_branch)
        fw_branch = results["fw_branch"]
        if strip_prefix:
            return fw_branch.replace("release/", "")
        return fw_branch

    def fw_to_drv_core(self, fw_core):
        """
        Translating from fw core to the matches driver core
        Args:
            fw_core (str): The fw core we would like to translate (CoreCycle##_stab/master or release/core##)
        Raises:
            NoMatchingFWBranch: If the fw branch is not defined in any core.
        Returns:
            The driver core that matches the fw core
        """
        # In case we need to add "release/" to get the full fw core branch
        if fw_core != "master" and "CoreCycle" not in fw_core and "release/" not in fw_core:
            fw_core = "release/" + fw_core
        where = f"fw_branch = '{fw_core}' AND is_active = 1"
        results = self.db.get_single_row(table="coreDefinitions", where=where, select="drv_branch")
        if not results:
            raise NoMatchingFWBranch(fw_core, "FW branch does not match any driver core!")

        return results["drv_branch"]

    @staticmethod
    def __is_descendent(child, parent):
        if os.name == "nt":  # Windows
            child = child.replace("/", "\\")
            parent = parent.replace("/", "\\")
        elif os.name == "posix":  # Linux
            child = child.replace("\\", "/")
            parent = parent.replace("\\", "/")
        else:
            raise OSError(f"Unsupported OS {os.name}!")

        while child and os.path.dirname(child) != child:
            if child == parent:  # We found a match or a sub match
                return True
            child = os.path.dirname(child)
        return False  # If we reached here, no match was found


class CoreNotFoundError(Exception):
    """Exception when trying to query information about a core that doesn't exist"""

    def __init__(self, core_name, message=None):
        self.core_name = core_name
        self.message = message if message else f'Core "{core_name}" was not found in the database!'
        super().__init__(self.message)


class ProgramNotFoundError(Exception):
    """Exception when trying to query information about a program that doesn't exist"""

    def __init__(self, program_version):
        self.program_version = program_version
        super().__init__(f"Program version {program_version} was not found in the database!")


class NoPreviousVersion(Exception):
    """Exception when trying to get a previous version of a program but it has none"""

    def __init__(self, program_version, message=None):
        self.program_version = program_version
        self.message = message or f"Program version {program_version} has no previous version!"
        super().__init__(self.message)


class NoValidDriver(Exception):
    """Exception when trying to find top driver in an MSI but no driver is found"""

    def __init__(self, msi_build):
        self.msi_build = msi_build
        self.message = f"MSI Build ${msi_build} has no valid drivers to find which one is the top!"
        super().__init__(self.message)


class NoValidRecommendation(Exception):
    """Exception when trying to find top driver in a program recommendation but no driver is found"""

    def __init__(self, program_version):
        self.program_version = program_version
        self.message = f"Program version {program_version} has no valid recommendations to find which one is the top!"
        super().__init__(self.message)


class ModuleNotFound(Exception):
    """Exception when trying to find a module that doesn't exist"""

    def __init__(self, module_name):
        self.module_name = module_name
        self.message = f"Module '{module_name}' does not exist!"
        super().__init__(self.message)


class FileNotMappedToModule(FileNotFoundError):
    """Exception when a file is not mapped to any module.
    This is specifically when a mapping exists but mapped to None module.
    """

    def __init__(self, file_path):
        self.file_path = file_path
        self.message = (
            f"Neither the path {file_path} nor any of its ancestors have any module mappings in the database!"
        )
        super().__init__(self.message)


class DomainNotFound(Exception):
    """Exception when trying to find a domain that doesn't exist"""

    def __init__(self, domain_name):
        self.domain_name = domain_name
        self.message = f"Domain '{domain_name}' does not exist!"
        super().__init__(self.message)


class NoMatchingFWBranch(Exception):
    """Exception for when trying to get branch head of a non-existing branch"""

    def __init__(self, branch, message=None):
        self.message = message or f'Can\'t match "{branch}" to a known or standard firmware branch!'
        super().__init__(self.message)
