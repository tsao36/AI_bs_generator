""" The main entrypoint for the DatabaseAPI class """

import time
import logging
import pyodbc
from .query_lib import Builds, Definitions, BuildNotFoundException, CoreNotFoundError, NightlyNotFound
from .query_lib._builds import DATE_FORMAT, NIGHTLY_WHERE, NotSupportedBuildType, NotANightly
from .query_lib._definitions import DomainNotFound, ModuleNotFound, FileNotMappedToModule, NoMatchingFWBranch
from .query_lib._skynet import Skynet, PackageType
from ._exceptions import NoResultsForQuery, NoResultsInTable, UpdateHasNoEffect

DEFAULT_DRIVER = "{ODBC Driver 18 for SQL Server}"

log = logging.getLogger("DatabaseAPI")


class DBConnector:
    """
    An instance of the Database Connector.
    """

    def __init__(
        self,
        server,
        db_name,
        username,
        password,
        driver=DEFAULT_DRIVER,
        encrypt=True,
        max_retry=10,
        trust_server_cert=True,
    ):
        self.server = server
        self.db_name = db_name
        self.username = username
        self.password = password
        self.driver = driver
        self.max_retry = max_retry

        self.connection_string = (
            f"DRIVER={driver};"
            f"SERVER={server};"
            f"DATABASE={db_name};"
            f"UID={username};"
            f"PWD={password};"
            f"Encrypt={'yes' if encrypt else 'no'};"
            f"{'TrustServerCertificate=yes;' if trust_server_cert else ''}"
        )

        self.builds = Builds(self)
        self.definitions = Definitions(self)
        self.skynet = Skynet(self)

    # Class Imports
    # pylint: disable=import-outside-toplevel

    from ._queries import (
        query_table,
        simple_query,
        get_row_count,
        get_single_row,
        get_primary_key,
        get_column_names,
        get_column_values,
        get_top_n_rows,
        get_top_row,
    )
    from ._updaters import delete_row, insert_to_table, update_table, delete_all_rows

    # pylint: enable=import-outside-toplevel

    def get_connection(self) -> pyodbc.Connection:
        """Creates a connection to the DB.
        Preferably, this is called for every query.

        Returns:
            [pyodbc.Connection]
        """
        # when autocommit is set to True, the database executes a commit automatically after every SQL statement
        # Implement a simple retry mechanism to handle transient connection errors (e.g.SSL provider closed connection).
        max_attempts = self.max_retry if hasattr(self, "max_retry") else 5
        last_exception = None
        for attempt in range(1, max_attempts + 1):
            try:
                return pyodbc.connect(self.connection_string, autocommit=True)
            except pyodbc.OperationalError as op_err:
                # OperationalError commonly wraps transient network/driver issues
                last_exception = op_err
                if attempt == max_attempts:
                    log.error(
                        "Failed to establish DB connection to %s after %d attempts",
                        self.server,
                        max_attempts,
                    )
                    raise
                log.warning(
                    "Transient DB connection error on attempt %d/%d: %s. Retrying in 10 seconds...",
                    attempt,
                    max_attempts,
                    str(op_err),
                )
                time.sleep(10)
                continue
            except pyodbc.Error as err:
                # For any other pyodbc errors, re-raise immediately (they may be configuration issues)
                log.exception("Unexpected pyodbc error while connecting to %s: %s", self.server, err)
                raise

        # If we exit the loop unexpectedly, raise the last exception we saw.
        if last_exception:
            raise last_exception
        # Fallback: try a final connect which will raise the appropriate exception
        return pyodbc.connect(self.connection_string, autocommit=True)

    def smart_execute(self, cursor: pyodbc.Cursor, *args, **kwargs) -> pyodbc.Cursor:
        """A smart cursor execute function which wraps the execute function with a retry on connection errors, that
        has an exponentially growing sleep time.
        Any error that's not a connection error will not trigger a retry and will just be raised normally.
        The maximum amount of retries allowed is define in the DBConnector init.

        NOTE THAT IF RECOVERY OCCURS, THE CONNECTION AND CURSOR OBJECTS WILL CHANGE!

        Args:
            cursor (pyodbc.Cursor): The cursor to execute the arguments for.

        Returns:
            pyodbc.Cursor: Since the cursor can change during smart execute, we return the final cursor
        """
        max_retry = self.max_retry
        for attempt in range(1, max_retry + 1):
            try:
                cursor.execute(*args, **kwargs)
                if attempt > 1:
                    log.warning("Recovered on attempt %d/%d!", attempt, max_retry)
                return cursor
            except pyodbc.Error as odbc_error:
                if attempt == max_retry:
                    log.error("Failed to connect even after %s retries!", max_retry)
                    raise  # re-raise exception
                if odbc_error.args[0] == "08S01":  # Communication error.
                    sleep_for = 2 ^ (attempt - 1)
                    log.warning(
                        "Connection to %s failed! Nuking connection and trying again in %d seconds (attempt %d/%d)...",
                        self.server,
                        sleep_for,
                        attempt,
                        max_retry,
                    )
                    time.sleep(sleep_for)
                    # Nuke the connection and retry.
                    cursor.connection.close()  # Note that this also closes the cursor
                    cursor = self.get_connection().cursor()
                    continue
                raise  # Re-raise any other exception

        # We shouldn't ever reach here
        raise NotImplementedError("How did you get here?!")
