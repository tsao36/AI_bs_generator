"""Utility functions that may be useful in other scripts"""

import os
import sys
import traceback
import time
import logging
from functools import wraps
from typing import Tuple
import subprocess
import platform

# pylint: disable=wrong-import-position
sys.path.append(os.path.join(os.path.dirname(__file__)))
from DatabaseAPI import DBConnector
from Sherlock import Database, DatabaseSandbox

# pylint: enable=wrong-import-position


def auto_retry(*allowed: Tuple[Exception]):
    """A decorator function which wraps the function it precedes with a retry mechanism.
    If a function with this decorator raises an exception, it will be retried after 10 seconds, then an exponentially
    increasing number of seconds until a total of 10 retries and 1310 seconds of sleeping.

    To use, simply put `@auto_recovery` in the line above the function you want to wrap.
    You can have exceptions bubble through the retry (meaning, stop retrying completely) by wrapping them in the
    "NoRetryException" exception, like so:

    To print verbose error messages (with the full error stack) even between retries, set the environment variable
    `AUTO_RETRY_DEBUG` to `true`.

    ```py
    raise NoRetryException(Exception("This will not retry"))
    ```

    Args:
        allowed (Exception, optional): If supplied - exceptions of this type (or types) will not trigger a retry.

    Raises:
        ex: If all retries failed, the last exception raised.
    """

    def decorator(func):
        @wraps(func)
        def auto_retry_wrapper(*args, **kwargs):
            log = logging.getLogger("AutoRetry")
            retry = 1
            sleep = 10
            max_retry = 10
            final_exception = None
            total_sleep = 0
            while retry < max_retry:  # Max wait time of 10 minutes
                try:
                    return func(*args, **kwargs)
                except NoRetryException as nre:
                    if traceback.format_exc().count(func.__name__) > 1:  # In an inner retry
                        raise nre
                    raise nre.inner_exception
                except Exception as caught_ex:
                    if any(isinstance(caught_ex, allowed_ex) for allowed_ex in allowed):
                        log.error(
                            "Caught %s which was in the allowed exceptions list - not retrying!",
                            type(caught_ex).__name__,
                        )
                        raise caught_ex
                    final_exception = caught_ex
                    verbose_error = os.environ.get("AUTO_RETRY_VERBOSE", "false").lower() == "true"
                    log.error(
                        "Function wrapped in AutoRetry failed with the error: %s\n%s\n"
                        "Trying again in %d seconds (%d/%d retries)",
                        caught_ex,
                        traceback.format_exc() if verbose_error else "",
                        sleep,
                        retry,
                        max_retry,
                    )
                    # If we reach here, we're about to retry
                    time.sleep(sleep)
                    total_sleep += sleep
                    retry = retry + 1
                    sleep = 10 * retry * int(0.5 * retry)

            # If we reach here, retries expired
            log.error("Function wrapped in AutoRetry failed even after %d attempts!", max_retry)
            raise final_exception  # ex here should be the last exception

        return auto_retry_wrapper

    return decorator


class NoRetryException(Exception):
    """
    Exception that will cause a function decorated with `@auto_retry` to not retry.
    """

    def __init__(self, inner_exception: Exception):
        """Exception that will cause a function decorated with `@auto_retry` to not retry.

        Args:
            inner_exception (Exception): The exception that we want to raise without retrying
        """
        self.inner_exception = inner_exception
        super().__init__()


def ping(host, timeout=0, packets=0):
    """
    Args:
        host (str): The hostname.
        timeout (int): Timeout (sec) for the entire ping session (after which no matter how many pings, it will end).
        packets (int): The number of packets we would like to get in the amount of time was specified (timeout).
    Returns:
        True if host (str) responds to a ping request, False otherwise.
    """
    if not any([timeout, packets]):
        raise ValueError("Either a timeout, a packet limit, or both need to be set!")

    if platform.system() != "Linux":
        raise Exception("This function currently only supports Linux!")

    params = ["/usr/bin/ping"]
    if packets:
        params += ["-c", str(packets)]

    if timeout:
        params += ["-w", str(timeout)]

    params.append(host)

    with subprocess.Popen(params) as proc:
        return proc.wait() == 0


def dfs_path_resolver(path: str, debug=False) -> str:
    """Resolves a network path as a DFS path if it is one of the known paths in the database.
    Converts the prefix of the path to the actual server the DFS is resolved to.

    Arguments:
        path (str): The path to resolve
        debug (bool, optional): Use Sandbox Database for testing. Defaults to False.

    Raises:
        ValueError: If the path is not a network path, or if it's not fully qualified (with the ".intel.com" suffix)

    Returns:
        str: The resolved path

    """
    path = path.replace("\\", "/")  # Normalize

    if not path.startswith("//"):
        raise ValueError(f"Provided path {path} is not a network path!")
    if not (host := path.rsplit("/")[2]).endswith(".intel.com"):  # Make sure path is a fully qualified DNC path
        raise ValueError(f'Host "{host}" is not a fully qualified hostname (it lacks the .intel.com suffix)')

    DBAuth = DatabaseSandbox if debug else Database
    db = DBConnector(DBAuth.server, DBAuth.database, DBAuth.username, DBAuth.password)

    server_mapping = db.query_table("dfs_server_mapping")
    matches = [row for row in server_mapping if path.lower().startswith(row["prefix"].lower())]

    if not matches:
        # DFS path is not in the database, return the path as is and hope for the best
        return path

    if len(matches) == 1:
        match = matches[0]

    if len(matches) > 1:
        # Multiple matches found, choose the longest one
        match = max(matches, key=lambda x: len(x["prefix"]))

    prefix = match["prefix"]
    server = match["server"]
    share = prefix.rsplit("/", 1)[-1]
    # share = match["share_name"]
    return path.lower().replace(prefix.lower(), f"//{server}/{share}")
