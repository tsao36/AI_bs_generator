"""
A collection of methods for interacting with Jenkins.
Uses the Jenkins REST API Python package
Documentation for that is available at https://python-jenkins.readthedocs.io/en/latest/
"""

import re
import os
import sys
import time
import json
import logging
from datetime import datetime
import jenkins
from jenkins import NotFoundException, JenkinsException
from requests import Request, HTTPError
from requests.exceptions import SSLError
from bs4 import BeautifulSoup

# pylint: disable=wrong-import-position
sys.path.append(os.path.join(os.path.dirname(__file__)))
from utils import auto_retry, NoRetryException

# pylint: enable=wrong-import-position

log = logging.getLogger("JenkinsAPI")

VALID_STAGE_STATES = ["UNSTABLE", "NOT_EXECUTED", "ABORTED", "FAILED", "SUCCESS"]


class Jenkins:
    """The instance of the Jenkins API interface."""

    verbose = False

    def __init__(
        self,
        server_url,
        username,
        password,
        suppress_exceptions=True,
        timeout=60,
        verbose=False,
    ):
        """Start a Jenkins API Instance.
        SSL Verification is done via environment - it can not be set programmatically.
        Use `REQUESTS_CA_BUNDLE` or `CURL_CA_BUNDLE` to set the path to the CA bundle.

        Args:
            server_url (str): The URL of the Jenkins controller.
                              (e.g. "https://cje-il-prod01.devtools.intel.com/ccg-cps-wifiwindrvprod/")
            username (str): The username to authenticate with.
            password (str): The password (or better yet - token) to authenticate with.
            suppress_exceptions (bool, optional): Whether to suppress non-critical exceptions or raise them.
                                                  True means some exceptions will be suppressed and just return empty.
                                                  False raises all exceptions.
                                                  Defaults to True.
            timeout (int, optional): How long to wait for an answer from the server.
                                     Defaults to 60.
            verbose (bool, optional): If True, print debug messages.
        """
        self.jenkins_url = server_url
        self.jenkins_instance = jenkins.Jenkins(server_url, username=username, password=password, timeout=timeout)
        self.suppress_exceptions = suppress_exceptions
        self.verbose = verbose

    @auto_retry(SSLError, NotFoundException)
    def _queue_build(
        self, job_name: str, build_params: dict = None, file_params: dict = None, use_url_params: bool = False
    ) -> int:
        """Queues a build with Jenkins using POST instead of python_jenkins' build in method (which uses GET)

        Args:
            job_name (str): The full job name (e.g. windows-wifi-driver/WIFI_DRV)
            build_params (dict, optional): The build parameters to build with (only use native types).
            file_params (dict, optional): Any file parameters to add to the request.
                                          Use the format {"param_name": "file_path", "param_name2": "file_path2", ...}
            use_url_params (bool, optional): Use params in the request url instead of in the request body.
                                             Default to None.

        Raises:
            ValueError: If the queue response was not parsed successfully
            FileNotFoundError: If a file parameter is specified but the file doesn't exist.

        Returns:
            int: The queue number of the build
        """
        log.info("Queueing build in job %s ...", job_name)
        if build_params:
            log.debug("Using parameters:\n%s", json.dumps(build_params, indent=4))
        if file_params:
            log.debug("Using file parameters:\n%s", json.dumps(file_params, indent=4))
        url = self.jenkins_instance.build_job_url(job_name) + ("WithParameters" if build_params else "")
        url_params = build_params if use_url_params else None
        data_params = build_params if not use_url_params else None

        try:
            # Since we might have multiple files, we can't use a context manager (`with open(...)`).
            # We have to open the files manually and close them manually, while saving their handlers in a dictionary.
            files = {}
            if file_params:
                for param_name, file_path in file_params.items():
                    files[param_name] = (
                        os.path.basename(file_path),
                        open(file_path, "rb"),  # pylint: disable=consider-using-with
                    )

            request = Request("POST", url, data=data_params, params=url_params, files=files)
            response = self.jenkins_instance.jenkins_request(request)
        finally:
            if files:
                for file in files.values():
                    file[1].close()

        location = response.headers["Location"]  # location is a queue item, eg. "http://jenkins/queue/item/25/"
        if match := re.match(r".+/(\d+)/$", location):
            queue_num = int(match.group(1))
            log.info("Build queued. Queue #%d", queue_num)
            return queue_num
        raise NoRetryException(ValueError(f"Couldn't parse queue URL: {location}"))

    @auto_retry(SSLError, NotFoundException)
    def _dequeue_build(self, job_name: str, queue_number: int, timeout: int = 60 * 30):
        """Polls a build in the queue until it starts then dequeues it.

        Args:
            job_name (str): The full job name (e.g. windows-wifi-driver/WIFI_DRV)
            queue_number (int): The queue number of the build. This is the return value of `_queue_build`.
            timeout (int, optional): After this time (in seconds), stop waiting for the build to dequeue.
                                     Defaults to 60*30 (30 minutes).

        Raises:
            TimeoutError: If the amount of time (in seconds) specified in `timeout` has passed before the build started.

        Returns:
            dict: The build information of the build that just started.
        """
        try:
            queued_build = self.jenkins_instance.get_queue_item(queue_number)
        except JenkinsException as exc:
            if "does not exist" in str(exc):
                log.warning("Failed to get queue item for queue #%d: %s. Attempting recovery...", queue_number, exc)
                builds = self.get_all_builds(job_name, ["queueId", "number"])
                queued_build = next((x for x in builds if x.get("queueId") == queue_number), None)
                if queued_build:
                    log.info("Build is already dequeued - build id is %s", queued_build["number"])
                    return self.get_build_info(job_name, queued_build["number"])

            raise NoRetryException(exc) from exc

        # What we're waiting for here is for the "Queued" item to be executed and turn into an actual build
        # When this happens, it gets an "executable" item:
        start = datetime.now()
        attempt = 1
        interval = 5

        log.info('Waiting for build queued for "%s" (queue #%d) to exit queue... ', job_name, queue_number)
        while not queued_build.get("executable"):
            if queued_build.get("stuck"):
                log.debug(
                    'Build queued for "%s" (queue #%d) is in stuck state. '
                    "Ignoring until timeout of %d seconds expires.",
                    job_name,
                    queue_number,
                    timeout,
                )
            if "cancelled" in queued_build and queued_build["cancelled"]:
                log.warning('Build queued for "%s" (queue #%d) was cancelled!', job_name, queue_number)
                return None
            if (datetime.now() - start).seconds > timeout:
                raise NoRetryException(
                    TimeoutError(
                        f'Build queued for "{job_name}" (queue #{queue_number}) did not start after '
                        f"the alloted {timeout} seconds!"
                    )
                )
            log.debug(
                'Build queued for "%s" (queue #%d) has not yet started. '
                "Sleeping %d seconds and trying again (Attempt %d)...",
                job_name,
                queue_number,
                interval,
                attempt,
            )
            time.sleep(interval)

            # staggered increase of interval
            attempt += 1
            if 20 > attempt > 10:  # Between 10 and 20 attempts use 10 seconds
                interval = 10
            elif attempt > 20:
                interval = 30
            queued_build = self.jenkins_instance.get_queue_item(queue_number)
        build_number = queued_build["executable"]["number"]
        log.info(
            'Build queued for "%s" (queue #%d) has dequeued after %d attempt%s and received the build number %d.',
            job_name,
            queue_number,
            attempt,
            "s" if attempt > 1 else "",
            build_number,
        )
        return self.get_build_info(job_name, build_number)

    def trigger_build(
        self,
        job_name: str,
        build_params: dict = None,
        file_params: dict = None,
        trigger_timeout: int = 60 * 30,
        use_url_params: bool = False,
    ) -> dict:
        """Starts a Jenkins build

        Args:
            job_name (str): The full job name (e.g. windows-wifi-driver/WIFI_DRV)
            build_params (dict, optional): The build parameters to build with. Only use native types.
            file_params (dict, optional): Any file parameters to add to the request.
                                          Use the format {"param_name": "file_path", "param_name2": "file_path2", ...}
            trigger_timeout(int, optional): If the build doesn't start within this time (seconds), raise a TimeoutError.
                                            Defaults to 60 * 30 (30 minutes).
            use_url_params (bool, optional): Use params in the request url instead of in the request body.
                                             Default to None.
        Raises:
            TimeoutError: If the amount of time (in seconds) specified in `timeout` has passed before the build started.
            FileNotFoundError: If a file parameter is specified but the file doesn't exist.

        Returns:
            dict: The build information dictionary
        """
        queue_num = self._queue_build(job_name, build_params, file_params, use_url_params)
        return self._dequeue_build(job_name, queue_num, trigger_timeout)

    @auto_retry(SSLError, NotFoundException)
    def poll_build(self, build_info: dict, interval=10, timeout=60 * 60 * 3):
        """Polls a build until it either finishes or times out.

        Args:
            build_info (dict): The build dictionary received from triggering the build.
            interval (int, optional): Time in seconds to wait between each querying of the build status.
                                      Defaults to 10.
            timeout (int, optional): Time in seconds after which a build will be considered timed out.
                                     Defaults to 3 hours.

        Raises:
            TimeoutError: If the build doesn't finish within the alloted time (specified in `timeout`)

        Returns:
            dict: The build information object.
        """
        job_name = build_info["job_name"]

        display_name = build_info["fullDisplayName"]

        # while in jenkins queue, first build
        if queue_id := build_info.get("queueId"):
            log.info('Build "%s" not yet started. Waiting for queue to finish ...', display_name)
            jenkins_build_id = self._dequeue_build(job_name, queue_id)["number"]
            log.info('Build "%s" exited queue.', display_name)

        # get build info about current run, return tuple[is_running, result]
        build_info = self.get_build_info(job_name, jenkins_build_id)
        start = datetime.now()

        # running
        log.info('Waiting for "%s" build to finish... (no further info prints will appear until it does)', display_name)
        while build_info.get("building"):
            if (datetime.now() - start).seconds > timeout:
                raise NoRetryException(
                    TimeoutError(f"The build didn't finish within the alloted {timeout / 60} minutes!")
                )
            log.debug('Build "%s" is still building. Waiting %d seconds and checking again...', display_name, interval)
            time.sleep(interval)
            build_info = self.get_build_info(job_name, jenkins_build_id)

        # done
        log.info('Build "%s" finished with the result %s.', display_name, build_info["result"])
        return build_info

    @auto_retry(SSLError, NotFoundException)
    def stop_build(self, job_name, build_number):
        """Stops a Jenkins build. Has no return value, just stops it and returns.

        Args:
            job_name (str): The full job name (e.g. windows-wifi-driver/WIFI_DRV)
            build_number (int): The build number to stop.
        """

        self.jenkins_instance.stop_build(job_name, build_number)

    def download_artifact(self, build_info, file_name, target):
        """Download an artifact from Jenkins and save it locally.

        Args:
            build_info (dict): The build information object.
            file_name (str): Name of the file to download (best not full path, just filename.ext)
            target (str): Path to save to. If path is a directory, the original file name will be used.

        Raises:
            FileNotFoundError: If the supplied build has no artifact matching the supplied file name.
        """
        # First see if the build has artifacts at all:
        display_name = build_info["fullDisplayName"]

        if not (artifacts := build_info.get("artifacts")):
            raise FileNotFoundError(f'Build "{display_name}" has no artifacts.')

        # See if the build has the given artifact
        if not (artifact := next((x for x in artifacts if any(x[y] == file_name for y in x)), None)):
            raise FileNotFoundError(f'Build "{display_name}" has no artifacts matching the name "{file_name}"')

        file_url = f"{build_info['url']}/artifact/{artifact['relativePath']}"

        if os.path.isdir(target) or target.endswith(("/", "\\")):
            target = os.path.join(target, artifact["fileName"])

        log.info("Saving %s to %s ...", artifact["fileName"], target)

        response = self.jenkins_instance.jenkins_request(Request("GET", file_url))
        if response.status_code != 200:
            raise HTTPError(response=response)

        with open(target, mode="wb") as file_writer:
            file_writer.write(response.content)
        log.info("File saved successfully as %s", target)

    def get_all_builds(self, job_name, fields: list = None):
        """Return all builds for a specific job.
        Limited by the length of history Jenkins keeps.

        Args:
            job_name (str): The name of the job to query.
            fields (list): A list of fields you want to see in the response.
                           Note that if you specify non-existing fields, no error will occur, it will just be missing
                           from the response.
                           Defaults to "number" and "url".

        Returns:
            list: A list of all build objects, each containing the fields requested.
        """
        job_url = self.jenkins_instance.get_job_info(job_name)["url"]
        fields_string = f"[{','.join(fields)}]" if fields else "[number,url]"
        request = Request("GET", job_url + f"api/json?tree=allBuilds{fields_string}")
        response = self.jenkins_instance.jenkins_request(request)
        response.raise_for_status()
        all_builds = response.json()["allBuilds"]
        return all_builds

    def get_build_status(self, job_name, build_number):
        """Query the jenkins to get build status about specific build.

        Args:
            job_name (str): The job name.
            build_number (int): The build number to query.

        Returns:
            status (tuple):
                is_building (bool) : is the job in status "RUNNING"
                state (str) : build result (i.e: SUCCESS, FAILURE or ABORTED )
        """
        build_info = self.get_build_info(job_name, build_number)
        if build_info["building"] is True:
            return True, None
        return False, build_info["result"]

    def get_build_info(self, name, number, depth=0):
        """Forwarder for the Jenkins library get_build_info function"""
        build_info = self.jenkins_instance.get_build_info(name, int(number), depth)
        return {**build_info, "pipeline": build_info["url"] + "display/redirect", "job_name": name}

    def get_build_parameters(self, job_name, build_number):
        """Get builds parameters used in a specific Jenkins build.

        Args:
            job_name (str): The job name of the build to query
            build_number (int): The build number of the build to query

        Returns:
            dict: A Key: Value dict of the parameters.
        """
        return Jenkins.params_from_build_info(self.jenkins_instance.get_build_info(job_name, int(build_number)))

    def get_build_console_output(self, job_name, build_number):
        """Forwarder for the Jenkins library get_build_console_output function"""
        build_console_output = self.jenkins_instance.get_build_console_output(job_name, int(build_number))
        return build_console_output

    def get_all_nodes(self) -> dict:
        """Returns all of the nodes in the Jenkins instance.

        Returns:
            dict: All the nodes, with the node name as the key and the node object as the value.
        """
        request = Request("GET", self.jenkins_url + "computer/api/json")
        all_nodes = self.jenkins_instance.jenkins_request(request).json()["computer"]
        return {node["displayName"]: node for node in all_nodes}

    def get_node_config_by_name(self, node_name: str) -> dict:
        """The function return node information based on node name

        Args:
            node_name (str): Node Name

        Returns:
            dict: Node information
        """
        if self.jenkins_instance.node_exists(node_name):
            node_config = self.jenkins_instance.get_node_config(node_name)
            return node_config
        log.error("Failed to find %s on the master server", node_name)
        return None

    def get_node_info_by_name(self, node_name: str) -> dict:
        """The function return node information based on node name

        Args:
            node_name (str): Node Name

        Returns:
            dict: Node information
        """
        if self.jenkins_instance.node_exists(node_name):
            node_info = self.jenkins_instance.get_node_info(node_name)
            return node_info
        log.error("Failed to find %s on the master server", node_name)
        return None

    def disable_node(self, node_name: str, disable_reason: str) -> bool:
        """The function disable the node

        Args:
            node_name (str): Node name
            disable_reason (str): Disable reason

        Returns:
            bool: True if disablement succeed
        """
        log.debug("Taking server '%s' offline", node_name)
        if self.jenkins_instance.node_exists(node_name):
            try:
                self.jenkins_instance.disable_node(node_name, disable_reason)
                log.debug("Server '%s' is now offline", node_name)
                return True
            except Exception as ex:
                log.error("Failed to disable %s", node_name)
                log.error(ex)
                return False
        else:
            log.error("Failed to find %s on the master server", node_name)
            return False

    def enable_node(self, node_name: str) -> bool:
        """The function enables the node

        Args:
            node_name (str): Node name
            disable_reason (str): Disable reason

        Returns:
            bool: True if enablement succeed
        """
        log.debug("Bringing server '%s' back online", node_name)
        if self.jenkins_instance.node_exists(node_name):
            try:
                self.jenkins_instance.enable_node(node_name)
                log.debug("Server '%s' is now online", node_name)
                return True
            except Exception as ex:
                log.error("Failed to enable %s", node_name)
                log.error(ex)
                return False
        else:
            log.error("Failed to find %s on the master server", node_name)
            return False

    def wait_for_jobs_to_finish(self, node_name, timeout=6 * 3600, check_interval=30):
        """
        Waits for all jobs to finish on the specified Jenkins server within a given timeout period.

        Args:
            node_name (str): The name of the Jenkins node to monitor.
            timeout (int, optional): The maximum time to wait for jobs to finish, in seconds.
                                     Default is 6*3600 seconds (6 hours).
            check_interval (int, optional): The interval between checks for job completion, in seconds.
                                            Default is 30 seconds.

        Raises:
            TimeoutError: If the timeout period is reached and jobs are still running.
            ValueError: If the node information could not be retrieved.
        """
        log.info("Waiting for all jobs to finish on node '%s' with a timeout of %s hours...", node_name, timeout / 3600)
        end_time = time.time() + timeout  # timeout from now
        while True:
            if time.time() > end_time:
                raise TimeoutError(
                    f"Timeout of {timeout / 3600} hours reached. Jobs are still running on node '{node_name}'"
                )
            node_info = self.get_node_info_by_name(node_name)
            if not node_info:
                raise NotFoundException(f"Failed to get node info for node '{node_name}'")
            if node_info["idle"]:
                log.info("All jobs have finished on node '%s'", node_name)
                break
            # Wait for check_interval seconds before checking again
            log.debug("Jobs are still running on node '%s', waiting for %d seconds", node_name, check_interval)
            time.sleep(check_interval)

    def get_node_labels(self, node_name: str, labels_dict: dict) -> list:
        """
        The function fills the given dictionary with Jenkins node labels

        Args:
            node_name (str): Node name
            labels_dict (dict): Setups dictionary
        """
        nodes_info = self.get_node_config_by_name(node_name)
        soup = BeautifulSoup(nodes_info, features="html.parser")
        for labels in soup.findAll("label"):
            if labels.contents:
                node_lables_list = labels.contents[0].split()
                for label in node_lables_list:
                    labels_dict[node_name].append(label)

    def map_nodes_labels(self):
        """
        The function maps all Jenkins node labels

        Returns:
            dict: Nodes labels dictionary
        """
        all_nodes = self.get_all_nodes()
        return {node["displayName"]: [label["name"] for label in node["assignedLabels"]] for node in all_nodes.values()}

    def get_nodes_by_label(self, label):
        """
        Get all nodes that match a specific label

        Returns:
            dict: Matching node name as keys, and node objects as values.
        """
        nodes = self.get_all_nodes()
        found_nodes = {}
        for node in nodes.values():
            if label in [label["name"] for label in node["assignedLabels"]]:
                found_nodes[node["displayName"]] = node
        return found_nodes

    @staticmethod
    def params_from_build_info(build_info: dict):
        """Extracts the weirdly formatted params from the build_info object as a regular dictionary"""
        param_list = next(
            x["parameters"] for x in build_info["actions"] if x.get("_class") == "hudson.model.ParametersAction"
        )
        return {param["name"]: param["value"] if param["value"] != "" else None for param in param_list}

    def get_stages(self, job_name, build_no, filter_state=None, minimum_duration=None):
        """Get all stage information from a certain build.

        Args:
            job_name (str): The full job name
            build_no (int): The build number in Jenkins.
            filter_state (str | list, optional): Only show stages that match the given string or are in the list.
                                                 All values must be one of the following:
                                                 ['UNSTABLE', 'NOT_EXECUTED', 'ABORTED', 'FAILED', 'SUCCESS']
                                                 Defaults to None (show all stages).

            minimum_duration (int, optional): The minimum duration (in millisec) required to get a stage in the results.
                                              Defaults to 0 if no filter_state is used, or 5000 if any *is* used.

        Raises:
            TypeError: IF the filter_state is neither a string, a list, or None

        Returns:
            dict: Key is the stage name, value is all the stage info
        """
        if not filter_state:
            filter_state = VALID_STAGE_STATES
        if isinstance(filter_state, str):
            filter_state = [filter_state]

        if not isinstance(filter_state, list):
            raise TypeError(
                "Filter Status must be a string for a single filter, a list for multiple, or None for no filters"
            )

        if invalid_states := [x for x in filter_state if x not in VALID_STAGE_STATES]:
            raise ValueError(
                f"The specified state(s) {invalid_states} is not one of the valid states: "
                + (", ").join(VALID_STAGE_STATES)
            )

        build_info = self.get_build_info(job_name, int(build_no))
        request = Request(method="GET", url=build_info["url"] + "/wfapi")
        response = self.jenkins_instance.jenkins_request(request)
        response.raise_for_status()
        minimum_duration = (5 * 1000) if minimum_duration is None else minimum_duration

        stages = {
            stage["name"]: stage
            for stage in response.json()["stages"]
            if stage["durationMillis"] > minimum_duration and stage["status"] in filter_state
        }

        return stages

    def get_node_os(self, node_name):
        """
        The function returns the OS of the given node e.g. Linux (amd64) or Windows (amd64)

        Args:
            node_name (str): Node name

        Returns:
            str: OS of the given node aka Linux (amd64) or Windows (amd64)
        """
        node_info = self.get_node_info_by_name(node_name)
        return node_info["monitorData"]["hudson.node_monitors.ArchitectureMonitor"]

    def get_node_os_name(self, node_name):
        """
        The function returns the OS name of the given node e.g. Linux or Windows

        Args:
            node_name (str): Node name

        Returns:
            str: OS name of the given node aka Linux or Windows
        """
        node_info = self.get_node_info_by_name(node_name)
        if "linux" in node_info["monitorData"]["hudson.node_monitors.ArchitectureMonitor"].lower():
            return "Linux"
        if "windows" in node_info["monitorData"]["hudson.node_monitors.ArchitectureMonitor"].lower():
            return "Windows"
        raise ValueError(f"OS {node_info['monitorData']['hudson.node_monitors.ArchitectureMonitor']} not supported")
