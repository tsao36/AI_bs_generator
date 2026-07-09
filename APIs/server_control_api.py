"""
A collection of methods for controlling servers remotely.
This module provides functions to execute system commands, wait for a server to go offline,
restart a server, and shut down a server. It uses the ping3, subprocess, and time modules
to perform these operations.
"""

import logging
import subprocess
import time
from ping3 import ping


logging.basicConfig(format="%(asctime)s [%(levelname)s][%(name)s] %(message)s")
log = logging.getLogger("server_control_api")
log.setLevel(logging.INFO)
supported_os = ["windows", "linux"]


def wait_for_server_offline(server_name, timeout=600, check_interval=5):
    """
    Waits for the specified server to go offline within a given timeout period.

    Args:
        server_name (str): The name of the server to check.
        timeout (int, optional): The maximum amount of time (in seconds) to wait for the server to go offline.
                                 Default is 600 seconds.
        check_interval (int, optional): The interval (in seconds) between each check to see if the server is offline.
                                        Default is 5 seconds.

    Raises:
        TimeoutError: If the server does not go offline within the specified timeout period.
    """
    end_time = time.time() + timeout
    log.info("Pinging server '%s' to check if it is offline...", server_name)
    while ping(server_name) is not None:
        if time.time() > end_time:
            raise TimeoutError(
                f"Server '{server_name}' did not go offline within the timeout period of {timeout} seconds"
            )
        log.debug("Server '%s' is still online, waiting for it to go offline", server_name)
        time.sleep(check_interval)
    log.info("Server '%s' is now offline", server_name)


def restart_server(server_name, server_os):
    """
    Restarts the specified server based on its operating system.

    Args:
        server_name (str): The name or IP address of the server to restart.
        server_os (str): The operating system of the server (e.g., 'windows', 'linux').

    Raises:
        Exception: If the server restart command fails.
    """
    if server_os.lower() not in supported_os:
        raise ValueError(f"Unsupported operating system: {server_os}. Supported OS: {supported_os}")
    log.info("Restarting server '%s'", server_name)
    command = (
        f"shutdown /r /f /m \\\\{server_name} /t 0"
        if "windows" == server_os.lower()
        else f"ssh {server_name} 'sudo shutdown -r now'"
    )
    result = subprocess.run(command, shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout.decode(), stderr=result.stderr.decode()
        )
    log.info("Server '%s' was sent the restart command successfully", server_name)


def shutdown_server(server_name, server_os):
    """
    Shuts down a server based on its operating system.

    Args:
        server_name (str): The name or IP address of the server to shut down.
        server_os (str): The operating system of the server (e.g., 'Windows', 'Linux').

    Raises:
        Exception: If the server shutdown command fails.
    """
    if server_os.lower() not in supported_os:
        raise ValueError(f"Unsupported operating system: {server_os}. Supported OS: {supported_os}")
    log.info("Shutting down server '%s'", server_name)
    command = (
        f"shutdown /s /f /m \\\\{server_name} /t 0"
        if "windows" == server_os.lower()
        else f"ssh {server_name} 'sudo shutdown -h now'"
    )
    result = subprocess.run(command, shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout.decode(), stderr=result.stderr.decode()
        )
    log.info("Server '%s' was sent the shutdown command successfully", server_name)
