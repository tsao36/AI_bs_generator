import os
import json
import logging
import base64
import getpass
import sys
import re
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from select import select

import certifi
from bs4 import BeautifulSoup

VER = 1.0

DATE_FORMAT = '%Y-%m-%d %H:%M:%S.%f%z'
DATE_FORMAT_FOR_QUERY = '%Y-%m-%d'
PRINT_DATE_FORMAT = '%Y-%m-%d %H:%M'
CA_MARKER = "-----BEGIN CERTIFICATE-----"

DEFAULT_CONFIG_FILE = 'config_wifi.json'
EXTERNAL_CA_NAME = "IntelSHA256RootCA-base64.crt"

FILES_DIRECTORY = "files"
LOGS_DIRECTORY = FILES_DIRECTORY + "/" + "logs"
FIELD_MAPS_DIRECTORY = FILES_DIRECTORY + "/" + "field_maps"
DICTIONARY_DIRECTORY = FILES_DIRECTORY + "/" + "dictionaries"
CONFIG_DIRECTORY = FILES_DIRECTORY + "/" + "configs"
CERTS_DIRECTORY = FILES_DIRECTORY + "/" + "certs"

SERVER_ACCESS_RETRY_COUNT = 5
SERVER_ACCESS_BACKOFF_MINUTES = 2
MAX_REPORT_FILE_COUNT = 20
UNUPDATED_JIRA_ISSUES = set()

MAX_SHAREPOINT_ATTACHMENT_FILE_SIZE = 200 * (1024**3)
INVALID_COMMENT_TEXT = "no_comment_number_folder"
API_ACCESS_SLEEP_TIME_SEC = 5


def pad_to_match_date_format(date_str):
    # Split main + fractional seconds
    if "." in date_str:
        main, frac = date_str.split(".")
        frac = frac.ljust(6, "0")  # pad to 6 digits
        ts = f"{main}.{frac}+0000"  # add timezone (UTC)
    else:
        ts = date_str + ".000000+0000"

    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f%z")
    return dt


def get_work_directory():
    return os.path.dirname(os.path.realpath(__file__))


def config_logging(config):
    # on windows need to force logging encoding
    if os.name == 'nt':
        encoding = 'utf-8'
    else:
        encoding = None

    rfh = RotatingFileHandler(
        filename=config["log_file_name"],
        mode='a',
        maxBytes=config["log_file_max_size"] * 1024 * 1024,
        backupCount=2,
        encoding=encoding,
        delay=0
    )

    logging.basicConfig(level=logging.INFO,
                        handlers=[rfh],
                        format="%(asctime)-15s %(levelname)-8s "
                               "| %(filename)-24s %(funcName)s() "
                               "[%(lineno)s]: %(message)s")


def print_log(text, to_print=True, to_log=True, log_verb="info"):
    if to_print:
        print(text)

    if to_log:
        if log_verb == "info":
            logging.info(text)
        elif log_verb == "error":
            logging.error(text)


def print_only(text):
    print_log(text, to_log=False)


def log_only(text, verb="info"):
    print_log(text, to_print=False, log_verb=verb)


def load_config(config_file, config_path=None):
    """
    Load the config file to be used in the tool
    :param config_file: the config file name
    :param config_path: the config file path
    :return: a config instance
    """
    curr_dir = os.path.join(get_work_directory(), CONFIG_DIRECTORY)

    if config_path is None:
        config_path = os.path.join(curr_dir, config_file)

    try:
        with open(config_path) as file:
            config = json.load(file)
    except (ValueError, FileNotFoundError) as e:
        print_log("could not load config file - " + config_path)
        print_log(e, to_print=True, log_verb="error")
        return None

    return config


# Passwords are stored locally (since this script is intended to be used by
# a demon), so this is just a simple (AND VERY BASIC) obfuscation mainly to
# protect against day dreamers who actually deliver the config file with their
# passwords...
def validate_passwords(config, config_file, force_validation=False):
    """
    This function will check the validity of existing stored passwords,
    and will ask for new password if needed.
    Passwords are stored locally (since this script is intended to be used by
    a demon), so this is just a simple (AND VERY BASIC) obfuscation mainly to
    protect against day dreamers who actually deliver the config file with their
    passwords...
    :param config: an instance of the config
    :param config_file: path for the config file name to write to
    :param force_validation: if True will force password insertion
    """

    should_write_config = False

    log_only("validating passwords, force_validation=" + str(
        force_validation))

    if config["jira_user"]["pass"] == "" or force_validation:
        p = getpass.getpass("enter JIRA password:")
        h = base64.b64encode(p.encode("utf-8"))
        config["jira_user"]["pass"] = h.decode('ascii')
        should_write_config = True

    if should_write_config:
        log_only("rewriting config")
        json_file = open(os.path.join(get_work_directory(), config_file), "w+")
        json_file.write(json.dumps(config, indent=4, sort_keys=True))
        json_file.close()


def clean_report_directory(path, file_count=MAX_REPORT_FILE_COUNT):
    """
    clean the report directory to contain maximum value of file_count - 1
    :param path: the path of the report directory
    :param file_count: the max file count to be in the report directory
    """
    if not os.path.exists(path):
        return

    list_of_files = os.listdir(path)
    full_path = [path + "/{0}".format(x) for x in list_of_files]
    safe_guard = 2 * file_count

    while len(list_of_files) >= file_count:
        oldest_file = min(full_path, key=os.path.getctime)
        os.remove(os.path.abspath(oldest_file))
        list_of_files = os.listdir(path)
        full_path = [path + "/{0}".format(x) for x in list_of_files]

        safe_guard -= 1
        if safe_guard == 0:
            break


def save_report_to_file(config, report, fetch_time, file_name=None):
    """
    Save the sync report locally as a file
    :param config: the config instance
    :param report: the report content (usually parsed as HTML)
    :param fetch_time: datetime type when the sync fetched occurred
    :param file_name: (optional) report file name to use (will be taken from
    config if None)
    """
    path = config["report_options"]["reports_path"]
    if file_name is None:
        file_name = config["report_options"]["reports_file_name"]\
            .format(fetch_time.strftime(DATE_FORMAT))

    # on windows - use specific file name (no spaces, etc)
    if os.name == 'nt':
        file_name = "hsd2jira_sync_report.html"

    if not os.path.exists(path):
        os.makedirs(path)

    clean_report_directory(path)

    with open(os.path.join(path, file_name), "w+") as file:
        file.write(report)


def modify_text_for_html(text):
    """
    convert the given text to be html friendly
    :param text: the given text to convert
    :return: the text as html friendly
    """
    text = text.replace('<', '&lt')
    text = text.replace('>', '&gt')

    return text


def convert_timestamp_str_to_utc(time_stamp):

    if '.' not in time_stamp:
        time_stamp = time_stamp.replace('Z', '.000Z')
    time_stamp = time_stamp.replace('Z', '-0000')
    item_date = datetime.strptime(time_stamp, '%Y-%m-%dT%H:%M:%S.%f%z')

    return item_date


def print_pretty_json(response):
    print(json.dumps(response.json(), indent=2, sort_keys=True))


def clean_attachment_file(config, attachment):

    path = FILES_DIRECTORY + "/" + config["hsd"]["attachments_path"]
    file_path = os.path.join(path, attachment.resource_name)
    log_only("deleting file locally " + str(file_path))

    try:
        os.remove(file_path)
    except OSError:
        print_log("error while deleting file {}".format(str(file_path)),
                  log_verb="error")
        log_only("{} | {}".format(sys.exc_info()[0], sys.exc_info()[1]),
                 verb="error")


def text_contain_assert_keywords(text, dictionary):

    for key_word in dictionary["assert_key_words"]:
        if key_word.lower() in text.lower():
            return True

    return False


def extract_assert_from_text(text, dictionary):

    # first we'll find the relevant line which contain assert log print
    str_lines = text.splitlines()
    assert_log = dictionary["assert_log"]

    for line in str_lines:

        # if we have a match - we'll try to extract the assert
        if assert_log in line:
            assert_num = re.findall(r'0x[0-9A-F]+', line, re.I)

            for cur_ass in assert_num:
                if len(cur_ass) == 10:
                    return cur_ass

    return None


def load_dictionary_file(config):

    curr_dir = get_work_directory()
    dict_path = os.path.join(curr_dir, DICTIONARY_DIRECTORY,
                             config['jira_new_issue']['dictionary_file'])

    # on windows - open from to current working path
    if os.name == 'nt':
        dict_path = DICTIONARY_DIRECTORY + "/" + config['jira_new_issue']['dictionary_file']

    try:

        with open(dict_path) as file:
            dictionary = json.load(file)
    except (ValueError, FileNotFoundError):
        print_log("error while loading dictionary file - " + dict_path,
                  log_verb="error")
        log_only("{} | {}".format(sys.exc_info()[0], sys.exc_info()[1]),
                 verb="error")
        return None

    return dictionary


def backoff(try_count):
    backoff_min = SERVER_ACCESS_BACKOFF_MINUTES * try_count
    try_time = datetime.now() + timedelta(minutes=backoff_min)

    print_log("retry {} - backing off for {} minutes (at {})".format(
        try_count, backoff_min, try_time.strftime("%H:%M:%S")))

    print_only("(press enter to run now...)")

    # Wait for user input (enter) or backoff timer
    rlist, wlist, xlist = select([sys.stdin.fileno()], [], [],
                                 backoff_min * 60)
    # in case of user input (enter) - we flush stdin for next iteration
    if rlist:
        os.read(sys.stdin.fileno(), 512)


def parse_html_to_text(html_str):
    """
    converts a str of html to a readable text
    :param html_str: the html str to parse
    :return: a readable text
    """
    soup = BeautifulSoup(html_str, 'html.parser')
    clean_text = soup.get_text()
    if clean_text is None:
        print_log("could not parse the html. return empty string")
        return ""

    return clean_text


def pad_milliseconds(date_str):
    """
    ensure exactly 3 digits in the fractional seconds part (.mmm).
    """
    m = re.match(r"(.*:\d{2})(?:\.(\d+))?$", date_str)
    if not m:
        return date_str
    base, frac = m.groups()
    if frac is None:
        return f"{base}.000"
    frac = frac.ljust(3, "0")[:3]
    return f"{base}.{frac}"


def create_bundled_cacert(root_cert_path):
    """
    this function will create a valid ssl certificate that will allow sending requests to HSD-ES.
    the flow in general:
    1. locate the personal .pem certificate installed locally using certifi.
    2. embed the root_cert (intel certificate) into the .pem certificate. (is it ok to save it locally?)
    3. return the path to the newly created embedded certificate.

    WE WRITE THE INTERNAL CONTENT(PERSONAL CERTIFI CONTENT) INTO THE EXTERNAL INTEL CERTIFICATE INPLACE,
    NOT CREATING COPIES.

    :param root_cert_path: the path to the external Intel certificate.
    :return: the same path of the external certificate path, after the changes embedded into it.
    """
    if not root_cert_path:
        print(f"No root certificate path provided, abort process.")
        return None
    # first trying to locate the personal .pem certificate
    try:
        internal_certifi_path = Path(certifi.where())
        if not internal_certifi_path:
            print(f"no path was found for valid internal certificate.")
            return None

        internal_content = internal_certifi_path.read_text()

    except Exception as e:
        print(f"error for certifi request is - {e}")
        return None

    # now getting to open the external intel certificate
    try:

        external_ca_path = Path(root_cert_path + f"/{EXTERNAL_CA_NAME}")
        # now check if the certificate is already embedded
        if external_ca_path.name == "IntelSHA256RootCA-base64_embedded.crt":
            print_log(f"SSL certificate is already embedded, no need to embed again, skipping.")

        external_content = external_ca_path.read_text().splitlines(keepends=True)

        new_lines = []
        inserted = False

        for line in external_content:

            if CA_MARKER in line and not inserted:
                # inserting the certifi content right after the "BEGIN" line
                new_lines.append(internal_content)
                if not internal_content.endswith("\n"):
                    new_lines.append("\n")
                inserted = True

            new_lines.append(line)
        if not inserted:
            raise ValueError(f"CA Marker {CA_MARKER} not found in the external intel certificate")

        # write to external intel ca
        external_ca_path.write_text("".join(new_lines))

        # renaming the file's name, so we will know that the file was changed, regardless the times the tool was
        # executed. (we need to run this function only one time for creating this cert)
        modified_path = rename_file(external_ca_path, "IntelSHA256RootCA-base64_embedded.crt")

        return modified_path

    except Exception as e:
        print(f"Error is - {e}")
        return None


def rename_file(file_path, new_name):
    """
    given a path and a new name, we will change a file's name and will return the new path.
    """
    path = Path(file_path)

    new_path = path.with_name(new_name)
    path.rename(new_path)

    return new_path
