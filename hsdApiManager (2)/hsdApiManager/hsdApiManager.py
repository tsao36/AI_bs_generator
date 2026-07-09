import base64
import json
import os
import sys
from datetime import datetime, timezone
import requests
from tzlocal import get_localzone
import utils
import externalIssue
from pathlib import Path

QUERY_URL = "https://hsdes-api.intel.com/rest/auth/query/execution/"
GET_ISSUE_QUERY = "https://hsdes-api.intel.com/rest/auth/article/"
ATTACHMENT_HSD_URL = "https://hsdes.intel.com/resource/"

# ARTICLE = "1203659509"

FULL_HISTORY_URL = "history?fields=id%2Ctitle%2Cdescription%2Coperating_system%2Cfamily%2Cprocessor%2Ccomments%2" \
                   "Curl_link%2Cww_submitted%2Cupdated_date%2Cowner%2Cstatus%2Cpriority"
PARTIAL_HISTORY = "history?fields=id%2Ctitle%2Ccomments%2Cww_submitted%2Cupdated_date"
DOWNLOAD_ATTACHMENT = "https://hsdes-api.intel.com/rest/auth/binary/"


class HsdSession:
    """
    struct to hold all data related to send a request to HSD:
    headers: hsd headers that hold the token.
    verify: the path to the ssl certificate
    """

    def __init__(self):
        self.headers = None
        self.verify = None


def get_token(config):
    """
    because we need to extract the token more than once, we will use a builtin function.
    :param config: the config file to take the path to the token json file.
    :return: the token if file exists
    """
    if config is None:
        return None
    # loading credentials
    cred_path = os.path. \
        join(utils.get_work_directory(),
             config['hsd']['token_path'])

    if not os.path.isfile(cred_path):
        utils.print_log(f"Cant find HSD token file '{cred_path}', aborting.")
        return None

    try:
        with open(cred_path, 'r') as f:
            cred_data = json.load(f)

            token = cred_data['token']

            return token
    except OSError:
        utils.print_log(f"could not open/read file - {cred_path}")
        return None


def start_auth_flow(config, bundled_cert_path):
    """
    Start the authentication flow in HSD server,
    :param config: the config instance
    :param bundled_cert_path: the ssl certificate, after being bundled.
    :return: an instance of the HSD authentication, if something went wrong, return None
    """
    if config is None:
        return None

    try:
        token = get_token(config)

        if not token:
            utils.print_log("could not get HSD token, aborting.")
            return None

        # building a request with token
        full_token = config["hsd"]["user"] + ':' + token
        encoded_token = base64.b64encode(full_token.encode()).decode()
        url = config["hsd"]["server"]
        headers = {'Content-Type': 'application/json', 'Authorization': 'Basic %s' % encoded_token}

        ca_path = bundled_cert_path
        # send request
        response = requests.get(url, headers=headers, verify=ca_path)

        if response.status_code == 200:
            utils.log_only("HSD authorization established ")
            hsd_session = HsdSession()
            hsd_session.headers = headers
            hsd_session.verify = ca_path
            return hsd_session
        else:
            utils.print_log(f"Error while connecting to HSD: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        utils.print_log(f"ERROR while connecting to HSD, issue probably is with the"
                        f" request itself.\nerror message: {e}", log_verb="error")
        utils.log_only("{} | {}".format(sys.exc_info()[0],
                                        sys.exc_info()[1]),
                       verb="error")
        return None


def build_query(config, query_id=None, specific_id=False):
    """
    building a hsd query by id, title and other fields.
    :param config: config file that includes all fields needed for the query.
    :param query_id: the kind of query we want to use, the value is the id of the query.
    :param specific_id: bool value to indicate if we fetch specific issue.
    for example, if we want a query for all closed issues then it will have id of 1234.
    return: a query (string)
    """
    if config is None:
        utils.print_log("did not find config file.")
        return None

    if specific_id:
        query = GET_ISSUE_QUERY + f"{specific_id}"
        return query

    query_base = QUERY_URL
    if query_id:
        query_base += query_id
    # include text fields needs to be first
    query_base += "?include_text_fields=Y"
    try:
        if config["hsd"]["max_results"] != "None":
            query_base += "&max_results="
            query_base += config["hsd"]["max_results"]

        utils.print_log(f"HSD query base is - {query_base}")

        return query_base

    except KeyError as e:
        utils.print_log(f"Key in dictionary not found, error message - {e}")
        return None


def fetch_hsd_issues(config, hsd_headers, hsd_ids):
    """
    will fetch HSD issues by query ID
    :param config: config file
    :param hsd_headers: the hsd session
    :param hsd_ids: from args, tells if we should fetch a specific issue or all
    :return: hsd issues list and the fetch time
    """
    # create a hsd dict! object will look like: [issue_id : updated_date]
    hsd_dict = {}
    # Fetch a specific hsd id issue to sync
    if hsd_ids.isdigit():
        utils.print_log("fetching specific HSD issue (id={})"
                        .format(hsd_ids))
        method = "POST"
        url = build_query(config, None, hsd_ids)
        payload = {"querytype": "eql",
                   "query": f"select id, updated_date where discovery.issue.id = {hsd_ids}", "filter": ""}
    else:
        method = "GET"
        url = build_query(config, config['hsd']['query_fetch_headers'], False)
        payload = {}

    if url is None:
        # means we did not build a query, abort
        return None

    try:

        response = requests.request(method=method, url=url, json=payload, headers=hsd_headers.headers,
                                    verify=hsd_headers.verify)

        if response.status_code == 200:
            data_rows = response.json()['data']

            for row in data_rows:
                # we extracted the headers, now we will arrange
                # the data as an issue object. after creation, we will insert it to the dictionary
                # of external issue dict
                issue_id = row['id']
                updated_date = row['updated_date']

                # now attach the issue object to the dictionary, the key will be the hsd_id
                hsd_dict[issue_id] = updated_date

            # now extract current time for time stamp
            hsd_fetch_time = datetime.now(get_localzone())

            utils.print_log(f"HSD dictionary is:\n{hsd_dict}")

            return hsd_dict, hsd_fetch_time

        else:
            utils.print_log(f"ERROR while connecting to HSD, error message: {response.status_code}, {response.text}",
                            log_verb="error")
            return None, None

    except Exception as e:
        utils.print_log(f"Error with requests - {e}")
        return None, None


def extract_issue_updates(config, hsd_session, hsd_issue_id, last_db_sync_time=None):
    """
    This function will iterate over the given hsd issue fields and extract
    their new updates (comments and attachments) since the given
    last_db_sync_time. if last_db_sync_time is None it means that this is a new issue,
    and we need to import all data.
    it will return the given hsd_issue populated with all its new updates.
    :param config: the config instance
    :param hsd_session: the HSD headers authentication instance
    :param hsd_issue_id: the hsd issue id
    :param last_db_sync_time: the time stamp (datetime type) where we will
    extract all new updates after that
    :return:
    """
    url = QUERY_URL + "eql?start_at=1"

    if last_db_sync_time is None:
        # means its a new issue and we need to extract all history
        payload = f'{{"eql": "select id,title,description,bug.operating_system,family,client_platf.bug.processor,' \
                  f'client_platf.bug.url_link,client_platf.bug.ww_submitted,updated_date,owner,status,priority where' \
                  f' client_platf.bug.id={hsd_issue_id}"}}'

    else:
        # it's a known issue, we need to extract title, priority, updated_date,
        # status, and comments and attachments from last sync
        payload = f'{{"eql": "select id,title,description,updated_date,owner,status,priority where' \
                  f' client_platf.bug.id={hsd_issue_id}"}}'

    try:
        response = requests.post(url, headers=hsd_session.headers, verify=hsd_session.verify, data=payload)
        if response.ok:
            hsd_issue = parse_issue_updates_response(config, hsd_session, response,
                                                     hsd_issue_id, last_db_sync_time)

        else:
            utils.print_log("ERROR while extracting updates for HSD ID " +
                            str(hsd_issue_id), log_verb="error")

            utils.log_only("{}".format(response.content), verb="error")
            return None

        return hsd_issue

    except Exception as e:

        utils.print_log(f"Error while connecting to {hsd_issue_id} HSD issue page\nerror is: {e}", log_verb="error")
        return None


def parse_issue_updates_response(config, hsd_session, response, issue_id, last_db_sync):
    """
    Parse the 'issue_get' query response from hsd and extract its
    relevant data which is later than last_db_sync, this function will update
    the given hsd issue with all new updates (comments and/or attachments)
    :param config: the config instance
    :param hsd_session: the hsd authentication headers
    :param response: the query response to parse (json format)
    :param issue_id: the hsd issue to update (externalIssue type)
    :param last_db_sync: the timestamp to update from (datetime type)
    :return: the given hsd issue (externalIssue type) with the new updates
    """
    # first, taking the response to extract from there all data, organize it in the
    # externalIssue object and after that we will handle the comments separately

    # it's supposed to be one row...
    for row in response.json()['data']:

        issue_obj = externalIssue.ExternalIssue(
            id=row.get('id'),
            title=row.get('title'),
            description=utils.parse_html_to_text(row.get('description')),
            link=row.get('client_platf.bug.url_link'),
            os=row.get('bug.operating_system'),
            family=row.get('family'),
            processor=row.get('client_platf.bug.processor'),
            comments=None,
            attachments=None,
            ww_submitted=row.get('client_platf.bug.ww_submitted'),
            updated_date=row.get('updated_date'),
            owner=row.get('owner'),
            external_status=row.get('status'),
            priority=row.get('priority')
        )

        # handling comments
        issue_comments = parse_issue_comments(issue_id, hsd_session, last_db_sync)
        if issue_comments is None:
            utils.print_log(f"could not fetch comments from HSD for issue {issue_id}, continue without\n")
            issue_comments = []
        issue_obj.comments = issue_comments
        print("done syncing comments\n")

        # moving to attachments,
        # attachments list meta is an object list of attachments metadata!
        attachments_list_meta = get_attachments_metadata(config, hsd_session, issue_obj, last_db_sync)
        if attachments_list_meta is None:
            # meaning we failed to extract any issue!
            utils.print_log("Could not fetch attachments, continue without attachments.\n")
            return issue_obj

        # reaching here, we can assume we have attachment's metadata
        attachment_list = []

        # now iterating on all attachments objects for the issue to download it
        for attachment in attachments_list_meta:
            file = download_attachment(attachment.resource_name, hsd_session, attachment.attachment_id)
            if file is None:
                # indicating that we could not download the attachment, we will provide hsd url later in jira
                attachment.download_failed = True
                continue

            attachment_list.append(file)

        # attaching the attachments list to the object
        issue_obj.attachments = attachments_list_meta
        return issue_obj


def parse_issue_comments(issue_id, hsd_session, last_db_sync=None):
    """
    extract from hsd comments data by the last time we synced.
    if it;s a new issue, last_db_sync will be null and will extract all comments
    :param issue_id: issue id
    :param hsd_session: the hsd authentication session
    :param last_db_sync: date of the last db sync, can be null
    :return: a list of issue comments
    """
    # HSD API DOES NOT ALLOW TO FILTER COMMENTS ON THE SAME API CALL.
    # WE WILL EXTRACT ALL COMMENTS, AND IF THIS IS A KNOWN ISSUE, WE WILL FILTER BY DATE HERE.
    method = "GET"
    url = GET_ISSUE_QUERY + \
          f"{issue_id}/" + "children?child_subject=comment&fields=id,owner,description,updated_date"
    data = None

    try:
        response = requests.request(method=method, url=url, json=data, timeout=10,
                                    headers=hsd_session.headers, verify=hsd_session.verify)

        if response.status_code == 200:
            issue_comments = response.json()['data']

            if last_db_sync:
                # meaning it's a known issue, and we need to filter the issues by date
                updated_issue_comments = []
                for comment in issue_comments:
                    date_from_str = datetime.fromisoformat(utils.pad_milliseconds(comment["updated_date"]))
                    matched_date = date_from_str.replace(tzinfo=timezone.utc)
                    if matched_date > last_db_sync:
                        # meaning we need to include this because it was not synced.
                        updated_issue_comments.append(comment)

                return updated_issue_comments

        else:
            utils.print_log(f"error with the request to get issue comments\nresponse - {response.text}")
            return None

        return issue_comments
    except Exception as e:
        utils.print_log(f"Error while fetching the comments from HSD, issue message is - {e}\n")
        return None


def get_attachments_metadata(config, hsd_session, issue, update_from=None):
    """
    getting the attachment's metadata by the last updated date we synced.
    if this is a new issue, 'updated_from' will be null, and we will upload all attachments.
    :param config: the config file
    :param hsd_session: the hsd headers
    :param issue: hsd issue
    :param update_from: the date we need to sync attachments from
    :return: list of attachments with metadata, none if failed
    """
    try:
        # first extracting all attachment's metadata
        url = GET_ISSUE_QUERY + f"{issue.id}/children"
        token = get_token(config)
        if not token:
            utils.print_log(f"could not fetch HSD private token, skip fetching attachment's metadata for issue -"
                            f" {issue.id}.")
            return None

        payload = {'tenant': token, 'child_subject': 'attachment'}

        response = requests.get(url, verify=hsd_session.verify, headers=hsd_session.headers,
                                params=payload)
        if response.ok:
            attachments_list = response.json()["data"]
        else:
            utils.print_log(f"bad response from fetching attachments metadata, for issue - {issue.id}")
            return None

    except Exception as e:
        utils.print_log(
            f"error while getting attachments metadata for issue - {issue.id}/\nerror message - {e}")
        return None

    # reaching here, we have all metadata including size of the attachments, so we will initialize attachment object
    # list to store all relevant data and check several things: first if we should download the attachment or not,
    # based on the updated date and sync time. also, we will check if attachment is too big for jira,
    # if so, we will update it in the attachment's object and upload it to sharepoint
    attachments_objects = []
    # counter for comment number
    # TODO: THINK OF A BETTER WAY FOR COMMENT COUNT
    count = 1
    for attachment in attachments_list:

        # first checking if we should download this attachment, or it was already downloaded in the previous sync
        date_from_str = datetime.fromisoformat(utils.pad_milliseconds(attachment["updated_date"]))
        matched_date = date_from_str.replace(tzinfo=timezone.utc)
        if matched_date > update_from:
            # skip this is already been synced
            continue

        # we will initialize here the attachments as objects
        attach_object = externalIssue.Attachment(author=attachment["submitted_by"], resource_name=attachment["title"],
                                                 attachment_id=attachment["id"], date=attachment["submitted_date"],
                                                 size=attachment["document.size"], comment_num=count)

        if int(attach_object.size) >= utils.MAX_SHAREPOINT_ATTACHMENT_FILE_SIZE:
            # skipping very large attachment
            # we will provide link to hsd db
            utils.log_only("skipping big file'{}'".format(attach_object.resource_name))
        else:
            if int(attach_object.size) >= config["jira_options"]["max_jira_attachment_size"]:
                attach_object.upload_to_sharepoint = True
                utils.log_only(
                    "file will be uploaded to sharepoint'{}'".format(
                        attach_object.resource_name))

        attachments_objects.append(attach_object)
        count += 1

    return attachments_objects


def download_attachment(file_hsd_name, hsd_session, attachment_id):
    """
    downloads attachment using the metadate
    :param file_hsd_name: the name of the file in HSD.
    :param attachment_id: attachment id
    :param hsd_session: the hsd authentication headers
    :return: the path of the download if successful, None otherwise
    """

    url = DOWNLOAD_ATTACHMENT + f"{attachment_id}"
    try:
        # Create (if needed) a "downloads" sub folder
        downloads_dir = Path(__file__).parent / "files/downloads"
        downloads_dir.mkdir(exist_ok=True)

        file_name_with_path = downloads_dir / f"{file_hsd_name}"

        response = requests.get(url,
                                verify=hsd_session.verify, headers=hsd_session.headers)
        if response.ok:
            with open(file_name_with_path, "wb") as w:
                w.write(response.content)

            return file_name_with_path

    except Exception as e:
        utils.print_log(f"error while downloading attachment for issue - {attachment_id}/\nerror message - {e}")
        return None


def fetch_closed_issues(config, hsd_session):
    """
    sub routine to fetch closed hsd db issues for a comparison with their
    jira counterpart status.
    :param config: the config instance
    :param hsd_session: the HSD authentication instance
    :return: a list of all HSD closed issues
    """
    hsd_dict = {}
    url = build_query(config, config['hsd']['query_closed_issues'], False)

    utils.print_log("fetching closed HSD DB issues")
    utils.print_log("HSD-DB query is '{}'".format(url))

    response = requests.get(url=url, headers=hsd_session.headers,
                            verify=hsd_session.verify)

    if response.status_code == 200:
        data_rows = response.json()['data']

        for row in data_rows:
            # we extracted the headers, now we will arrange
            # the data as an issue object. after creation, we will insert it to the dictionary
            # of external issue dict
            issue_id = row['id']
            updated_date = row['updated_date']
            # now attach the issue object to the dictionary, the key will be the hsd_id

            hsd_dict[issue_id] = updated_date

        # now extract current time for time stamp
        hsd_fetch_time = datetime.now(get_localzone())
        # WE NEED TO CALL EXTRACT ISSUE HEADERS!
        return hsd_dict, hsd_fetch_time
    else:
        utils.print_log("HSD fetch response is not ok... aborting!",
                        log_verb="error")
        utils.log_only("bad response = " + str(response.raw), verb="error")
        return [], None


def is_issue_id_in_dict(issue_id, issues):
    for issue_key in issues.keys():
        if int(issue_key) == int(issue_id):
            return True
    return False
