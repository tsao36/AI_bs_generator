"""
Verifies that files meet validity requirements based on file-type.
If no setting file is supplied, assumes there are no settings.
Settings must be a json and contain the following:
{
    "root-requirements": "...",
    "skip-files": ["some/path", ...],
    "skip-folders": ["folder1", "folder2", ...]
    "disallowed-patterns": {
        "*": ["disallowed-pattern-in-all-files", ...]
        "some/path": ["disallowed-in-this-file", ...]
    }
    "allowed-disables": {
        "*": ["allowed-in-all-files", ...],
        "some/path": ["allowed-in-this-file", ...]
    }
    "disallowed-files": [ "some/path", ... ]
    "deprecated-files": ["some/path", ...]
}

All paths are relative to the given target directory.
All list values can be strings if they only have one member.
"""

import os
import re
import sys
import ast
import json
import logging
import subprocess
import collections
from glob import glob
import argparse
import yaml
import black
import pylint.lint
import pylint.reporters

# pylint: disable=wrong-import-position
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from GitAPI import GitAPI
from GerritAPI import Gerrit
from Sherlock import Gerrit as GerritAuth, WinDRVJenkins
from jenkins_api import Jenkins
from github import Github, Auth

# pylint: enable=wrong-import-position

logging.basicConfig(format="%(asctime)s [%(levelname)s][%(name)s] %(message)s")
log = logging.getLogger("ValidityChecker")

EXCLUDE_FOLDERS = [".vscode", ".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"]


def run_pytest():
    """Run Pytest using a subprocess.

    Returns:
        None if no tests are available. Otherwise, a dict:
        {
            "success": bool - whether or not all tests passed,
            "message": The error message if any, otherwise empty string
        }
    """
    # First, find all the test files:
    test_files = list(glob("test/test_*"))
    if not test_files:
        log.info("No test files were found, PyTest will not run")
        return None
    log.info("Running PyTest ...")
    pytest_run = subprocess.run([sys.executable, "-m", "pytest"], cwd=args.target_dir, check=False)
    message = " * Pytest failed! See log above for details!" if pytest_run.returncode != 0 else ""
    return {"success": pytest_run.returncode == 0, "message": message}


def install_python_requirements(files):
    """Goes over all the directories in the files list, and if it sees a requirements.txt, installs it.
    Using `subprocess.check_call` instead of built in `pip.main` since that is the recommended way by pip themselves.

    Args:
        files (list): The list of files that are to be validated.
    """

    files += list(glob("test/test_*"))
    if os.name == "nt":
        files = [x.replace("/", "\\") for x in files]

    # First, determine just the folder names:
    folder_names = list(set(os.path.dirname(x) for x in files))

    # Now find requirement.txt files
    # Yes, the following two lines could be one, but this is more readable and the lists are too short to care about
    # performance.
    requirements_files = [os.path.realpath(os.path.join(args.target_dir, x, "requirements.txt")) for x in folder_names]
    requirements_files = [x for x in requirements_files if os.path.isfile(x)]

    for file in requirements_files:
        log.info("Installing required packages for %s ...", os.path.basename(os.path.dirname(file)))
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", file], check=True, capture_output=True)

    if requirements_files:
        inner_run = subprocess.run([sys.executable, *sys.argv, "--no-install"], check=False)
        sys.exit(inner_run.returncode)


def normalize_path(path):
    """Normalizes a path string for comparisons and such"""
    return path.replace("\\", "/")


def validate_config(config_dict: dict):
    """Validates and normalizes a config dictionary to match the expected format"""
    log.info("Validating config file...")

    supported_lists = [
        "disallowed-files",
        "deprecated-files",
        "skip-files",
        "skip-folders",
        "root-requirements",
        "versionless-packages",
    ]
    supported_dicts = ["allowed-disables", "disallowed-patterns"]
    supported_strings = ["root-requirements"]

    return_dict = {}
    for key, value in config_dict.items():
        if key in supported_lists:
            if isinstance(value, list):
                if key in supported_strings:
                    raise BadConfig(f"{key} must only have a single string value, not a list!")
                return_dict[key] = [normalize_path(x) for x in value]
            elif isinstance(value, str):
                return_dict[key] = normalize_path(value) if key in supported_strings else [normalize_path(value)]
            else:
                raise BadConfig(f"Value for the key {key} must be lists of files, not {type(value).__name__}")

        elif key in supported_dicts:
            if not isinstance(value, dict):
                raise BadConfig(f"{key} has to have a dictionary as its value!")
            return_dict[key] = {}
            for inner_key, inner_value in value.items():
                if isinstance(inner_value, list):
                    if inner_key in supported_strings:
                        raise BadConfig(f"{inner_key} must only have a single string value, not a list!")
                    return_dict[key][normalize_path(inner_key)] = inner_value

                elif isinstance(inner_value, str):
                    # We can fix it!
                    if inner_key in supported_strings:
                        continue
                    return_dict[key][normalize_path(inner_key)] = (
                        normalize_path(inner_value) if key in supported_strings else [normalize_path(inner_value)]
                    )
                else:
                    raise BadConfig(f"Inner values for {key} must be in a list, not {type(inner_value).__name__}!")
        else:
            supported_keys = ", ".join([*supported_lists, *supported_dicts])
            raise BadConfig(f"Unsupported key {key}! Supported keys are: {supported_keys}")
    log.info("Config valid!")
    return return_dict


class BadConfig(Exception):
    """Exception for the config is invalid"""

    def __init__(self, message):
        super().__init__(message)


def check_json(file_name):
    """
    This function checking json files syntax and validity.

    Args:
        file_name (str): the full file name
    """
    try:
        final_path = os.path.join(args.target_dir, file_name)
        with open(final_path, "r", encoding="utf-8") as json_file:
            json.load(json_file)
        return {"success": True}
    except Exception as ex:
        return {"success": False, "message": str(ex)}


def check_jenkins(file_path, debug=False):
    """Check if Jenkinsfile is valid

    Args:
        file_path (str): Path to the Jenkinsfile
        debug (bool): Run in DEBUG mode (with DEBUG logging, DB and jenkins)

    Returns:
        dict: {"success": Valid or not, "message": The validator message}
    """
    if "JENKINS_URL" in os.environ:
        jenkins_auth = WinDRVJenkins.get_instance_from_url(os.environ.get("JENKINS_URL"))
        jenkins = Jenkins(jenkins_auth.url, jenkins_auth.username, jenkins_auth.token)
    else:
        log.info("Jenkins instance not found by the environment variable. Init Jenkins instance from Sherlock.")
        jenkins_auth = WinDRVJenkins.Pre if debug else WinDRVJenkins.Prod
        jenkins = Jenkins(jenkins_auth.url, jenkins_auth.username, jenkins_auth.token)
    with open(os.path.join(args.target_dir, file_path), "r", encoding="utf-8") as jenkinsfile:
        jenkinsfile_content = jenkinsfile.read()
    jenkinsfile_errors = jenkins.jenkins_instance.check_jenkinsfile_syntax(jenkinsfile_content)
    if not jenkinsfile_errors:
        return {"success": True}

    resolved_errors = jenkinsfile_errors[0]["error"] if jenkinsfile_errors else []
    resolved_errors = [resolved_errors] if isinstance(resolved_errors, str) else resolved_errors  # Fix single result

    return {"success": False, "message": "* " + "\n* ".join(resolved_errors)}


def check_requirements(file_path):
    """Checks requirements.txt to make sure all packages have specific versions and that no packages are re-used from
    the root requirements.txt file (the one in APIs root)

    Args:
        file_path (str): The path of the file to validate
    """
    full_req_path = os.path.join(args.target_dir, file_path)
    with open(full_req_path, "r", encoding="utf-8") as requirements_file:
        requirements = [line.strip() for line in requirements_file.readlines()]

    if not (root_file := config.get("root-requirements")):
        raise BadConfig(
            "A root requirements file must be defined in the config file if a requirements.txt file is changed!"
        )

    full_root_path = os.path.join(args.target_dir, root_file)
    if not os.path.isfile(full_root_path):
        raise FileNotFoundError(f"The root requirements file {root_file} does not exist!")

    with open(full_root_path, "r", encoding="utf-8") as root_requirements_file:
        root_requirements = [line.split("==")[0].strip() for line in root_requirements_file.readlines()]

    if duplicates := [item for item, count in collections.Counter(root_requirements).items() if count > 1]:
        raise BadConfig(f"Root requirements files contains duplicate packages: {', '.join(duplicates)}")

    bad_requirements = {}
    for requirement in requirements:
        if full_req_path != full_root_path and requirement.split("==")[0] in root_requirements:
            bad_requirements[requirement] = (
                f"Requirement is already defined in the root requirements.txt file ({root_file})!"
            )
            continue
        if requirement not in config.get("versionless-packages", []) and not re.match(
            r"^[\w_\-\.]+(([=>]=[\d.]+)|(\[[\w_\-\.]+\]))", requirement
        ):
            bad_requirements[requirement] = "Requirement does not have a specific version!"
            continue
    return {
        "success": not bool(bad_requirements),
        "message": "\n".join(f"* {k}: {v}" for k, v in bad_requirements.items()),
    }


def get_init_hook(file_path):
    """Finds and executes 'sys.path.append' commands from the given source code.

    Args:
        file_path (str): Path of the file to parse for commands.
    """
    with open(file_path, "r", encoding="utf-8") as source:
        source_code = source.read()

    if re.search(r"#\s*pylint:\s*skip-file", source_code):
        return None

    tree = ast.parse(source_code)

    append_nodes = list(
        filter(
            lambda node: hasattr(node, "value")
            and hasattr(node.value, "func")
            and hasattr(node.value.func, "attr")
            and node.value.func.attr == "append",
            ast.walk(tree),
        )  # This is how ast finds "append", because it's a hell package designed by masochists.
    )  # It can't even find "sys.path.append" - just "append". We have to deal with that manually.
    append_commands = [re.sub(r"\s*\n\s*", "", ast.get_source_segment(source_code, x)) for x in append_nodes]
    final_hook = f'import sys; sys.path.append("{os.path.dirname(file_path)}"); '
    for command in append_commands:
        if match := re.match(r"sys\.path\.append\((.+)\)", command):
            path = eval(match.group(1), {"__file__": file_path, "os": os, "sys": sys})  # pylint: disable=eval-used
            final_hook += f'sys.path.append("{os.path.realpath(path)}"); '
    return final_hook.replace("\\", "/")


def check_python(file_name):
    """
    This function checking python files syntax and validity.

    Args:
        file_name (str): The file name (relative to the root)
    """
    final_path = os.path.realpath(os.path.join(args.target_dir, file_name))

    output = ""

    if not (init_hook := get_init_hook(final_path)):  # Will happen if file was skipped
        return None

    with open(final_path, "r", encoding="utf-8") as original_file:
        python_content = original_file.read()

    # Black
    return_value = black.format_str(python_content, mode=black.Mode(line_length=120))
    if return_value != python_content:
        output += "\n * Black would reformat the file!"

    # pylint
    allowed_disables = [
        *config.get("allowed-disables", {}).get("*", []),
        *config.get("allowed-disables", {}).get(normalize_path(file_name), []),
    ]

    for index, line in enumerate(python_content.split("\n")):
        if match := re.search(r"noqa\s*:\s*([\w\d]+)", line):
            output += (
                "\n * "
                + file_name.replace("/", "\\")
                + f":{index + 1}: NoQA is not allowed! Use `pylint: disable` instead for pre-allowed rules only!"
            )
            continue
        if match := re.search(r"pylint\s*:\s*disable\s*=\s*([^#]+)", line):
            for rule in [x.strip() for x in match.group(1).split(",")]:
                if rule not in allowed_disables:
                    output += (
                        "\n * " + file_name.replace("/", "\\") + f':{index + 1}: Disabling "{rule}" is not allowed!'
                    )

    pylintrc = os.path.join(args.target_dir, ".pylintrc")

    subprocess_args = [final_path, "--init-hook", init_hook]
    if os.path.exists(pylintrc):
        subprocess_args += ["--rcfile", pylintrc]

    _reporter = pylint.reporters.CollectingReporter()
    pylint.lint.Run(subprocess_args, do_exit=False, reporter=_reporter)

    for message in _reporter.messages:
        output += "\n * " + message.format("{path}:{line}:{column}: {msg} ({msg_id}: {symbol})")

    return {"success": output == "", "message": output}


def check_yaml(file_name):
    """
    This function checks yaml files syntax and validity.

    Args:
        file_name (str): the file name (relative to the root)
    """
    try:
        final_path = os.path.join(args.target_dir, file_name)
        with open(final_path, "r", encoding="utf-8") as yaml_file:
            yaml.load(yaml_file, Loader=yaml.FullLoader)
        return {"success": True}
    except Exception as ex:
        return {"success": False, "message": str(ex)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Files Validity Check")
    parser.add_argument("--target-dir", type=str, required=True, help="Target dir to scan changes")
    parser.add_argument("--file-list", type=str, help="A list of files to validate, separated by commas.")
    parser.add_argument("--config-file", type=str, required=False, help="A config file which defines workspace rules.")
    parser.add_argument("--branch", type=str, help="Scan only changed files between the target dir and this branch.")
    parser.add_argument("--refspec", type=str, help="Gerrit refspec to post errors to.")
    parser.add_argument("--pull-request", type=int, help="The pull request number. Must supply --repository if used.")
    parser.add_argument(
        "--repository",
        type=str,
        help="The GitHub Repository the pull request is from. e.g. intel-innersource/drivers.wireless.wifi.windows.foo",
    )
    parser.add_argument("--no-install", action="store_true", help="Don't install required packages.")
    parser.add_argument("--no-tests", action="store_true", help="Don't run PyTests.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose (DEBUG) logging.")
    parser.add_argument("--debug", action="store_true", help="Run in DEBUG mode (with DEBUG logging, DB and Jenkins)")
    args = parser.parse_args()
    log.setLevel(logging.DEBUG if args.verbose else (logging.CRITICAL if args.no_install else logging.INFO))

    if args.no_tests:
        log.warning("No Tests will run!")

    if not os.path.isdir(args.target_dir):
        raise FileNotFoundError("The supplied target dir does not exist!")

    target_dir = args.target_dir
    if config_path := args.config_file:
        if not os.path.isfile(config_path):
            raise FileNotFoundError("The supplied config file doesn't exist!")
        with open(config_path, mode="r", encoding="utf-8") as config_file:
            config_json = json.loads(config_file.read())
        config = validate_config(config_json)
        config["disallowed-files"] = [
            *config.get("disallowed-files", []),
            normalize_path(os.path.relpath(config_path, target_dir)),  # Protect config file from tempering
        ]
    else:
        log.warning("No config file was defined!")
        config = {}

    log.info("Generating list of changed files...")

    if args.file_list:
        changed_files = args.file_list.split(",")

    elif args.refspec:  # Running from Gerrit, get files from gerrit.
        gerrit = Gerrit(GerritAuth.url, GerritAuth.username, GerritAuth.token)
        changed_files = gerrit.get_list_of_changed_files(args.refspec, show_deleted=False)

    elif pull_request := args.pull_request:  # Running from GitHub, get files from GitHub.
        if not (repo_path := args.repository):
            parser.error("A repository must be supplied if validating a pull request!")
        github = Github(auth=Auth.NetrcAuth())
        repo = github.get_repo(repo_path)
        pr = repo.get_pull(pull_request)
        changed_files = [file.filename for file in pr.get_files() if file.status not in ("removed", "renamed")]

    else:  # Running locally, get diff from remote.
        git_review_path = os.path.join(target_dir, ".gitreview")

        # Determine branch
        if not (branch := args.branch):
            if not os.path.isfile(git_review_path):  # No branch, no gitreview file
                log.warning("No gitreview file was found and no branch was supplied, validation will run on ALL files!")
            else:  # No branch, Have gitreview file
                with open(git_review_path, mode="r", encoding="utf-8") as gitreview_file:
                    gitreview = gitreview_file.read()
                if search_result := re.search(r"defaultbranch=([\S]*)", gitreview):
                    branch = search_result.group(1)
                else:
                    log.warning("gitreview had no branch and no branch was supplied, validation will run on ALL files!")

        if branch:
            log.warning("PerCI will run compared to remote branch %s. Please rebase for most accurate results!", branch)
            changed_files = GitAPI(target_dir).get_diff_from_branch(branch)
        else:
            log.warning("PerCI will run on all files! You probably don't want this, so please supply --branch")
            changed_files = list(glob("**/*", recursive=True))

    EXCLUDE_FOLDERS += config.get("skip-folders", [])

    for folder in EXCLUDE_FOLDERS:
        exclude = [x for x in changed_files if any(path == folder for path in normalize_path(x).split("/"))]
        if exclude:
            log.warning("Excluding %d files that match the excluded folder: %s", len(exclude), folder)
            changed_files = [x for x in changed_files if x not in exclude]

    if not changed_files:
        log.info(
            "No files were changed. Please note that only files that were staged or committed are marked as 'changed'.",
        )
    else:
        log.info("Found %d changed files.", len(changed_files))
    results = {}

    python_changed = any(x.endswith(".py") for x in changed_files)

    if not args.no_install and python_changed:
        install_python_requirements(changed_files)

    log.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    if changed_files:
        log.info("Validating files ...")
        for changed_file in changed_files:
            if os.path.isdir(changed_file):
                log.info("\t%s: SKIPPED! (Directory or submodule)", changed_file)
                continue

            if changed_file in config.get("skip-files", []):
                log.info("\t%s: SKIPPED! (Defined in config file)", changed_file)
                continue

            if normalize_path(changed_file) in config.get("deprecated-files", []):
                log.info("\t%s: DEPRECATED!", changed_file)
                results[changed_file] = {
                    "success": False,
                    "message": (
                        "This file is being deprecated and no changes in it are allowed!\n"
                        "If you're simply removing code from it, approve this commit manually!"
                    ),
                }
                continue

            if normalize_path(changed_file) in config.get("disallowed-files", []):
                log.info("\t%s: BLOCKED!", changed_file)
                results[changed_file] = {
                    "success": False,
                    "message": f"Changes in {changed_file} require manual approval!",
                }
                continue

            full_path = os.path.join(args.target_dir, changed_file)
            extension = os.path.splitext(changed_file)[1]

            if extension == ".py":
                result = check_python(changed_file)
                if not result:
                    log.info("\t%s: SKIPPED! (pylint directive)", changed_file)
                    continue
            elif extension == ".json":
                result = check_json(changed_file)
            elif extension in [".yaml", ".yml"]:
                result = check_yaml(changed_file)
            elif extension == "" and "jenkinsfile" in changed_file.lower():  # Jenkinsfile are a bit tricky to detect
                result = check_jenkins(changed_file, args.debug)
            elif os.path.basename(changed_file) == "requirements.txt":
                result = check_requirements(changed_file)
            else:
                # No checker for file type
                log.info("\t%s: SKIPPED! (no checker for file type)", changed_file)
                continue
            results[changed_file] = result
            if not result["success"]:
                log.info("\t%s: %s", changed_file, "FAILED!")
                continue
            with open(full_path, mode="r", encoding="utf-8") as file_handler:
                file_content = file_handler.read()

            disallowed_patterns = [
                *config.get("disallowed-patterns", {}).get("*", []),
                *config.get("disallowed-patterns", {}).get(changed_file, []),
            ]

            for pattern in disallowed_patterns:
                if re.search(pattern, file_content, re.IGNORECASE):
                    results[changed_file] = {
                        "success": False,
                        "message": (
                            f"The file {changed_file} contains a sequence matching the pattern {pattern} which it is "
                            "not allowed to have according to the PerCI config file for the repository!"
                        ),
                    }
                    continue
            log.info("\t%s: %s", changed_file, "PASSED!" if results[changed_file]["success"] else "FAILED!")

        if not all(x["success"] for x in results.values()):
            log.error("There were issues found in some of the changed files!")
        else:
            log.info("All files validated!")

    if python_changed and (not args.no_tests) and (pytest_result := run_pytest()):
        results["pytest"] = pytest_result

    if not all(x["success"] for x in results.values()):
        errors = dict(filter(lambda item: not item[1]["success"], results.items()))
        error_list = [
            f"{'='*(len(filename)+1)}\n" + f"{filename}:\n" + f"{'='*(len(filename)+1)}\n{result['message']}"
            for filename, result in errors.items()
        ]
        ERROR_STRING = "Validation errors detected:\n" + "\n\n".join(error_list) + "\n"
        if any(x.endswith(".py") for x in errors):
            ERROR_STRING += (
                "\n===\nPython linting errors detected.\n"
                "See https://wiki.ith.intel.com/display/WCDSherlock/Visual+Studio+Code#VisualStudioCode-Python "
                "for instructions on setting up linters and formatters in VSCode."
            )

        log.error(ERROR_STRING)

        # Post error to gerrit if running from PerCI
        if args.refspec:
            gerrit.post_comment(args.refspec, ERROR_STRING)

        sys.exit(-1)
    log.info("PerCI Passed!")
