""" Artifactory access API with common features and methods """

import sys
import os
import re
import logging
from typing import Union
from requests.exceptions import HTTPError
from artifactory import ArtifactoryPath

# pylint: disable=wrong-import-position
sys.path.append(os.path.dirname(__file__))  # Add own dir
from secret_manager import SecretManager
import Sherlock

# pylint: enable=wrong-import-position


log = logging.getLogger("ArtifactoryAPI")

VALID_REPOSITORIES = [
    "wcd-binaries-local",
    "wcd-windows-binaries-local",
    "wifi-windows-driver-il-local",
    "wcs_pcpl-fseq-dev-il-local",
]

VALID_ARTIFACT_PROPS_BASE = [
    {"$or": [{"repo": repo} for repo in VALID_REPOSITORIES]},
    {"@firmware_cfg_selection_option": "all"},
    {"$or": [{"@mac_address": "NA"}, {"@mac_address": "0x"}]},
]

VALID_ARTIFACT_PROPS_REMOTE = VALID_ARTIFACT_PROPS_BASE + [{"@sign_remotely": "true"}]
VALID_ARTIFACT_PROPS_MANUAL = VALID_ARTIFACT_PROPS_REMOTE + [{"@sign_manualy": "true"}]


class Artifactory:
    """The Artifactory instance.
    Authentication is done via an API token that is retrieved via PAM.
    Authentication for PAM is done with a certificate that is stored in the environment variable PAM_CERT.
    See more information at https://wiki.ith.intel.com/display/WCDSherlock/Secret+Manager.

    Args:
        server_path (str): Address of the server, e.g. "https://ubit-artifactory-il.intel.com/artifactory/"
    """

    def __init__(self, server_path):
        # Get API Key
        if server_path[-1:] != "/":
            server_path += "/"
        self.server_path = server_path

        if "PAM_CERT" not in os.environ:
            raise EnvironmentError("PAM_CERT environment variable not found!")

        secret_mgmt = SecretManager(Sherlock.PAM.cert_appid, Sherlock.PAM.safe_name, os.environ["PAM_CERT"])
        self.token = secret_mgmt.get_secret(Sherlock.Artifactory.username, "Artifactory")
        self.query_path = ArtifactoryPath(self.server_path, token=self.token)

    def sha1_from_path(self, path):
        """Get the SHA1 related to the given artifact.

        Args:
            path (str): The artifact path relative to Artifactory,
                    e.g. "wcd-binaries-local/FW/Post_CI/core43/34366/WCD_FW_BUILD_68451_SHA1_e689f2ea_R_BIN_WIN.tar.gz"

        Returns:
            str: The SHA1 stored in the artifact's properties as "fw_full_sha1".
        """
        # We're skipping the query, we're opening a path directly to the artifact we want.
        artifact = ArtifactoryPath(self.server_path + path, token=self.token)
        if not artifact.exists():
            raise ArtifactDoesNotExist(path)
        if "fw_full_sha1" not in artifact.properties:
            raise NoMatchingSha1(artifact.path_in_repo)
        return artifact.properties["fw_full_sha1"][0]

    def paths_from_props(self, props, exclude_list: list = None):
        """Get all the artifactory paths that match the supplied artifactory props (properties).
        Results are sorted by descending creation date (newer first).

        Args:
            props (str | dict): string of prop key and values separated by ';' - e.g. key1=val1;key2=val2
                                or
                                one-dimensional dict of props e.g. {"key": "val", "key2": "val2"}
                                All values must be strings!

            exclude (list): list of words that if they exist in the path, it will not be included in the results.

        Returns:
            list: list of paths found for the given props. Empty list if none found.
        """

        if isinstance(props, dict):
            bad_props = {k: v for k, v in props.items() if not isinstance(v, str)}
            if bad_props:
                raise TypeError(
                    "Dictionary must be one-level deep and all values must be strings! "
                    "The following values have invalid types: "
                    + ", ".join([f"{k}: {v} ({v.__class__.__name__})" for k, v in bad_props.items()]),
                )
            prop_list = [{f"@{key}": val} for key, val in props.items()]

        elif isinstance(props, str):
            chars = "[a-zA-Z0-9_-]"
            if not re.match(rf"^{chars}+={chars}+(;{chars}+={chars}+)*$", props):
                raise ValueError("The provided props string is invalid! Must be in the 'key=val;key=val' format!")
            prop_list = [{f"@{x.split('=')[0]}": x.split("=")[1]} for x in props.split(";")]

        else:
            raise TypeError("The props must either be a dict, or a string in the format key=val;key=val!")

        exclude_list = ["latest"] + (exclude_list if exclude_list else [])  # Always exclude "latest"

        prop_list.append({"name": {"$match": "*BIN_WIN*"}})
        search_params = {"$and": prop_list}
        artifacts = self.query_path.aql("items.find", search_params)
        if not artifacts:
            return []

        log.debug("Removing artifacts with words from the exclude list in path...")
        artifacts = [x for x in artifacts if not any(y.lower() in x["path"].lower() for y in exclude_list)]
        if not artifacts:
            log.warning(
                "After removing all artifacts that match a word in the exclude list, "
                "no artifacts remain! Exclude list was: %s",
                ", ".join(exclude_list),
            )
            return []

        artifacts.sort(key=lambda x: x["created"], reverse=True)  # Sort results by date descending
        return [f"{artifact['repo']}/{artifact['path']}/{artifact['name']}" for artifact in artifacts]

    def path_from_props(self, props, exclude: list = None):
        """Get the artifactory path from artifactory props (properties).
        If more than one

        Args:
            props (str): list of key=val separated by ';' - e.g. key1=val1;key2=val2
            exclude (list): list of words that if they exist in the path, it will not be included in the results.

        Returns:
            str: the path for the found artifact
        """
        artifacts = self.paths_from_props(props, exclude)

        if not artifacts:
            raise NoMatchingProps(props)

        # in case of several matches - take the latest
        return artifacts[0]

    def path_from_sha1(self, sha1, additional_queries=None, exclude_list=None, fallback_query=None):
        """Get the artifact path relating to the given SHA1.

        Args:
            sha1 (str): The SHA1 whose artifact we want to find.
            additional_queries (list, optional): A list of additional queries to add to the search.
                                       See https://www.jfrog.com/confluence/display/JFROG/Artifactory+Query+Language
                                       for syntax.
            exclude_list (list, optional): A list of strings which if any are found in the path, it will not be used.
                                 By default always includes the word "latest" for backwards compatibility.
            fallback_query (list, optional): If the search fails, try this query instead before raising an exception.

        Raises:
            NoMatchingArtifact: If no artifact was found with the given SHA1

        Returns:
            str: The address of the artifact relative to Artifactory,
                 e.g. "wcd-binaries-local/FW/Post_CI/core43/34366/WCD_FW_BUILD_68451_SHA1_e689f2ea_R_BIN_WIN.tar.gz"
        """
        additional_queries = additional_queries if additional_queries else []
        exclude_list = ["latest"] + (exclude_list if exclude_list else [])  # Always exclude "latest"

        if not isinstance(additional_queries, list) or not all(isinstance(x, dict) for x in additional_queries):
            raise TypeError("Additional Queries need to be a list of dicts!")

        search_params = {"$and": [{"@fw_full_sha1": sha1}, {"name": {"$match": "*BIN_WIN*"}}] + additional_queries}

        try:
            artifacts = self.query_path.aql("items.find", search_params)
            if not artifacts:
                if fallback_query:
                    log.warning("The base query failed, trying fallback query...")
                    return self.path_from_sha1(
                        sha1, additional_queries=fallback_query, exclude_list=exclude_list, fallback_query=None
                    )
                raise NoMatchingArtifact(sha1)
        except HTTPError as err:
            if err.response.status_code == 400:
                raise ValueError(
                    "ArtifactoryAPI did not understand your query. Make sure your syntax is correct!\n"
                    "For example, Artifact Property keys must start with a @, and all final values must be strings "
                    "(even booleans and integer)!"
                ) from err
            raise err
        log.debug("Found %d artifact%s.", len(artifacts), "s" if len(artifacts) > 1 else "")
        if len(artifacts) > 1:
            log.debug("Sorting by descending date created...")
            artifacts.sort(key=lambda x: x["created"], reverse=True)

        log.debug("Removing artifacts with words from the exclude list in path...")
        artifacts = [x for x in artifacts if not any(y.lower() in x["path"].lower() for y in exclude_list)]
        if not artifacts:
            log.error(
                "After removing all artifacts that match a word in the exclude list, "
                "no artifacts remain! Exclude list was: %s",
                ", ".join(exclude_list),
            )
            if fallback_query:
                log.warning("The base query failed, trying fallback query...")
                return self.path_from_sha1(
                    sha1, additional_queries=fallback_query, exclude_list=exclude_list, fallback_query=None
                )
            raise NoMatchingArtifact(sha1)

        log.debug("%d artifact%s remaining...", len(artifacts), "s" if len(artifacts) > 1 else "")
        if len(artifacts) > 1:
            log.debug("Selecting most recent artifact...")

        artifact = artifacts[0]
        return f"{artifact['repo']}/{artifact['path']}/{artifact['name']}"

    def deploy_artifact(self, file_to_upload, target_dir, properties=None, overwrite=False):
        """Deploys an artifact to the requested path in Artifactory, creating the path if it doesn't exist.

        Args:
            file_to_upload (str): LOCAL path of the file on the local computer
            target_dir (str): The Artifactory target directory, e.g. "wifi-windows-driver-il-local/release/"
            properties (dict): Any properties to be attached to the file.
            overwrite (bool): If exists, overwrite existing.
                              Defaults to false (in which case, it will raise an exception).
        """
        target_dir_instance = self.get_instance(target_dir)
        target_file_path = f"{target_dir}{os.path.basename(file_to_upload)}"

        if not target_dir_instance.exists():
            target_dir_instance.mkdir()
            log.info("Target dir %s did not exist and was created.", target_dir)

        else:
            # Check if file already exists
            target_file_instance = self.get_instance(target_file_path)
            if target_file_instance.exists() and not overwrite:
                raise ArtifactAlreadyExists(target_file_path)

        target_dir_instance.deploy_file(file_to_upload)
        target_file_instance = self.get_instance(target_file_path)
        if not target_file_instance.exists():
            raise FileNotFoundError(
                "Deploy operation reported success but file still not found in expected target path!"
            )

        target_file_instance.properties = {"retention.days": "365", **(properties if properties else {})}

        log.info(
            "File deployed successfully: "
            "https://ubit-artifactory-il.intel.com/artifactory/webapp/#/artifacts/browse/tree/General/%s",
            target_file_path,
        )
        return target_file_path

    def set_property(self, artifact_path, key, value):
        """Sets a single property to an artifact

        Raises:
            ArtifactDoesNotExist: If the path supplied doesn't exist

        Args:
            artifact_path (str): The API path of the artifact
            key (str): The key of the property
            value (str): The value of the property
        """
        path = self.get_instance(artifact_path)
        if not path.exists():
            raise ArtifactDoesNotExist(artifact_path)
        properties = path.properties
        properties[key] = value
        path.properties = properties

    def create_folder(self, target_dir):
        """Creates a folder in the requested path if it does not already exist.

        Args:
            target_dir (str): The Artifactory target directory, e.g. "wifi-windows-driver-il-local/release/"
        """
        path = self.get_instance(target_dir)
        if not path.exists():
            path.mkdir()
            log.info("Directory %s created successfully.", target_dir)
        else:
            log.warning("Directory %s already exists.", target_dir)

    def delete_folder(self, target_dir, recursive=False):
        """Deletes the folder in the specified path if it exists.

        Args:
            target_dir (str): The Artifactory target directory, e.g. "wifi-windows-driver-il-local/release/"
            recursive (bool): Whether or not to also delete contents.
        """
        path = self.get_instance(target_dir)
        if path.exists():
            if sum(1 for x in path.iterdir()) and not recursive:  # If dir is nor empty and recursive is false
                log.error('Folder %s is not empty and can not be deleted without "recursive = True"', target_dir)
                raise DirNotEmpty(target_dir)

            path.rmdir()
            log.info("Directory %s deleted successfully.", target_dir)
        else:
            log.warning("Direcotry %s does not exist.", target_dir)

    def get_instance(self, path: Union[str, ArtifactoryPath]) -> ArtifactoryPath:
        """Parses and the target path and returns an ArtifactoryPath instance
        ArtifactoryPath works like pathlib in almost every way.

        Args:
            path (str): The Artifactory path, e.g. "wifi-windows-driver-il-local/release/"

        Returns:
            ArtifactoryPath: A pathlib equivalent representation of the Artifactory path
        """
        if isinstance(path, ArtifactoryPath):
            return path
        if path[:1] == "/":
            path = path[1:]  # Remove leading /
        if path[-1:] == "/":
            path = path[:-1]  # Remove ending /
        target_instance = self.server_path + path
        return ArtifactoryPath(target_instance, token=self.token)

    def download_artifact(self, artifactory_path, target_folder, overwrite=False):
        """download artifacts to target folder from given artifactory path

        Args:
            artifactory_path (str): The Artifactory path, e.g. "wifi-windows-driver-il-local/release/"
            target_folder (str): The path to save the artifacts to
            overwrite (bool): Whether to overwrite local file if it already exists

        Returns:
            int : 0 if artifacts download was completed or the file already exists and overwrite was False.
                  Defaults to True.
            file_name : artifact name
        """
        path = self.get_instance(artifactory_path)
        file_name = path.name
        target_file = os.path.join(target_folder, file_name)
        log.info("Downloading artifact %s to %s", artifactory_path, target_file)
        if os.path.exists(target_file):
            if not overwrite:
                log.warning("file %s already exists - skiping", target_file)
                return 0, file_name
            log.warning("File exists! Overwriting...")
            os.remove(target_file)
        with open(target_file, "wb") as out:
            try:
                log.info("Downloading using stream method...")
                with path.open() as file_handle:
                    out.write(file_handle.read())
            except OverflowError as overflow_error:
                log.warning("Stream method failed! Falling back on chunk method...")
                res = path.session.get(str(path), stream=True, verify=True, cert=None)
                if res.status_code != 200:
                    log.error("Couldn't get artifact!")
                    raise RuntimeError(res.status_code) from overflow_error
                for chunk in res.iter_content(chunk_size=2048):
                    if chunk:
                        out.write(chunk)
        return 0, target_file

    def copy(self, source_path, target_path, overwrite=True):
        """Copy one path (artifact or folder) to another.
        If A -> B, B is a directory, and it already exists, one of two things will happen:
        * If overwrite is True, A's copy will replace B.
        * If overwrite is False, A's copy will be placed INSIDE of B.

        Args:
            source_path (str): Source path (directory or file)
            target_path (str): Target path (directory or file)
            overwrite (bool, optional): Whether or not to override existing artifacts. Defaults to True.

        Raises:
            ArtifactDoesNotExist: If the source artifact or directory can not be found
            ArtifactAlreadyexists: If the target is a file that exists and overwrite is Fasle
            IsADirectory: If the source is a file and the target is a directory
        """
        source = self.get_instance(source_path)
        target = self.get_instance(target_path)
        log.info("Copying artifacts from %s to %s", source_path, target_path)
        if not source.exists():
            raise ArtifactDoesNotExist(source_path)

        if source.is_dir() and target.is_dir() and overwrite:
            log.warning("Target is a directory which exists and overwrite is True. Removing it first ...")
            self.delete_folder(target_path, recursive=True)

        if target.is_file() and not overwrite:
            raise ArtifactAlreadyExists(target_path)

        if source.is_dir() and target.is_file():
            raise IsADirectoryError("You can't copy a directory into a file!")

        source.copy(target)
        log.info("Done!")

    def url_from_path(self, artifactory_path):
        """Returns the Web URL of the artifact / folder

        Args:
            artifactory_path (str): Path of artifact / folder to URLize

        Returns:
            str: The Web URL of the artifact / folder
        """
        if artifactory_path[:1] == "/":
            artifactory_path = artifactory_path[1:]
        return self.server_path + "webapp/#/artifacts/browse/tree/General/" + artifactory_path

    def validate_artifact_props(
        self, path, require_remote_sign=True, require_manual_sign=False, require_iml_sign=False
    ):
        """Validates that the artifact path supplied points to a valid artifact.
        Meaning that it's from the corret repository, is signed, and has all configs targeted.

        Args:
            path (str): The artifactory relative path to the artifact
            require_remote_sign (bool): Whether or not we require remote signature to consider an artifact valid.
                                        Defaults to True
            require_manual_sign (bool): Whether or not we require a manual signature to consider an artifact valid
                                        Default to False
            require_iml_sign (bool): Whether or not we require an IML signature to consider an artifact valid
                                     Defaults to False

        Returns:
            dict: Each of the evaluated property and whether or not it's valid.
                Keys are: full_fw_build, supported_repo, windows_binary, signed_remotely, signed_manually, signed_iml

        Raises:
            ArtifactDoesNotExist: If the supplied path does not exist
            ArtifactHasNoProps: If the supplied artifact has no props
            IsADirectory: If the supplied path is a directory and not an artifact file
        """
        instance = self.get_instance(path)
        if not instance.exists():
            raise ArtifactDoesNotExist(path)
        if instance.is_dir():
            raise IsADirectoryError(path)
        props = instance.properties
        if not props:
            raise ArtifactHasNoProps(path)
        returned_dict = {
            "full_fw_build": props.get("firmware_cfg_selection_option") == ["all"],
            "supported_repo": instance.repo in VALID_REPOSITORIES,
            "windows_binary": "BIN_WIN" in instance.name,
        }
        signatures = self.get_signature_types(path)
        if require_remote_sign:
            returned_dict["signed_remotely"] = signatures.get("remote", False)
        if require_manual_sign:
            returned_dict["signed_manually"] = signatures.get("manual", False)
        if require_iml_sign:
            returned_dict["signed_iml"] = signatures.get("iml", False)
        returned_dict["mac_address"] = signatures.get("mac_address") is None
        return returned_dict

    def get_signature_types(self, path):
        """Gets all the signature types of the FW artifact

        Args:
            path (str): The path of the artifact to get signature types for

        Raises:
            ArtifactDoesNotExist: If the supplied path does not exist
            ArtifactHasNoProps: If the supplied artifact has no props
            IsADirectory: If the supplied path is a directory and not an artifact file

        Returns:
            dict: {"remote": bool, "manual": bool, "iml": bool}
        """
        instance = self.get_instance(path)
        if not instance.exists():
            raise ArtifactDoesNotExist(path)
        if instance.is_dir():
            raise IsADirectoryError(path)
        props = instance.properties
        if not props:
            raise ArtifactHasNoProps(path)
        return {
            "remote": props.get("sign_remotely", ["false"]) == ["true"],
            "manual": props.get("sign_manualy", ["false"]) == ["true"],  # Typo here is in the source - don't fix!
            "iml": props.get("sign_iml", ["false"]) == ["true"],
            "mac_address": None if props.get("mac_address") in [["NA"], ["0x"]] else props.get("mac_address"),
        }


class DirNotEmpty(Exception):
    """Exception when trying to delete a non-empty folder with recursive = True"""

    def __init__(self, folder_path):
        super().__init__(f'Folder {folder_path} is not empty and can not be deleted without "recursive = True"')
        self.folder_path = folder_path


class ArtifactAlreadyExists(Exception):
    """Exception when trying to upload an artifact that already exists in the same path."""

    def __init__(self, artifact_path):
        super().__init__(f"{artifact_path} already exists!")
        self.artifact_path = artifact_path


class ArtifactDoesNotExist(Exception):
    """An exception when trying to get an artifact that doesn't exist"""

    def __init__(self, artifact_path):
        super().__init__(f"{artifact_path} does not exists!")
        self.artifact_path = artifact_path


class NoMatchingArtifact(Exception):
    """An exception for when trying to get an artifact from SHA1 but no artifact exists"""

    def __init__(self, sha1):
        super().__init__(f"No artifact exists matching SHA1 {sha1}")
        self.sha1 = sha1


class NoMatchingSha1(Exception):
    """An exception for when trying to get SHA1 from Artifact but no SHA1 exists"""

    def __init__(self, artifact):
        super().__init__(f"No SHA1 for artifact {artifact}")
        self.artifact = artifact


class NoMatchingProps(Exception):
    """An exception for when trying to get artifactory path from props but now match found"""

    def __init__(self, props):
        super().__init__(f"No artifact for props {props}")
        self.props = props


class ArtifactHasNoProps(Exception):
    """An exception for when trying to validate artifact but it has no props"""

    def __init__(self, artifact_path):
        super().__init__(f"Artifact {artifact_path} can not be validated since it has no props!")
        self.artifact_path = artifact_path
