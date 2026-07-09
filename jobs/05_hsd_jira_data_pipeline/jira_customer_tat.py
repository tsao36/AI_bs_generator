""" Generate customer bugs TCCB statistics from JIRA """

from datetime import datetime, date
import os
import sys
import logging
import argparse
import re
from dataclasses import dataclass, fields, is_dataclass, field
from xml.etree import ElementTree as ET
import pandas as pd
import urllib3
import snowflake.connector
import psycopg2
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None
from jira.exceptions import JIRAError

# pylint: disable=wrong-import-position
# internal includes inside the APIs submodule
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "APIs"))
import jira_api
import Sherlock

# pylint: enable=wrong-import-position

# suppress HTTP request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(format="%(asctime)s [%(levelname)s][%(name)s] %(message)s")
log = logging.getLogger("CUSTOMER_BUGS")


def _load_env() -> None:
    if load_dotenv is None:
        return
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip() or default


def _get_ips_snowflake_config() -> dict[str, str]:
    _load_env()
    return {
        "user": _env("SNOWFLAKE_IPS_USER"),
        "password": _env("SNOWFLAKE_IPS_PASSWORD"),
        "role": _env("SNOWFLAKE_IPS_ROLE"),
        "account": _env("SNOWFLAKE_IPS_ACCOUNT"),
        "warehouse": _env("SNOWFLAKE_IPS_WAREHOUSE"),
        "database": _env("SNOWFLAKE_IPS_DATABASE"),
    }

# definitions
SIGHTING = "Open->Sighting"
OPEN = "Open"
IN_PROGRESS = "In Progress"
PENDING = "Pending"
JIRA_TAT = "JIRA TAT"
DB_NA = "NA"
# take the beginning of the next year
DB_FUTURE_DATE = datetime(year=date.today().year + 1, month=1, day=1)


@dataclass
class IpsBugData:
    """
    struct for IPS bug data
    """

    ips_case_number: int = 0
    ips_url: str = DB_NA
    ips_status: str = DB_NA
    ips_sub_status: str = DB_NA
    ips_title: str = DB_NA
    ips_priority: str = DB_NA
    ips_created_date: datetime = DB_FUTURE_DATE
    ips_closure_status: str = DB_NA
    ips_env_details: str = DB_NA
    ips_found_in_build: str = DB_NA
    ips_hardware: str = DB_NA
    ips_platform: str = DB_NA
    ips_oem: str = DB_NA
    ips_odm: str = DB_NA
    ips_category: str = DB_NA
    ips_jira_promo_status: str = DB_NA
    ips_reporter_account_name: str = DB_NA
    ips_owner_name: str = DB_NA
    ips_owner_email: str = DB_NA
    ips_close_pending_date: datetime = DB_FUTURE_DATE
    ips_closed_date: datetime = DB_FUTURE_DATE
    ips_jira_id: str = DB_NA
    ips_reporter_account_geo: str = DB_NA
    ips_os: str = DB_NA
    ips_last_modified_date: datetime = DB_FUTURE_DATE


@dataclass
class JiraAnalysis:
    """
    JIRA extended analysis conclusions
    """

    num_of_comments_by_reporter: int = 0
    num_of_total_comments: int = 0
    avg_reporter_response_time_hour: int = 0
    did_reporter_reply_to_questions: bool = True


@dataclass
class JiraBugData:
    """
    struct for JIRA bug data
    """

    jira_id: str = DB_NA
    jira_title: str = DB_NA
    jira_exposure: str = DB_NA
    jira_created_date: datetime = DB_FUTURE_DATE
    jira_closed_date: datetime = DB_FUTURE_DATE
    jira_implemented_date: datetime = DB_FUTURE_DATE
    jira_verify_date: datetime = DB_FUTURE_DATE
    jira_affected_version: str = DB_NA
    jira_initial_component: str = DB_NA
    jira_final_component: str = DB_NA
    jira_is_sw_change: bool = False
    jira_state_reason: str = DB_NA
    jira_status: str = DB_NA
    jira_platform: str = DB_NA
    jira_nic: str = DB_NA
    jira_os: str = DB_NA
    jira_reporter_name: str = DB_NA
    jira_reporter_email: str = DB_NA
    jira_tat_hours: int = 0
    jira_sighting_hours: int = 0
    jira_open_hours: int = 0
    jira_in_progress_hours: int = 0
    jira_pending_hours: int = 0
    jira_customer_name: str = DB_NA
    jira_url: str = DB_NA
    jira_analysis: JiraAnalysis = field(default_factory=JiraAnalysis)
    jira_found_by: str = DB_NA
    jira_team: str = DB_NA


@dataclass
class MergedBugData:
    """
    merged struct of JIRA and IPS data
    """

    ips_data: IpsBugData = field(default_factory=IpsBugData)
    jira_data: JiraBugData = field(default_factory=JiraBugData)
    bug_project: str = DB_NA
    # the time since IPS was filled till JIRA promotion
    ips_tat_till_jira_hours: int = 0
    is_ips_promoted_to_jira: bool = False
    customer: str = DB_NA
    reporter: str = DB_NA
    customer_closed_date: datetime = DB_FUTURE_DATE


class IpsBug:
    """
    TODO: need to update this and add link to WIKI for snowflake
    class handling all IPS bugs
    IPS is Intel external tracking system accessible to customers
    caution: for this class to work, need to configure the DB on the server
    https://wiki.ith.intel.com/display/BIBigData/Impala+ODBC+Driver
    https://wiki.ith.intel.com/display/BIBigData/Impyla%3A+Python+Client+for+Impala
    AGS permissions:
    >>> "BI BIG DATA - HADOOP - END USERS - PRODUCTION - IAH IPS ANALYTICS -BUSINESS ANALYST"
    """

    def __init__(self, created_year):
        log.info("IPS CTOR is called")

        # IPS fields translator
        self.__ips_fields_map = {
            "ips_case_number": "CASE_NBR",
            "ips_url": "CASE_ID",
            "ips_status": "STATUS_TXT",
            "ips_sub_status": "SUB_STATUS_TXT",
            "ips_title": "SUBJECT_TXT",
            "ips_priority": "PRIORITY_TXT",
            "ips_created_date": "CASE_CREATED_DTM",
            "ips_closure_status": "CASE_CLOSURE_TXT",
            "ips_env_details": "ENV_DETAIL_DSC",
            "ips_category": "CORE_ISSUE_SUBCATEGORY_EXTERNAL_TXT",
            "ips_jira_promo_status": "PROMO_STATUS_TXT",
            "ips_reporter_account_name": "ACCOUNT_NM",
            "ips_owner_name": "CASE_OWNER_NM",
            "ips_owner_email": "CASE_OWNER_EMAIL_TXT",
            "ips_close_pending_date": "DATE_TIME_CLOSE_PENDING_DTM",
            "ips_closed_date": "CASE_CLOSURE_DTM",
            "ips_jira_id": "BACKEND_ID",
            "ips_reporter_account_geo": "ACCOUNT_SUB_GEOGRAPHIC_NM",
            "ips_last_modified_date": "LAST_MODIFIED_DTM",
        }

        # one of IPS fields is "Environment Details" which has additional data
        self.__ips_env_details_fields_map = {
            "ips_found_in_build": "Found In Build",
            "ips_hardware": "Hardware",
            "ips_platform": "Platforms",
            "ips_oem": "OEM",
            "ips_odm": "ODM",
            "ips_os": "Operating System",
        }

        # search for issues starting this year till now 'case_cre_dt'
        self.__created_year = created_year

        # the URL prefix for the IPS bugs, need to add /<case_id>/view to get the full link
        self.__ips_url = "https://intel.lightning.force.com/lightning/r/Case"

    def __del__(self):
        log.info("IPS DTOR is called")

    def __parse_env_details_table(self, ips_bug_data: IpsBugData) -> None:
        """
        fill specific fields in ips_bug_data from "Environment Details" variable
        this is a 2D table with specific key/val data
        if the desired key is not found, "NA" will be filled instead
        for DB space optimization, delete this field after use
        assumption: 'env_details' and 'case_number' fields are not empty
        """
        table_data = {}

        # table has <b> and </b> which breaks the XML parser, remove them
        xml_table_str = ips_bug_data.ips_env_details.replace("<b>", "").replace("</b>", "")

        # parse table
        try:
            table_obj = ET.XML(xml_table_str)
        except ET.ParseError:
            # table doesn't exist or not in a valid XML format
            # skip the parse, some fields will be empty
            log.info("[%d] invalid environment data (corrupted or non existing xml)", ips_bug_data.ips_case_number)
            # delete the data
            ips_bug_data.ips_env_details = DB_NA
            return

        # generate key val from each row
        tbl_rows = iter(table_obj)
        for tbl_row in tbl_rows:
            values = [col.text for col in tbl_row]
            table_data[values[0]] = values[1]

        # fix missing keys
        for key, val in self.__ips_env_details_fields_map.items():
            new_val = DB_NA
            if val in table_data:
                new_val = table_data[val]
                # in case value is None set back to default
                if not new_val:
                    new_val = DB_NA
                elif ";" in new_val:
                    # sometimes there are multiple strings separated by ';'
                    # not sure if same values replicated or different one
                    # anyway, for our purpose, take the first one
                    match = re.findall(r"^([^;]+)", new_val)
                    if match:
                        new_val = match[0]

            setattr(ips_bug_data, key, new_val)

        # DB space optimization
        # the "env_details" field is relatively large, since contains lots of irrelevant info
        # in addition, it is in xml format having tables and consumes much space
        # all the relevant data was already extracted and by zeroing this field, we can save ~80% of DB size
        # why do we still set value here and totally do not delete it from the struct?
        # [1] it is not that simple to delete attribute from dataclass
        # [2] there are generic functions adding data to DB, based on the dataclass struct
        #     therefore, better to avoid some tricks here and not complicate the DB addition code
        ips_bug_data.ips_env_details = DB_NA

    def get_all_bugs(self) -> list:
        """
        returns list of dictionaries with IPS data, for all IPS found in the jira_issues
        every JIRA should have link to IPS, however, if it doesn't have, this bug will not appear in the returned list
        """
        # list of IPS cases
        ips_bug_list = []

        ips_cfg = _get_ips_snowflake_config()
        db_name = f"{ips_cfg['database']}.sales_support_premier_analysis"
        table_name = "fact_case"
        ips_team = "WCS"

        # proxy is mandatory
        os.environ["HTTP_PROXY"] = "http://proxy-dmz.intel.com:912"
        os.environ["HTTPS_PROXY"] = "http://proxy-dmz.intel.com:912"
        os.environ["NO_PROXY"] = f"{ips_cfg['account']}.snowflakecomputing.com"

        # connect to DB
        db_engine = snowflake.connector.connect(
            user=ips_cfg["user"],
            password=ips_cfg["password"],
            role=ips_cfg["role"],
            account=ips_cfg["account"],
            warehouse=ips_cfg["warehouse"],
            database=ips_cfg["database"],
            schema=f"{db_name}.{table_name}",
        )

        cursor = db_engine.cursor()

        ips_query = (
            f"SELECT * FROM {db_name}.{table_name} WHERE "
            f"case_created_dtm > '{self.__created_year}-01-01' AND assigned_queue_ss_one_dsc = '{ips_team}'"
        )

        # run query
        log.info("IPS query: %s", ips_query)
        cursor.execute(ips_query)

        rows = pd.DataFrame(cursor.fetch_pandas_all())
        log.info("IPS: [%d] bugs were found", len(rows))

        # close connection to DB
        cursor.close()
        db_engine.close()

        # remove proxy otherwise JIRA will not work :)
        del os.environ["HTTP_PROXY"]
        del os.environ["HTTPS_PROXY"]
        del os.environ["NO_PROXY"]

        # parse the data into list of IpsBugData
        for row in rows.itertuples():
            ips_bug_data = IpsBugData()

            # get all fields
            for key, val in self.__ips_fields_map.items():
                ips_val = getattr(row, val)

                # handling types
                # using annotations will be faster, but for some reason, pylint doesn't like it
                # ips_bug_data.__annotations__[key]
                key_type = next(f.type for f in fields(IpsBugData) if f.name == key)

                if key_type == str:
                    ips_val = ips_val if ips_val else DB_NA
                elif key_type == datetime:
                    try:
                        # try to parse the time, if fails, means that this is not a real time
                        # this is not an error, not all the time fields really have time
                        # (e.g close date of a bug which is not closed yet)
                        ips_val = datetime.fromisoformat(str(ips_val))
                    except ValueError:
                        ips_val = DB_FUTURE_DATE
                elif key_type == bool:
                    ips_val = bool(ips_val)
                elif key_type == int:
                    ips_val = int(ips_val)

                setattr(ips_bug_data, key, ips_val)

            # fill additional fields
            self.__parse_env_details_table(ips_bug_data)

            # fix the closed date - take the min between the closed date and closed pending
            # update both fields to avoid further confusion when making reports
            ips_bug_data.ips_closed_date = min(ips_bug_data.ips_closed_date, ips_bug_data.ips_close_pending_date)
            ips_bug_data.ips_close_pending_date = ips_bug_data.ips_closed_date

            # generate url
            # the actual URL doesn't appear in the parameters, however, can be generated by case_id
            # override this variable and construct the correct URL
            ips_bug_data.ips_url = f"{self.__ips_url}/{ips_bug_data.ips_url}/view"

            # add bug to list
            ips_bug_list.append(ips_bug_data)

        return ips_bug_list


class JiraBug(jira_api.Jira):
    """
    Class handling all JIRA bugs
    """

    def __init__(self, created_year):
        super().__init__(False)
        log.info("successfully connected to JIRA")
        self.__created_year = created_year
        self.__date_format = "%Y-%m-%dT%H:%M:%S.%f%z"

    def __get_linked_bugs(self, bug: object) -> list:
        """
        returns list of linked bugs (duplicates) for a specific bug
        """
        bug_set_str = set()
        bug_list_obj = []

        for history in super().get_jira().issue(bug.key, expand="changelog").changelog.histories:
            for item in history.items:
                if item.field == "Link":
                    bug_set_str.add(item.to)

        # convert linked bugs to jira objects
        for bug_str in bug_set_str:
            try:
                bug_list_obj.append(super().get_jira().issue(bug_str))
            except JIRAError:
                # issue is not found in JIRA - probably was deleted
                log.error("%s is not found in JIRA", bug_str)

        return bug_list_obj

    def __is_bug_fixed_as_sw_change(self, bug: object, recursion_depth=0) -> bool:
        """
        returns True in case bug was closed as SW change (Fixed)
        recursive function, which goes to duplicated bugs if needed
        recursion_depth is meant to avoid infinite loop in case of cyclic bug pointing
        """
        # verify infinite loop
        recursion_depth += 1
        if recursion_depth > 4:
            # cyclic pointing
            log.warning("[%s] duplicate bugs having cyclic link", bug.key)
            return False

        # get the bug current status
        try:
            status = bug.fields.status.name
            state_reason_value = self.__extract_state_reason_value(bug)
        except AttributeError as err:
            log.warning("[%s] missing expected JIRA field while checking SW-change: %s", bug.key, err)
            return False

        if status in ["Closed", "Verify"] and state_reason_value:
            # check state reason
            if "Duplicate" in state_reason_value:
                # this is a duplicate bug - check his father
                dup_bug_list = self.__get_linked_bugs(bug)

                # sanity check
                if not dup_bug_list:
                    # should not happen
                    log.error("[%s] is marked as duplicate, however, no bugs were linked", bug.key)
                    # bug was surely not fixed
                    return False

                # go over all the duplicate bugs recursively
                # if one of them was fixed, we assume that this one (which duplicates it) is fixed
                for dup_bug in dup_bug_list:
                    if self.__is_bug_fixed_as_sw_change(dup_bug, recursion_depth):
                        return True

                # none of the dup bugs was fixed
                return False

            if "Fixed" in state_reason_value:
                # either "fixed" or "fixed by parent"
                return True

        # implemented can be only on SW change
        if status == "Implemented":
            return True

        return False

    @staticmethod
    def __get_final_component(bug: object) -> str:
        """
        returns the final component bug was closed on
        """
        if bug.fields.components:
            return bug.fields.components[0].name

        log.warning("final component is not found for JIRA: %s", bug.key)
        return DB_NA

    def __get_initial_component(self, bug: object) -> str:
        """
        returns the initial component bug was filed on
        this is extracted by the first transition of None -> component
        """
        for history in super().get_jira().issue(bug.key, expand="changelog").changelog.histories:
            for item in history.items:
                if item.field == "Component":
                    if item.fromString is None:
                        return item.toString
                    # if we have "from", this is the component
                    return item.fromString

        # no transition was found, meaning, the initial component equals to the final
        return self.__get_final_component(bug)

    def __get_jira_advanced_analysis(self, bug: object) -> JiraAnalysis:
        """
        performs inner analysis based on comments and fills the JiraAnalysis data
        """
        comments_db = {}
        last_comment_time = datetime.strptime(bug.fields.created, self.__date_format).replace(tzinfo=None)
        last_question_to_reporter_time = last_comment_time
        reporter_response_time_hour = []
        jira_analysis = JiraAnalysis()
        jira_analysis.avg_reporter_response_time_hour = -1

        # sanity check that reporter exists
        if not bug.fields.reporter:
            log.error("no reporter was found for %s", bug.key)
            return jira_analysis

        # generate list of possible keywords (try all combinations with name)
        reporter_name_keywords = [
            # rfridbur
            bug.fields.reporter.name,
            # roi.fridburg@intel.com
            bug.fields.reporter.emailAddress,
            # Roi
            bug.fields.reporter.displayName.split(",")[0],
            # Fridburg
            bug.fields.reporter.displayName.split(",")[-1],
        ]

        for comment in super().get_jira().issue(bug.key, expand="renderedFields").fields.comment.comments:
            # sanity check - comment has 'real' author, including
            # try to filter out faceless accounts, some don't have email
            if comment.author and comment.author.emailAddress:
                # get the comment time
                comment_time = datetime.strptime(comment.created, self.__date_format).replace(tzinfo=None)

                # if comment already exists, in means that it is an 'edit'
                # replace the orig comment by the new (edited) comment
                comments_db[comment.id] = {
                    "comment_time": comment_time,
                    "comment_author": comment.author.name,
                    "is_comment_by_reporter": (comment.author.name == bug.fields.reporter.name),
                    "is_question_to_reporter": False,
                }

                # look for questions to the reporter (not by reporter)
                if comment.author.name != bug.fields.reporter.name:
                    # this comment is not by reporter
                    # look if there is a question targeted to reporter
                    if "?" in comment.body:
                        # clean the comment body, remove all special chars
                        # e.g. roi- --> roi, roi: --> roi
                        comment_body_str = re.sub("[^a-z]+", " ", comment.body.lower())
                        # create unique list of words
                        # looking for exact keyword match, since sometimes reporter
                        # name can be part of a word, e.g. "he" --> "the" or "tu" --> "tune"
                        comment_body_list = set(comment_body_str.split(" "))
                        # remove empty string
                        if "" in comment_body_list:
                            comment_body_list.remove("")

                        for keyword in reporter_name_keywords:
                            # look if any keyword has exact match
                            if keyword.strip().lower() in comment_body_list:
                                # we have a question to the reporter
                                # update the comment DB
                                comment_data = comments_db[comment.id]
                                comment_data["is_question_to_reporter"] = True
                                break

        # count reporter comments
        jira_analysis.num_of_comments_by_reporter = len(
            [comment for comment in comments_db.values() if comment["is_comment_by_reporter"]]
        )

        jira_analysis.num_of_total_comments = len(comments_db)

        # calculate average reporter reply time
        was_answer_provided = True
        # assumption: comments are sorted by time, old to new
        for comment in comments_db.values():
            # check if this is a question
            if comment["is_question_to_reporter"]:
                # log the question time only if this is a new question,
                # meaning, answer was provided to the previous one
                # if this is another question, but previous one is still unanswered
                # keep the original time
                if was_answer_provided:
                    # new question, since answer was provided for the previous one
                    last_question_to_reporter_time = comment["comment_time"]
                    was_answer_provided = False

            # check if this is an answer
            elif not was_answer_provided and comment["is_comment_by_reporter"]:
                # this is a reply
                time_delta_sec = (comment["comment_time"] - last_question_to_reporter_time).total_seconds()
                reporter_response_time_hour.append(time_delta_sec / 3600)
                # reset question flag
                was_answer_provided = True

        # make sure there are answers ti questions
        if reporter_response_time_hour:
            jira_analysis.avg_reporter_response_time_hour = round(
                sum(reporter_response_time_hour) / len(reporter_response_time_hour)
            )
        else:
            # list is empty - in such case avg_response_time_hour will stay -1
            # when looking in PBI, need to ignore -1 in all queries later
            if not was_answer_provided:
                # there were no replies by reporter
                log.info(
                    "[%s] there were %d question(s) asked to reporter and no reply was provided",
                    bug.key,
                    len([comment for comment in comments_db.values() if comment["is_question_to_reporter"]]),
                )
                # reporter was asked direct questions and never replied
                jira_analysis.did_reporter_reply_to_questions = False

        return jira_analysis

    @staticmethod
    @staticmethod
    def __get_state_reason(bug: object) -> str:
        """
        returns the current state reason of the bug
        """
        try:
            status = bug.fields.status.name
        except AttributeError:
            return DB_NA

        state_reason_value = JiraBug.__extract_state_reason_value(bug)

        if status in ["Closed", "Verify"] and state_reason_value:
            return state_reason_value

        if status == "Implemented":
            return "Fixed"

        return DB_NA

    @staticmethod
    def __extract_state_reason_value(bug: object) -> str:
        """Return the raw state reason value from known custom fields."""
        candidate_fields = ("customfield_10218", "customfield_10208")
        raw_fields = getattr(bug, "raw", {}).get("fields", {})
        for field_name in candidate_fields:
            field_value = getattr(bug.fields, field_name, None)
            if not field_value:
                field_value = raw_fields.get(field_name)
            if not field_value:
                continue

            option_value = getattr(field_value, "value", None)
            if option_value:
                return str(option_value)

            if isinstance(field_value, str):
                return field_value
            if isinstance(field_value, (list, tuple)):
                for option in field_value:
                    opt_val = getattr(option, "value", None) or (option if isinstance(option, str) else None)
                    if opt_val:
                        return str(opt_val)

        return ""

    @staticmethod
    def __get_status(bug: object) -> str:
        """
        returns the current status of the bug
        """
        return bug.fields.status.name

    @staticmethod
    def __get_platform(bug: object) -> str:
        """
        returns the platform bug was filed on
        the "platform" field is a list, need to choose the recent one
        """
        # using some hack: there is no easy way to know which platform is newer
        # e.g. MTL is newer than RPL, but how can I know that? need to go to other DBs
        # this is an overkill, therefore, use the JIRA field ID
        # it is assumed that these numbers are monotonically rising, therefore,
        # if number is higher, it means that platform was added later, meaning, it is newer
        # yes, not perfect, but for customer issues, we do not expect a single bug to
        # be filed on multiple platforms. this can happen for validation, but not for customers
        platform_id = 0
        platform_name = None

        try:
            for platform in bug.fields.customfield_10242:
                if int(platform.id) > platform_id:
                    platform_id = int(platform.id)
                    platform_name = platform.value
        except (TypeError, AttributeError):
            return DB_NA

        return platform_name

    @staticmethod
    def __get_hardware(bug: object) -> str:
        """
        returns the hardware (NIC) bug was filed on
        the "hardware" field is a list, need to choose the recent one
        """
        # using same hack as in __get_platform()
        hardware_id = 0
        hardware_name = None

        try:
            for hardware in bug.fields.customfield_10223:
                if int(hardware.id) > hardware_id:
                    hardware_id = int(hardware.id)
                    hardware_name = hardware.value
        except (TypeError, AttributeError):
            return DB_NA

        return hardware_name

    @staticmethod
    def __get_os(bug: object) -> str:
        """
        returns the OS bug was filed on
        the "os" field is a list, need to choose the recent one
        """
        # using same hack as in __get_platform()
        os_id = 0
        os_name = None

        try:
            for operating_system in bug.fields.customfield_10277:
                if int(operating_system.id) > os_id:
                    os_id = int(operating_system.id)
                    os_name = operating_system.value
        except (TypeError, AttributeError):
            return DB_NA

        return os_name

    @staticmethod
    def __get_exposure(bug: object) -> str:
        """
        returns the bug exposure
        """
        return bug.fields.customfield_10252.value

    @staticmethod
    def __get_title(bug: object) -> str:
        """
        returns the bug subject/title
        """
        return bug.fields.summary

    @staticmethod
    def __get_reporter_name(bug: object) -> str:
        """
        returns the bug reporter
        """
        if bug.fields.reporter:
            return bug.fields.reporter.displayName

        # try creator if reporter is not found
        if bug.fields.creator:
            return bug.fields.creator.displayName

        log.warning("reporter is not found for JIRA: %s", bug.key)
        return DB_NA

    @staticmethod
    def __get_reporter_email(bug: object) -> str:
        """
        returns the bug reporter
        """
        if bug.fields.reporter:
            return bug.fields.reporter.emailAddress

        # try creator if reporter is not found
        if bug.fields.creator:
            return bug.fields.creator.emailAddress

        return DB_NA

    @staticmethod
    def __get_customer_name(bug: object) -> str:
        """
        returns the customer name
        """
        try:
            return bug.fields.customfield_10207.value
        except (TypeError, AttributeError):
            return DB_NA

    def __get_url(self, bug: object) -> str:
        """
        returns the JIRA bug URL
        """
        # get the JIRA server
        return f"{Sherlock.Jira.server}browse/{bug.key}"

    def __get_created_date(self, bug: object) -> datetime:
        """
        returns the bug created date
        """
        # '2022-07-06T09:47:50.000+0300'
        # remove the TZ
        return datetime.strptime(bug.fields.created, self.__date_format).replace(tzinfo=None)

    def __get_closed_date(self, bug: object) -> datetime:
        """
        returns the bug closed date
        """
        # '2022-07-06T09:47:50.000+0300'
        # remove the TZ
        if bug.fields.customfield_10253:
            return datetime.strptime(bug.fields.customfield_10253, self.__date_format).replace(tzinfo=None)

        # if bug is not closed, we don't have any date
        # set something in future, since we still need datetime variable
        return DB_FUTURE_DATE

    def __get_implemented_date(self, bug: object) -> datetime:
        """
        returns the bug implemented date (if exists)
        """
        # '2022-07-06T09:47:50.000+0300'
        # remove the TZ
        if bug.fields.customfield_10575:
            return datetime.strptime(bug.fields.customfield_10575, self.__date_format).replace(tzinfo=None)

        # if bug is not implemented, we don't have any date
        # set something in future, since we still need datetime variable
        return DB_FUTURE_DATE

    def __get_verify_date(self, bug: object) -> datetime:
        """
        returns the bug verify date (if exists)
        """
        # '2022-07-06T09:47:50.000+0300'
        # remove the TZ
        if bug.fields.resolutiondate:
            return datetime.strptime(bug.fields.resolutiondate, self.__date_format).replace(tzinfo=None)

        # if bug is not moved to verify, we don't have any date
        # set something in future, since we still need datetime variable
        return DB_FUTURE_DATE

    @staticmethod
    def __get_affected_version(bug: object) -> str:
        """
        returns the affected version (cleaned - numbers only)
        in case of several, take the first one
        """
        if bug.fields.versions:
            ver = bug.fields.versions[0].name
            # version can sometimes be with REL/rel prefix and sometimes can have other format (free text)
            # try to spot something looking like a.b or a.b.c or a.b.c.d
            # need to align all to be a.b.c
            # 22.220.0.1
            if match := re.findall(r"(\d+\.\d+\.\d+)\.\d+", ver):
                # take first 3 bytes (22.220.0)
                return match[0]

            # 22.220.0
            if match := re.findall(r"\d+\.\d+\.\d+", ver):
                # take as is (22.220.0)
                return match[0]

            # 22.220
            if match := re.findall(r"\d+\.\d+", ver):
                # add 0 in 3rd byte (22.220.0)
                return f"{match[0]}.0"

        return DB_NA

    @staticmethod
    def __convert_time_str_to_object(time: str) -> datetime:
        """
        convert JIRA time format to datetime object
        """
        # '2022-09-05T20:11:46.093+0300'
        # remove everything after '.' --> '2022-09-05T20:11:46'
        time_without_ms = re.sub(r"\..*", "", time)
        datetime_format = "%Y-%m-%dT%H:%M:%S"
        return datetime.strptime(time_without_ms, datetime_format)

    @staticmethod
    def __calc_time_diff_hours(last_time: datetime, first_time: datetime) -> int:
        """
        calculate the time difference between last_time (new) and first_time (old)
        time diff is returned in hours, rounded to int
        in case any of the times is None, 0 will be returned
        """
        if last_time and first_time:
            return round((last_time - first_time).total_seconds() / 3600)

        # one of the dates is not available, this is not necessarily a bug
        return 0

    def __subtract_sighting_from_actual_tat(self, actual_dict: dict, sighting_dict: dict) -> None:
        """
        subtract all sighting states from the actual and update the actual_dict
        """
        for state in sighting_dict:
            if state in actual_dict:
                actual_dict[state] = actual_dict[state] - sighting_dict[state]

    def __calculate_tat(self, bug: object) -> dict:
        """
        calculate the overall turn-around time (TAT) and inner state distribution,
        meaning, how much time bug was in every state
        returns dict in the following format where all the values are in hours
        >>> {
                "state1": val1,
                "state2": val2
        >>> }
        """
        # desired status list will store the commutative time (hours) which bug spent in every state
        # 'sighting' is not a real state, will need to have some kombina to calculate it
        actual_status_duration_hours_dict = {}
        sighting_status_duration_hours_dict = {}
        status_duration_hours_dict = actual_status_duration_hours_dict

        # get the created date - this is simple
        jira_created_date = self.__convert_time_str_to_object(bug.fields.created)
        # get close date - this is a bit more tricky, since it is possible that bug was never implemented
        # (e.g. duplicate bugs) need to monitor all transitions and looking for specific states
        jira_closed_date = None

        # the reference time is the previous transition - init with bug creation date
        previous_transition_date = jira_created_date

        # log.info(
        #     "[%s] [%s] [%s] '*' --> Open",
        #     bug.key,
        #     str(bug.fields.reporter.displayName).ljust(30),
        #     jira_created_date,
        # )

        for history in super().get_jira().issue(bug.key, expand="changelog").changelog.histories:
            for item in history.items:
                # get the from/to states
                from_state = item.fromString
                to_state = item.toString
                # log the transition date
                current_transition_date = self.__convert_time_str_to_object(history.created)

                # 'sighting' is not a status, but a 'state'
                # you can be in sighting in any status
                if item.field == "State Reason":
                    if to_state == SIGHTING:
                        # entering sighting mode
                        status_duration_hours_dict = sighting_status_duration_hours_dict
                        log.debug(
                            "[%s] [%s] [%s] %s --> %s",
                            bug.key,
                            str(history.author.displayName).ljust(30),
                            current_transition_date,
                            from_state,
                            SIGHTING,
                        )

                    if from_state == SIGHTING:
                        # exit sighting mode
                        if not sighting_status_duration_hours_dict:
                            # the 'sighting' dict is empty. we never entered this state --> bug was filed as 'sighting'
                            # update dictionary with "Open" status (which is the default)
                            delta_hour = self.__calc_time_diff_hours(current_transition_date, previous_transition_date)
                            sighting_status_duration_hours_dict["Open"] = delta_hour

                        status_duration_hours_dict = actual_status_duration_hours_dict
                        log.debug(
                            "[%s] [%s] [%s] %s --> %s [%s]",
                            bug.key,
                            str(history.author.displayName).ljust(30),
                            current_transition_date,
                            SIGHTING,
                            to_state,
                            delta_hour,
                        )

                    # go to next transition, this should not change anything, besides turning on/off sighting flag
                    continue

                if item.field == "status":
                    if from_state not in status_duration_hours_dict:
                        status_duration_hours_dict[from_state] = 0

                    orig = int(status_duration_hours_dict[from_state])
                    delta_hour = self.__calc_time_diff_hours(current_transition_date, previous_transition_date)
                    status_duration_hours_dict[from_state] = orig + delta_hour
                    log.debug(
                        "[%s] [%s] [%s] %s --> %s [%s]",
                        bug.key,
                        str(history.author.displayName).ljust(30),
                        current_transition_date,
                        from_state,
                        to_state,
                        delta_hour,
                    )

                    # update closed date - any of the following sates is defined to be "closed"
                    # (take the first one, since transitions are in order)
                    if not jira_closed_date:
                        if to_state in ["Implemented", "Verify", "Closed"]:
                            jira_closed_date = current_transition_date

                    # store the previous transition date for later use
                    previous_transition_date = current_transition_date
                    continue

        # subtract the sighting time from the actual one
        self.__subtract_sighting_from_actual_tat(actual_status_duration_hours_dict, sighting_status_duration_hours_dict)

        # prepare final dict - add overall TAT and sighting
        actual_status_duration_hours_dict[JIRA_TAT] = self.__calc_time_diff_hours(jira_closed_date, jira_created_date)
        actual_status_duration_hours_dict[SIGHTING] = sum(sighting_status_duration_hours_dict.values())
        # Commented out for testing) log.info(actual_status_duration_hours_dict)

        return actual_status_duration_hours_dict

    def get_specific_bug(self, bug_number: str) -> str:
        """
        gets bug number eg. WIFI-1234 or WOT-567 and returns the corresponding bug ID in JIRA
        do you think it should be the same?
        """
        _actual_bug = DB_NA

        try:
            issue = super().get_jira().issue(bug_number)
            _actual_bug = issue.key
        except jira_api.JIRAError:
            # bug was not found
            pass

        return _actual_bug

    @staticmethod
    def __get_found_by(bug: object) -> str:
        """
        returns the "found by" field
        """
        try:
            return bug.fields.customfield_10224.value
        except (TypeError, AttributeError):
            return DB_NA

    @staticmethod
    def __get_team(bug: object) -> str:
        """
        returns the "team" field
        """
        try:
            return bug.fields.customfield_10299.value
        except (TypeError, AttributeError):
            return DB_NA

    def get_all_bugs(self) -> list:
        """
        returns all JIRA bugs matching specific query
        """
        # lits for all jira bugs
        jira_bug_list = []

        jira_query = (
            f"{jira_api.Field.PROJECT.value} in (WIFI, BT, CIE, DBGT, WOT) "
            f"and {jira_api.Field.ISSUE_TYPE.value} = Bug "
            f"and 'TEAM' in (CAE, 'CAE - Enterprise', 'CIE Engineering', 'CAE – Certifications', 'CAE - Linux') "
            f"and 'Created' >= {self.__created_year}-01-01"
        )

        log.info("JIRA query: %s", jira_query)
        issues = super().get_jira().search_issues(jira_query, maxResults=False)

        log.info("JIRA: [%d] bugs were found", len(issues))

        for bug in issues:
            jira_bug_data = JiraBugData()

            jira_bug_data.jira_id = bug.key
            jira_bug_data.jira_title = self.__get_title(bug)
            jira_bug_data.jira_exposure = self.__get_exposure(bug)
            jira_bug_data.jira_created_date = self.__get_created_date(bug)
            jira_bug_data.jira_closed_date = self.__get_closed_date(bug)
            jira_bug_data.jira_implemented_date = self.__get_implemented_date(bug)
            jira_bug_data.jira_verify_date = self.__get_verify_date(bug)
            jira_bug_data.jira_affected_version = self.__get_affected_version(bug)
            jira_bug_data.jira_initial_component = self.__get_initial_component(bug)
            jira_bug_data.jira_final_component = self.__get_final_component(bug)
            jira_bug_data.jira_is_sw_change = self.__is_bug_fixed_as_sw_change(bug)
            jira_bug_data.jira_state_reason = self.__get_state_reason(bug)
            jira_bug_data.jira_status = self.__get_status(bug)
            jira_bug_data.jira_platform = self.__get_platform(bug)
            jira_bug_data.jira_nic = self.__get_hardware(bug)
            jira_bug_data.jira_os = self.__get_os(bug)
            jira_bug_data.jira_reporter_name = self.__get_reporter_name(bug)
            jira_bug_data.jira_reporter_email = self.__get_reporter_email(bug)
            jira_bug_data.jira_customer_name = self.__get_customer_name(bug)
            jira_bug_data.jira_url = self.__get_url(bug)
            jira_bug_data.jira_found_by = self.__get_found_by(bug)
            jira_bug_data.jira_team = self.__get_team(bug)

            # extended analysis
            jira_bug_data.jira_analysis = self.__get_jira_advanced_analysis(bug)

            # TAT (turn around time)
            tat_values_hour = self.__calculate_tat(bug)
            jira_bug_data.jira_tat_hours = tat_values_hour[JIRA_TAT]
            jira_bug_data.jira_sighting_hours = tat_values_hour[SIGHTING]
            jira_bug_data.jira_open_hours = tat_values_hour[OPEN] if OPEN in tat_values_hour else 0
            jira_bug_data.jira_in_progress_hours = tat_values_hour[IN_PROGRESS] if IN_PROGRESS in tat_values_hour else 0
            jira_bug_data.jira_pending_hours = tat_values_hour[PENDING] if PENDING in tat_values_hour else 0

            jira_bug_list.append(jira_bug_data)

        return jira_bug_list


def get_bug_project(_merged_bug: MergedBugData) -> str:
    """
    returns the bug project as listed in JIRA
    e.g. WIFI, BT, TOOL, ICPS
    """
    # in case we have a JIRA, so take the JIRA ID (before the '-')
    # if no JIRA, translate the 'ips_category' to match the JIRA convention
    # TODO: move this map to external json configuration file
    ips_category_jira_project_map = {
        "WiFi Windows": "WIFI",
        "Bluetooth (BT)": "BT",
        "Debug Tools": "DBGT",
        "OEM Tools": "WOT",
        "WCS Innovation Engineering": "CIE",
    }

    bug_project = DB_NA

    if _merged_bug.jira_data.jira_id != DB_NA:
        match = re.findall(r"(\w+)-", _merged_bug.jira_data.jira_id)
        if match:
            bug_project = match[0]
        else:
            # how did we get here?
            assert False, "failed to get project name based on JIRA ID"
    else:
        # no JIRA is found, get from 'ips_category'
        # if not found, set as 'NA'
        bug_project = ips_category_jira_project_map.get(_merged_bug.ips_data.ips_category, DB_NA)

    if bug_project == DB_NA:
        log.info("JIRA: %s, IPS category: %s", _merged_bug.jira_data.jira_id, _merged_bug.ips_data.ips_category)

    return bug_project


def get_ips_tat_till_jira(_merged_bug: MergedBugData) -> int:
    """
    returns the IPS TAT till JIRA was filed (hours)
    assumption: JIRA exists
    """
    # sanity check
    assert _merged_bug.jira_data.jira_id != DB_NA, "JIRA doesn't exist"

    time_delta = _merged_bug.jira_data.jira_created_date - _merged_bug.ips_data.ips_created_date
    return round(time_delta.total_seconds() / 3600)


class DbConnector:
    """
    Postgres DB connector, to be merged later with DatabaseAPI module
    """

    def __init__(self):
        log.info("CTOR - DbConnector")
        self.__connection = None
        self.__cursor = None

        # open connection to DB
        self.__open_db_connection()

    def __del__(self):
        log.info("DTOR - DbConnector")

        self.__cursor.close()
        self.__connection.close()

    def __get_table_data(self, table: str) -> list:
        """
        returns all table data in a list of dict, where each element has
        key (field name): val (field data)
        assumption: table exists in DB
        """
        # run the query
        self.__cursor.execute(f"SELECT * FROM {table}")

        # get the results
        rows = self.__cursor.fetchall()

        # get column names
        col_names = [x[0] for x in self.__cursor.description]

        # arrange in a form of list of dictionaries (column: val)
        return list(dict(zip(col_names, list(result))) for result in rows)

    def get_customers_data(self) -> list:
        """
        returns all data from customers table
        """
        return self.__get_table_data("customers")

    def __open_db_connection(self):
        """
        open the connection to the postgres customer engineering DB
        stores:
        >>> conn: a new instance of the connection class
        >>> cur : cursor to execute any SQL statements
        """
        connection = psycopg2.connect(
            database=Sherlock.PostgresCustomerEngineeringDb.database,
            user=Sherlock.PostgresCustomerEngineeringDb.user,
            password=Sherlock.PostgresCustomerEngineeringDb.password,
            host=Sherlock.PostgresCustomerEngineeringDb.host,
            port=Sherlock.PostgresCustomerEngineeringDb.port,
        )

        cursor = connection.cursor()
        self.__connection = connection
        self.__cursor = cursor

        self.__connection.set_session(autocommit=True)

    def __delete_table(self, table_name: str) -> None:
        """
        delete a table from DB
        caution: all data will be erased, requires SO (schema owner) permissions
        """
        log.info("delete table: %s", table_name)
        self.__cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

        # commit changes
        self.__connection.commit()

    def __create_table(self, table_name: str, table_structure: list) -> None:
        """
        create a new table in the DB
        assumption: 'table_name' doesn't exist
        caution: requires SO (schema owner) permissions
        INSERT INTO
            table_name (col1, col2)
        VALUES
            (val11, val12),
            (val21, val22)
        """
        # generate all columns
        columns_str = ""
        for entry in table_structure:
            field_name = entry["field_name"]
            field_type = entry["field_type"]

            columns_str += f"{field_name} {field_type},"

        # remove last comma
        columns_str = columns_str[:-1]

        # generate final query
        query_string = f"CREATE TABLE {table_name} ({columns_str})"

        # create a table
        log.info("create a new table using the following query:")
        log.info(query_string)
        self.__cursor.execute(query_string)

        # commit changes
        self.__connection.commit()

    def __get_table_fields_for_db(self, data_obj: object) -> list:
        """
        generates list with field names, values and filed types of a table
        based on the data_obj which is a dataclass (can be nested)
        e.g. temp_data_list = [
            {"field_name": col1, "field_type": INT, "field_val": 1},
            {"field_name": col2, "field_type": TEXT, "field_val": "some text"},
        ]
        """
        temp_data_list = []

        for _field in fields(data_obj):
            # check if this field is a data class
            # if yes, call same func recursively
            if is_dataclass(_field.type):
                temp_data_list += self.__get_table_fields_for_db(getattr(data_obj, _field.name))
            else:
                # primitive variable
                val = getattr(data_obj, _field.name)
                # fix cases where there is no value at all
                if val is None:
                    val = DB_NA
                key_type = ""
                if isinstance(val, str):
                    key_type = "TEXT"
                    # do not allow empty strings
                    if not val:
                        val = DB_NA
                    # single quotes are not allowed
                    val = val.replace("'", "")
                    # remove non-ENG chars (PBI doesn't like it)
                    val = val.encode(encoding="ascii", errors="ignore").decode()
                elif isinstance(val, bool):
                    key_type = "BOOLEAN"
                elif isinstance(val, int):
                    key_type = "INT"
                elif isinstance(val, datetime):
                    key_type = "TIMESTAMP"
                else:
                    assert False, "unsupported format for DB"

                temp_data_list.append(
                    {
                        "field_name": _field.name,
                        "field_type": key_type,
                        "field_val": val,
                    }
                )

        return temp_data_list

    def insert_to_table(self, table_name: str, value_dict: dict, is_delete_existing_table: bool) -> None:
        """
        insert the whole list of dictionaries into the table
        assumption: all dictionaries have same structure
        """
        sql_strings = []
        tbl_parsed = []
        for _entry in value_dict:
            single_raw_data = []
            tbl_parsed = self.__get_table_fields_for_db(_entry)
            for val in tbl_parsed:
                field_val = val["field_val"]
                single_raw_data.append(f"'{field_val}'")

            sql_strings.append(f"({', '.join(single_raw_data)})")

        # get field names (table is symmetrical, take last row)
        field_names = [entry["field_name"] for entry in tbl_parsed]
        # create str for sql
        columns_sql = f"({', '.join(field_names)})"

        # delete existing table if needed
        if is_delete_existing_table:
            self.__delete_table(table_name)
            self.__create_table(table_name, tbl_parsed)

        # insert all data row-by-row
        # technically, it is possible to insert up to 1000 rows in a single query
        # however,it complicates the logic and we don't care much about performance
        # (which shouldn't be that different)
        log.info("update table with relevant data (this might take a while) ..")
        for row in sql_strings:
            query_string = f"INSERT INTO {table_name} {columns_sql} VALUES {row}"
            self.__cursor.execute(query_string)

        # commit changes
        self.__connection.commit()

        log.info("update is complete. %s rows were inserted", len(sql_strings))


def get_customer_name(_customer_data: list, _merged_bug: MergedBugData) -> str:
    """
    deduce the customer possible names from the bug_data, based on customer_data DB
    """
    # dict where key (str) is the customer name and value (int) is the number of times it appeared
    # this is needed in case we found multiple customers for same bug, we take the one which appears the most
    customers_dict = {}

    # get bug numbers for prints only
    ips_case_number = _merged_bug.ips_data.ips_case_number
    jira_id = _merged_bug.jira_data.jira_id

    # get all the fields which might contain customer name
    str_list = [
        _merged_bug.ips_data.ips_title,
        _merged_bug.jira_data.jira_title,
        _merged_bug.jira_data.jira_customer_name,
        _merged_bug.ips_data.ips_oem,
        _merged_bug.ips_data.ips_odm,
        _merged_bug.ips_data.ips_reporter_account_name,
    ]

    # concat together
    merged_str = " ".join(str_list).lower()

    # replace all special chars (everything but letters) by spaces
    # e.g. [dell][quanta] some bug --> dell quanta some bug
    clean_str = re.sub("[^a-z]+", " ", merged_str)

    # divide into words list
    words_list = clean_str.split(" ")

    for customer in _customer_data:
        # search by OEM only
        if customer["customer_role"] == "oem":
            customer_name = customer["customer_name"]
            # keywords are additional potential names
            customer_keywords = customer["customer_keywords"]
            customer_keywords_list = [] if not customer_keywords else customer_keywords.split(",")

            for name in [customer_name] + customer_keywords_list:
                for word in words_list:
                    # look for exact match
                    if name == word:
                        if customer_name not in customers_dict:
                            customers_dict[customer_name] = 0
                        customers_dict[customer_name] += 1

    if not customers_dict:
        log.info("no customer was found for IPS [%s], JIRA [%s]", ips_case_number, jira_id)
        return DB_NA

    # get the max value in case we have multiple customers
    final_customer = max(customers_dict, key=customers_dict.get).upper()

    if len(customers_dict) > 1:
        # this is probably not a bug, but it is always better that we have only one
        log.debug(
            "multiple customers were found for IPS [%s], JIRA [%s], choosing %s",
            ips_case_number,
            jira_id,
            final_customer,
        )
        log.debug(customers_dict)

    # in case of multiple customers, take the one appears the most
    return final_customer


def get_employee_name_from_email(email: str) -> str:
    """
    returns the employee name from the email address
    e.g. Roi Fridburg, from roi.fridburg@intel.com
    """
    # need to get rid of the middle name
    if "@" in email:
        names = email.split("@")[0].split(".")
        # take the first and last indices
        return f"{names[0].capitalize()} {names[-1].capitalize()}"

    # we dont have an email, employee is unknown
    return DB_NA


def fill_mutual_bug_params(_merged_bug_list: list) -> None:
    """
    update merged_bug_list with parameters mutual to both IPS and JIRA bugs
    """

    # get customer data from DB
    _db_manager = DbConnector()
    customer_data = _db_manager.get_customers_data()
    del _db_manager

    # generic flags for all bugs
    for merged_bug in _merged_bug_list:
        # update project (deduced from JIRA or from IPS in case of no JIRA)
        merged_bug.bug_project = get_bug_project(merged_bug)

        # we have JIRA and IPS together
        merged_bug.is_ips_promoted_to_jira = bool(
            merged_bug.jira_data.jira_id != DB_NA and merged_bug.ips_data.ips_case_number
        )

        # get the customer name based on several fields in JIRA and IPS
        merged_bug.customer = get_customer_name(customer_data, merged_bug)

        # get the reporter - prefer JIRA, if exists
        # technically, if both exist, they should be the same
        if merged_bug.jira_data.jira_reporter_email != DB_NA:
            reporter_email = merged_bug.jira_data.jira_reporter_email
        else:
            reporter_email = merged_bug.ips_data.ips_owner_email

        merged_bug.reporter = get_employee_name_from_email(reporter_email)

        # customer closed date
        # take the earlier of the following states, since technically bug is already closed
        # if any of the below happen
        merged_bug.customer_closed_date = min(
            merged_bug.jira_data.jira_closed_date,
            merged_bug.jira_data.jira_implemented_date,
            merged_bug.jira_data.jira_verify_date,
        )


def generate_merged_bug_list(_ips_data: list, _jira_data: list) -> list:
    """
    generate a merged list of JIRA and IPS bugs
    """
    _merged_bug_list = []

    # join tables - two steps
    # [1] go over all IPS bugs, fix the JIRA links and add them
    #     most of the JIRAs are filed from IPS, meaning, IPS is a larger group
    # [2] go over all JIRA bugs and find those who were not originated by IPS
    #     (there shouldn't be that many)
    # fix ips_jira_id links for all IPS issues having JIRA link
    for ips_bug in _ips_data:
        merged_bug = MergedBugData()
        jira_obj = None
        # get the JIRA ID and see if needs to be fixed
        ips_jira_id = ips_bug.ips_jira_id
        if ips_jira_id != DB_NA:
            jira_obj = [_bug for _bug in jira_data if _bug.jira_id == ips_jira_id]
            if not jira_obj:
                # the JIRA bug listed in IPS was not found in internal dictionary
                # possible reasons:
                # [1] the query for JIRA bugs is incorrect/missing
                #     this case is less common (<1%), but need to review the query and see how it was missed
                # [2] bug was moved to a different project (e.g. was filed as WOT and moved to WIFI)
                #     this case is common (~5%) - need to find the "new" bug and update the DB
                #     the original "old" bug doesn't exist, so we have nothing to do with it
                # [3] bug was deleted from JIRA DB - not sure what we can do here (complain?)
                # search for the bug in JIRA
                actual_bug = jira.get_specific_bug(ips_jira_id)
                # check which one of the above happened
                if actual_bug == DB_NA:
                    # bug was not found in JIRA at all, case [3]
                    log.info("JIRA [%s] from IPS [%d] doesn't exist in JIRA DB", ips_jira_id, ips_bug.ips_case_number)
                    # reset the link
                    ips_bug.ips_jira_id = DB_NA
                elif actual_bug == ips_jira_id:
                    # the returned bug is identical to original one, case [1]
                    # we 'miss' this bug and will not be able to calc JIRA TAT, since jira_data doesn't include it
                    log.info(
                        "JIRA [%s] from IPS [%d] was not found by original query - check why",
                        actual_bug,
                        ips_bug.ips_case_number,
                    )
                else:
                    # actual_bug != ips_jira_id
                    # the returned bug is different from the original one, case [2]
                    log.info(
                        "IPS [%d] bug changed project, [%s] --> [%s]", ips_bug.ips_case_number, ips_jira_id, actual_bug
                    )
                    # check if the new bug exists in the DB
                    jira_obj = [_bug for _bug in jira_data if _bug.jira_id == actual_bug]
                    if jira_obj:
                        # exists, good - update new link
                        ips_bug.ips_jira_id = actual_bug
                    else:
                        log.info("JIRA [%s] was not found by original query - check why", actual_bug)
                        # doesn't exist, case [1]
                        # reset the link
                        ips_bug.ips_jira_id = DB_NA

        # fill the IPS data
        merged_bug.ips_data = ips_bug
        # fill the JIRA data if exist
        # if empty, meaning, no corresponding JIRA bug found for this IPS
        if jira_obj:
            merged_bug.jira_data = jira_obj[0]
            merged_bug.ips_tat_till_jira_hours = get_ips_tat_till_jira(merged_bug)

        # add to final list
        _merged_bug_list.append(merged_bug)

    # go over all JIRA issues and add to list all bugs which exist in JIRA
    # and were not promoted from IPS (were not added previously)
    for jira_bug in _jira_data:
        if not any(_bug for _bug in _merged_bug_list if _bug.jira_data.jira_id == jira_bug.jira_id):
            # bug doesn't exist in merged_bug_list
            log.info("JIRA [%s] was found in query, but was not originated from IPS", jira_bug.jira_id)
            merged_bug = MergedBugData()
            merged_bug.jira_data = jira_bug
            # add to final list
            _merged_bug_list.append(merged_bug)

    # sanity check, we should not see any JIRA twice
    #bug_list = [_bug.jira_data.jira_id for _bug in _merged_bug_list if _bug.jira_data.jira_id != DB_NA]
    #assert len(bug_list) == len(set(bug_list)), "some JIRA bugs are added twice"

    # update generic/mutual params for both JIRA and IPS
    fill_mutual_bug_params(_merged_bug_list)

    return _merged_bug_list


if __name__ == "__main__":
    # logger configurations
    log.setLevel(logging.INFO)
    log.info("JIRA bug statistics job starts")

    
    # parse input params
    parser = argparse.ArgumentParser(description="JIRA bugs stats")
    parser.add_argument("-cy", "--created-year", required=True, type=str, help="filter in bugs created since this year")
    params = parser.parse_args()

    start_time = datetime.now()
    
    # IPS data
    ips = IpsBug(params.created_year)
    ips_data = ips.get_all_bugs()
    
    # JIRA data
    jira = JiraBug(params.created_year)
    jira_data = jira.get_all_bugs()

    # merged data
    merged_bug_list = generate_merged_bug_list(ips_data, jira_data)

    
    # update DB
    db_manager = DbConnector()
    db_manager.insert_to_table("ips_jira_bugs", merged_bug_list, True)
    del db_manager
    end_time = datetime.now()

    log.info("done, %s", end_time - start_time)
