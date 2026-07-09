import logging
import json
import sys
import os
import traceback
from typing import Union
from typing import List

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "APIs"))
import Sherlock
from DatabaseAPI import DBConnector

ALL = 70

FAIL = 60
CRITICAL = 50
FATAL = CRITICAL
ERROR = 40
WARNING = 30
WARN = WARNING
INFO = 20
DEBUG = 10
NOTSET = 0


class LoggingHandler(logging.Handler):
    """
    Logger handler class. Derived from logging.Handler.
    Catch all logs and keep them on buffer dictionary with next format:
    "<message number>": {
        "message": "<log message>",
        "pathname": "<file name where log came from>",
        "funcname": "<function name where log came from>",
        "lineno": <line number where log occur>,
        "excline": "<exception line>",
        "loglevel": "<log level>"
    }
    """

    def __init__(self):
        logging.Handler.__init__(self, level=logging.DEBUG)
        self.__buffer = {}

    def emit(self, record):
        """
        override Handler function "emit". This function
        will create structure from received log in format explained before.
        and keep created message of buffer dictionary.
        Args:
            record: logging handler log object
        """
        recordDict = record.__dict__
        newMessage = {}
        newMessage["message"] = recordDict["msg"]
        newMessage["pathname"] = recordDict["pathname"]
        newMessage["funcname"] = recordDict["funcName"]
        newMessage["lineno"] = recordDict["lineno"]
        newMessage["excline"] = ""
        newMessage["loglevel"] = recordDict["levelname"]
        if recordDict["exc_info"]:
            exc_type, exc_value, exc_traceback = recordDict["exc_info"]
            for frame in traceback.extract_tb(exc_traceback):
                pathname, lineno, funcname, line = frame
                if pathname != newMessage["pathname"]:
                    break
                newMessage["lineno"] = lineno
                newMessage["funcname"] = funcname
                newMessage["excline"] = line

        messageNumber = str(len(self.__buffer) + 1)
        self.__buffer[messageNumber] = newMessage

    def get_buffer(self) -> dict:
        """
        Returns:
            return current buffer
        """
        return self.__buffer


class ExtLogger(logging.Logger):
    """
    ExtLogger class derived from logging.Logger.
    Use same functionality as a regular logger with next additions:
    new log level "fail" defined. Ability to save all logs to buffer
    then write messages from buffer to db or file in json format.
    """

    def __init__(self, name: str):
        logging.Logger.__init__(self, name)
        self.__handler = None

    def add_handler(self, loggerHandler: LoggingHandler):
        """
        Init logger with Logging Handler
        Args:
            loggerHandler: LoggingHandler object
        """
        self.__handler = loggerHandler
        self.addHandler(loggerHandler)

    def fail(self, msg, *args, **kwargs):
        """
        Log 'msg % args' with severity 'FAIL'.

        To pass exception information, use the keyword argument exc_info with
        a true value, e.g.

        logger.fail("Houston, we have a %s", "thorny problem", exc_info=1)
        """
        if self.isEnabledFor(FAIL):
            self._log(FAIL, msg, args, **kwargs)

    def show_buffer(self, *messageType: Union[int]):
        """
        print messages from buffer by message type.
        ALL by default will print all buffer. e.g ERROR will print
        only messages with log level ERROR.
        Args:
            messageType: ALL,ERROR,FAIL, etc...
        """
        typeList = list(messageType)
        data = self.__build_json(typeList)
        self.info(data)

    def get_buffer(self, *messageType: Union[int]) -> str:
        """
        Will return buffer in json format to user by message type.
        ALL by default will return all buffer. e.g ERROR will return
        only buffer contains log level ERROR.
        Args:
            messageType: ALL,ERROR,FAIL, etc...
        Returns:
            string contains buffer data in json format.
        """
        typeList = list(messageType)
        data = self.__build_json(typeList)
        return data

    def write_to_file(self, jsonName: str, *messageType: Union[int]):
        """
        Write data from buffer by message type to file in json format.
        ALL by default will print all buffer.
        e.g ERROR will write only messages with log level ERROR.
        Args:
            jsonName: json file name (without extension)
            messageType: ALL,ERROR,FAIL, etc...
        """
        try:
            typeList = list(messageType)
            data = self.__build_json(typeList)
            with open(f"{jsonName}.json", "w") as outfile:
                outfile.write(data)
        except Exception as ex:
            self.warning(f"Exception while writing buffer to file: {str(ex)}")

    def write_to_db(self, table: str, rowId: str, columnName: str, *messageType: Union[int]):
        """
        Write data from buffer by message type to DB in json format.
        ALL by default will print all buffer.
        e.g ERROR will write only messages with log level ERROR.
        Args:
            table: DB table name
            rowId: row id to modify
            columnName: column name to modify
            messageType: ALL,ERROR,FAIL, etc...
        """
        try:
            typeList = list(messageType)
            data = self.__build_json(typeList)
            updateDict = {columnName: data}
            con = DBConnector(
                Sherlock.Database.server,
                Sherlock.Database.database,
                Sherlock.Database.username,
                Sherlock.Database.password,
            )
            con.update_table(table, rowId, updateDict)
        except Exception as ex:
            self.warning(f"Exception while writing to DB: {str(ex)}")

    def __build_json(self, messageType: List[int]) -> str:
        """
        This function create string from buffer in json format.
        Args:
            messageType: ALL,ERROR,FAIL, etc...
        Returns:
            data string in json format.
        """
        if not self.__handler:
            return ""
        buffer = self.__handler.get_buffer()
        data = ""
        if messageType == [] or messageType == ALL:
            data = json.dumps(buffer, indent=4)
        else:
            filteredBuffer = {}
            levelsName = [logging.getLevelName(level) for level in messageType]
            for value in buffer.values():
                if value["loglevel"] in levelsName:
                    filteredBuffer[str(len(filteredBuffer) + 1)] = value
            data = json.dumps(filteredBuffer, indent=4)
        return data


ExtLogger.manager.setLoggerClass(ExtLogger)
logging.addLevelName(FAIL, "FAIL")


def get_logger(
    loggerName="root", loggerFormat="[%(asctime)s][%(filename)s][%(funcName)s][%(levelname)s] %(message)s"
) -> ExtLogger:
    """
    Get new logger
    Args:
        loggerName (str): Logger name, by default "root"
        loggerFormat (str): Logger format.
    Returns:
        New extend logger object
    """
    logging.basicConfig(format=loggerFormat, level=logging.DEBUG, force=True)
    logger = ExtLogger.manager.getLogger(loggerName)
    loggerHandler = LoggingHandler()
    formatter = logging.Formatter(loggerFormat)
    loggerHandler.setFormatter(formatter)
    logger.add_handler(loggerHandler)
    return logger
