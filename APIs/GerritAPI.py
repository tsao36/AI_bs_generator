"""
A wrapper for the Gerrit Rest API.
This API is written on a "write-on-need" basis, meaning it is far for complete,
and only features previously needed have been written into it.
If you need a feature that is not present - add it yourself.
"""

import re
import logging
from datetime import datetime
import urllib.parse
from pygerrit2 import GerritRestAPI, HTTPBasicAuthFromNetrc, HTTPBasicAuth
from requests import exceptions

from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning

disable_warnings(InsecureRequestWarning)

log = logging.getLogger("GerritAPI")

SHA1_PATTERN = r"^[0-9a-zA-Z]{6,40}$"
GERRIT_PATTERN = r"^I[0-9a-zA-Z]{40}$"
REFSPEC_PATTERN = r"^refs\/changes\/\d+\/(\d+)\/(\d+)$"


class Gerrit:
    """The "Gerrit" instance.
    If username and password are not provided, will attempt to login using NetRC.
    NetRC uses a "_netrc" file with the credentials needs to be in %userprofile% for this to work.
    For syntax see https://github.com/dpursehouse/pygerrit2
    For gerrit API see https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html
    Args:
        url (str): The URL of the gerrit repository you wish to work with.
        username (str): The username to login with. If not supplied, defaults back to NetRC.
        password (str): The password to login with. If not supplied, defaults back to NetRC.
        verify (bool|str, optional): Whether to verify the SSL certificate of the API. Defaults to True.
                                     Uses system certificate if `pip-system-certs` is installed and set to True.
                                     Can also be a path to a pem file for private certs.
    """

    def __init__(self, url, username=None, password=None, verify=True):
        if username is None or password is None:
            auth = HTTPBasicAuthFromNetrc(url)
        else:
            auth = HTTPBasicAuth(username, password)
        self.base_url = url + "/" if url[-1] != "/" else url  # Add trailing / if missing
        self.rest = GerritRestAPI(url=url, auth=auth, verify=verify)
        self.__parsed_change_ids_dict = {}

    def parse_change_id(self, change_id: str) -> str:
        """
        Parse given change_id to a commit sha.
        change id can be represented as sha1 or refspec.
        If change_id is valid and exists in gerrit then it will be save on parsed_change_id_dict,
        this will allow to get parsed change_id immediately next time.
        Args:
            change_id: The input change_id, either SHA1, Gerrit ChangeID, or Refspec
        Returns:
            Git SHA1 of requested revision
        Raises:
            ValueError: If the change_id is neither a valid SHA1 or Refspec
            ChangeNotFound: If change_id not found on gerrit
        """
        change_id = change_id.strip()
        if change_id in self.__parsed_change_ids_dict:
            return self.__parsed_change_ids_dict[change_id]

        query_string = ""
        if re.match(SHA1_PATTERN, change_id):
            # SHA1 is explicit - return it
            return change_id
        if re.match(GERRIT_PATTERN, change_id):
            # If gerrit pattern, get latest
            query_string = f"/changes/{change_id}/?o=CURRENT_REVISION&o=CURRENT_COMMIT"
            revision_to_get = "current_revision"
        elif refspec_match := re.match(REFSPEC_PATTERN, change_id):
            # If refspec, get sha from refspec
            change_number, revision_number = refspec_match.groups()
            query_string = f"/changes/{change_number}/revisions/{revision_number}/commit"
            revision_to_get = "commit"
        else:
            raise ValueError(f"Bad change id format: '{change_id}'")

        try:
            change = self.rest.get(query_string)
            commit_sha = change[revision_to_get]
            self.__parsed_change_ids_dict[change_id] = commit_sha
            self.__parsed_change_ids_dict[commit_sha] = commit_sha
            return commit_sha
        except exceptions.HTTPError as ex:
            if ex.response.status_code == 404:
                raise ChangeNotFound(change_id) from ex
            raise ex

    def get_all_commits(
        self,
        repo,
        status=None,
        branch=None,
        since: datetime = None,
        until: datetime = None,
        query_options: list = None,
    ):
        """Gets all the commits with the given properties.
        Since the maximum number the API allows is limited, we make several requests as long as there are results.
        Not recommended to cast too wide a net.

        Args:
            repo (str): The name of the repository to get commits from.
            status (str, optional): Filter by status. Options are NEW, MERGED, ABANDONED. Defaults to None.
            branch (str, optional): Filter by branch. Defaults to None.
            since (datetime, optional): Only get commits since this date. Defaults to None.
            until (datetime, optional): Only get commits until this date. Defaults to None.
            query_options (list, optional): A list of additional options to get for each commit.
                                The list of all possible values is available at:
                                https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#query-options

        Returns:
            list: A list of commit objects.
        """
        # Verify values
        if status and not status in ["NEW", "MERGED", "ABANDONED"]:
            raise ValueError(f'status must be either "NEW", "MERGED", or "ABANDONED"! Not "{status}"!')

        if (until and since) and (until < since):
            raise ValueError("Your 'since' date is after your 'until' date, which doesn't make sense!")

        if not any([until, since, status, branch]):
            log.warning("No filters were selected! This can cause big result sets and long response times!")

        query_string = (
            f"/changes/?q=project:{repo}"
            + (f"+status:{status}" if status else "")
            + (f"+branch:{branch}" if branch else "")
            + (f"+after:{since.strftime('%Y-%m-%d')}" if since else "")
            + (f"+before:{until.strftime('%Y-%m-%d')}" if until else "")
            + ("".join([f"&o={x}" for x in query_options]) if query_options else "")
        )
        skip = 0  # When querying the results from the server, start at the Nth result
        query_size = 500  # Query only this number of results from the server
        latest_result = self.rest.get(f"{query_string}&n={query_size}")
        final_results = []
        while latest_result:  # While we're still getting results
            final_results += latest_result  # Add the current result set to the final results
            skip += query_size  # We got this number of results, so we're gonna start the next query after this number
            latest_result = self.rest.get(f"{query_string}&n={query_size}&S={skip}")  # Check for more results
        return final_results

    def get_change_author(self, change_id: str) -> str:
        """
        Get author of a change
        Args:
            change_id: Gerrit change id, commit sha, or refspec number
        Returns:
            str: Author name of the latest revisions
        """
        gerrit_change = self.get_change(change_id, "CURRENT_COMMIT", "CURRENT_REVISION")
        return gerrit_change["revisions"][gerrit_change["current_revision"]]["commit"]["author"]["name"]

    def get_self_approvers(
        self,
        repo,
        branch=None,
        since: datetime = None,
        until: datetime = None,
    ):
        """Get a list of all the gerrits where the owners approved their own commits.
        Checks for both Verified = 1 and Code-Review = 2.

        Args:
            repo (str): The repository to look in.
            branch (str, optional): Filter to this branch.
            since (datetime, optional): Only check commits after this date. Defaults to None.
            until (datetime, optional): Only check commits until this date. Defaults to None.
        """
        commits = self.get_all_commits(
            repo,
            status="MERGED",
            branch=branch,
            since=since,
            until=until,
            query_options=["DETAILED_LABELS", "DETAILED_ACCOUNTS", "CURRENT_REVISION", "CURRENT_COMMIT", "WEB_LINKS"],
        )

        self_approved_commits = []
        for commit in commits:
            if commit["owner"]["name"] == "sys_windrvbuild":
                continue
            owner_id = commit["owner"]["_account_id"]
            cr_approver = [
                vote["_account_id"]
                for vote in commit["labels"]["Code-Review"].get("all", [])
                if vote.get("value", 0) == 2
            ]
            verified_approver = [
                vote["_account_id"] for vote in commit["labels"]["Verified"].get("all", []) if vote.get("value", 0) == 1
            ]
            if owner_id in [*cr_approver, *verified_approver]:
                self_approved_commits.append(commit)
        return self_approved_commits

    def get_sha1_from_refspec(self, refspec):
        """Gets the matching SHA1 for the given refspec by querying Gerrit.

        Args:
            refspec (str): The Refspec to search for

        Raises:
            BadChangeID: If the refspec is not valid (the string is not matching the refspec form)
            RevisionNotFound: The refspec was found but the revision wasn't.
            ChangeNotFound: If the refspec couldn't be found.

        Returns:
            str: The SHA1 associated with the refspec.
        """
        try:
            match = re.match(r"^refs\/changes\/\d+\/(\d+)\/(\d+)", refspec)
            if not match:
                raise BadChangeID(refspec)
            change_number = match.group(1)
            revision_number = match.group(2)
            query_string = f"/changes/{change_number}/?o=ALL_REVISIONS"
            result = self.rest.get(query_string)
            for key, revision in result["revisions"].items():
                if str(revision["_number"]) == revision_number:
                    return key
            raise RevisionNotFound(change_number, revision_number)
        except exceptions.HTTPError as ex:
            if ex.response.status_code == 404:
                raise ChangeNotFound(refspec) from ex
            raise ex

    def get_change(self, change_id: str, *params) -> dict:
        """
        Returns the ChangeInfo object matching the changeID supplied.
        The extra parameters are strings for requesting extra information.
        Args:
            change_id : SHA1 or refspec of the change to retrieve from the Gerrit.
            * params: Additional fields to be queried in addition to the basic ChangeInfo fields.
                      See https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#query-options
                      for all options.
        Returns:
            dict: A ChangeInfo struct.
                  See https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#change-info for the
                  ChangeInfo object structure.
                  Additionally adds the `url` key with the URL to the change.
        Raises:
            Exception: If any error occurs.
        """
        try:
            change_id = self.parse_change_id(change_id)
            query_string = f"/changes/{change_id}"
            if params:
                sep = "&o="
                query_string += f"/?o={sep.join(params)}"
            result = self.rest.get(query_string)
            return {
                "url": f"{self.base_url}c/{result['_number']}",
                **result,
            }
        except exceptions.HTTPError as ex:
            if ex.response.status_code == 404:
                raise ChangeNotFound(change_id) from ex
            raise ex

    def get_branch_head(self, project_name, branch_name) -> str:
        """Get the latest commit SHA1 on the branch.

        Args:
            project_name (str): The name of the project to get the branch from.
            branch_name (str): The name of the branch to get the latest commit from.

        Returns:
            str: The SHA1 of the latest commit on the branch.
        """
        query_string = f"/projects/{project_name}/branches/{urllib.parse.quote(branch_name, safe='')}"
        return self.rest.get(query_string)["revision"]

    def get_related_commits(self, change_id: str, revision: str = None):
        """Gets all the commits in the gerrit chain.
        The chain will be ordered from top to bottom.

        Args:
            change_id (str): The change ID to query for
            revision (str): The revision SHA1 - if not supplied will use latest

        Raises:
            ChangeNotFound: If the change ID was not found

        Returns:
            dict: A dictionary with one key: 'changes',
                  with the value being a list of changes ordered from top to bottom.
        """
        change_id = self.parse_change_id(change_id)
        if not revision:
            change = self.get_change(change_id, "CURRENT_REVISION")
            revision = change["current_revision"]
        try:
            return self.rest.get(f"/changes/{change_id}/revisions/{revision}/related")
        except exceptions.HTTPError as ex:
            if ex.response.status_code == 404:
                raise ChangeNotFound(change_id) from ex
            raise ex

    def get_comments(self, change_id: str):
        """
        Returns a list of CommentInfo objects, each representing a comment in the given change
        Args:
            change_id : SHA1 or refspec of the change to retrieve from the Gerrit.
        """
        change_id = self.parse_change_id(change_id)
        query_string = f"/changes/{change_id}/comments"
        return self.rest.get(query_string)

    def get_messages(self, change_id: str):
        """
        Returns a list of CommentInfo objects, each representing a message in the given change.
        Args:
            change_id (str): The identifier of the Gerrit change.
        Returns:
            dict or list: The messages retrieved from the Gerrit REST API for the specified change.
        """
        change_id = self.parse_change_id(change_id)
        query_string = f"/changes/{change_id}/messages"
        return self.rest.get(query_string)

    def get_all_comments(self, change_id: str):
        """
        Returns a combined list of all Gerrit comments for a change, including inline/file comments and change messages.
        Each entry includes a 'type' field: 'inline' or 'message'.
        Args:
            change_id : SHA1 or refspec of the change to retrieve from the Gerrit.
        Returns:
            list: Combined list of comments and messages.
        """
        change_id = self.parse_change_id(change_id)
        # Inline/file comments
        file_comments = self.rest.get(f"/changes/{change_id}/comments")
        inline_comments = []
        for file_path, comments in file_comments.items():
            for comment in comments:
                comment_copy = comment.copy()
                comment_copy["file"] = file_path
                comment_copy["type"] = "comment"
                inline_comments.append(comment_copy)
        # Change messages
        messages = self.rest.get(f"/changes/{change_id}/messages")
        for msg in messages:
            msg["type"] = "message"
        return inline_comments + messages

    def get_labels(self, change_id: str, *args) -> dict:
        """
        Returns the labels associated with a given change.
        Args:
            change_id: SHA1 or refspec of the change to retrieve from the Gerrit.
        Returns:
            dict: A dictionary of LabelInfo objects associated with the given ChangeID. The key is the label's name
                  (as written in Gerrit).
                  See https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#label-info for the
                  LabelInfo object structure.
            args: Any additional options you want to forward to the query. Options are available at:
                  https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#query-options
        """
        change = self.get_change(change_id, "LABELS", *args)
        return change["labels"]

    def vote(self, change_id: str, labels=None, on_behalf_of="", comment=""):
        """
        Vote labels on the given change id.
        Allow to vote on behalf of another user.
        NOTICE !!! If you decide to vote on behalf of another user then make sure that this user exists in group which
                   is defined under current label while this label must be defined as (On Behalf Of).
                   for example: assume we want to vote on behalf of Lily Klugman which is PM.
                   She must be defined on SW_PM group on team forge.
                   Go to gerrit https://gerritwcs.ir.intel.com/admin/repos/wifi_drv-dev,access
                   make sure that "Label PM-Approval (on behalf of)" exists, otherwise create it under appropriate
                   Reference, add SW_PM group under current Reference, voila you can vote on behalf of all users exists
                   on SW_PM group.
        Args:
            change_id: SHA1 or refspec of the change to vote to.
            labels: Adds the supplied labels to the change Must be in the form of {"label1": value, "label2": value}.
                    Defaults to None.
            on_behalf_of: user email. allow to vote labels on behalf of this user.
            comment: post comment to gerrit
        Raises:
            Exception: If the change is not found or any other HTTP error occurs like
            user account not found or can't vote on behalf of geiven user.
        """
        try:
            change_id = self.parse_change_id(change_id)
            query_string = f"/changes/{change_id}/revisions/{change_id}/review"
            post_data = {"tag": "jenkins", "message": f"{comment}"}
            if labels:
                gerrit_labels = self.get_labels(change_id)
                # We can vote only labels which exist on gerrit revision,
                # therefore we must do intersection between user_labels and gerrit_labels
                exists_labels = {key: labels[key] for key in labels if key in gerrit_labels}
                if not exists_labels:
                    log.debug("Labels %s not in labels for %s!", labels, change_id)
                    return
                post_data["labels"] = exists_labels
                if on_behalf_of:
                    post_data["on_behalf_of"] = on_behalf_of
            self.rest.post(query_string, json=post_data)
        except exceptions.HTTPError as ex:
            response_content = ex.response.content.decode("utf-8")
            if ex.response.status_code == 400:
                raise ValueError(response_content) from ex
            raise ex

    def get_user(self, query: str):
        """Returns the user details of the given account by a query.
        Query could be user accound ID, email, name, etc.

        Args:
            account_id (int): The account ID to query

        Returns:
            dict: A user account information as detailed here:
                  https://gerrit-review.googlesource.com/Documentation/rest-api-accounts.html#account-detail-info
        """
        # Global fix for sys_windrvbuild's shenanigans.
        # LDAP only knows "sys_windrvbuild" while Gerrit only knows "windrvbuild".
        if query == "sys_windrvbuild@intel.com":
            query = "windrvbuild@intel.com"
        return self.rest.get(f"accounts/{query}/detail")

    def delete_votes(self, change_id: str, label_name: str):
        """
        Deletes all the votes for a label from a change.
        If multiple people voted on the same label - all of their votes will be removed.
        Args:
            change_id: SHA1 or refspec of the change to deletes votes in,
            label_name: Name of the label for which to delete all votes.
        Raises:
            Exception: In case deletion fails - usually happens when the change has already been submitted.
        """

        change_id = self.parse_change_id(change_id)
        labels = self.get_labels(change_id, "DETAILED_LABELS")
        if label_name not in labels:  # Check that the required label is even in the
            log.debug("Label %s not in labels for %s!", label_name, change_id)
            return

        account_id = []
        votes = labels[label_name].get("all")
        if not votes:
            log.warning("No votes were found for label %s so no votes will be deleted!", label_name)
            return
        for item in votes:
            if isinstance(item, dict) and "_account_id" in item and item.get("value"):
                account_id.append(item["_account_id"])
        for account in account_id:  # Delete all of them one by one
            self.rest.post(
                f"/changes/{change_id}/reviewers/{account}/votes/{label_name}/delete", json={"label": label_name}
            )

    def is_rejected_label(self, change_id: str, label_name: str):
        """
        If lable has -1 the func returns true, otherwise false
        Args:
            change_id: SHA1 or refspec of the change
            label_name: Name of the label for which we check if it has -1 vote
        Raises:
            Exception: In case check fails - return false.
        """

        change_id = self.parse_change_id(change_id)
        labels = self.get_labels(change_id)
        # Check that the required label exists and have vote(s)
        if (label_name not in labels) or (not labels[label_name]):
            log.debug("Label %s not in labels for %s or doesn't have any vote!", label_name, change_id)
            return False
        return "rejected" in labels[label_name].keys()

    def post_comment(self, change_id, comment, in_reply_to=None, file_path=None, line=None, unresolved=None) -> bool:
        """
        Post comment to current change id revision in gerrit.
        Args:
            change_id: SHA1 or refspec of the change to set comment in
            comment: comment to be set on gerrit
            in_reply_to: (optional) the ID of the comment to reply to (inline)
            file_path: (optional) file path for inline comment
            line: (optional) line number for inline comment
            unresolved: (optional) True/False to mark the comment as unresolved/resolved

        Returns:
            True if comment sent successfully, otherwise False
        """
        try:
            change_id = self.parse_change_id(change_id)
            query_string = f"/changes/{change_id}/revisions/{change_id}/review"
            post_data = {"tag": "jenkins"}

            if file_path and line:
                comment_obj = {
                    "line": line,
                    "message": comment,
                }
                if in_reply_to:
                    comment_obj["in_reply_to"] = in_reply_to
                if unresolved is not None:
                    comment_obj["unresolved"] = unresolved
                post_data["comments"] = {file_path: [comment_obj]}
            elif in_reply_to:
                log.error("in_reply_to requires file_path and line for inline comments.")
                return False
            else:
                # General comment
                post_data["message"] = f"{comment}"

            self.rest.post(query_string, json=post_data)
            return True
        except Exception as ex:
            log.error("Exception while post comment to '%s': %s", change_id, str(ex))
            return False

    def add_reviewers(self, change_id: str, mail_list: list) -> bool:
        """
        Add reviewers to gerrit
        Args:
            change_id: gerrit change id, commit sha, refspec number
            mail_list: users mail to be added to gerrit
        Returns:
            False if connection exception otherwise true.
        """
        res = True
        change_id = self.parse_change_id(change_id)
        query_string = f"/changes/{change_id}/reviewers"
        log.info("Next reviewers will be added to gerrit %s:", change_id)
        for mail in mail_list:
            try:
                respond = self.rest.post(query_string, json={"reviewer": mail})

                if error := respond.get("error"):
                    log.error("Error adding %s as reviewer: %s", mail, error)
                    res = False
                    continue

                for reviewer in respond["reviewers"]:
                    log.info(reviewer["name"])

            except exceptions.HTTPError as ex:
                error = ex.response.content.decode("utf-8")
                log.error("Error adding %s as reviewer: %s", mail, error)
                res = False
        return res

    def get_list_of_changed_files(self, change_id, show_deleted=True) -> list:
        """
        Rteurns list of changed file on a given change_id
        Args:
            change_id: gerrit change id, commit sha, refspec number
            show_delete: Whether or not to show deleted files as well.
                         Default is True for backwards compatibility.
        Returns:
            list: Changed file names.
                  Renamed files will show the new path instead of the old.
        """
        change = self.get_change(change_id, "ALL_REVISIONS", "ALL_FILES")
        if re.match(REFSPEC_PATTERN, change_id):  # For respec, get the specific refspec
            requested_revision = next(filter(lambda x: x["ref"] == change_id, change["revisions"].values()))
        elif re.match(SHA1_PATTERN, change_id):  # For SHA1, get the specific SHA1
            requested_revision = change["revisions"][change_id]
        else:  # For anything else, get latest revision
            requested_revision = change["revisions"][change["current_revision"]]
        files_dict = requested_revision["files"]
        if not show_deleted:
            files_dict = {x: y for x, y in files_dict.items() if y.get("status", "M") != "D"}
        return list(files_dict.keys())

    def get_file_content(self, change_id: str, file_path: str, start_line: int = None, end_line: int = None):
        """Gets the content of a file from a specified change.
        If the file is not part of the change, gets the content of the file as it would be if the change
        was checked out.

        Args:
            change_id (str): The Change ID / RefSpec / SHA1
            file_path (str): The file path (as known by git, relative and with "/" slashes)
            start_line (int, optional): The line number to start from (inclusive, 1-indexed). Defaults to None.
            end_line (int, optional): The line number to end at (inclusive, 1-indexed). Defaults to None.

        Returns:
            str: The content of the file

        Raises:
            ValueError: If the line range is invalid (start_line > end_line)
        """
        revision = self.parse_change_id(change_id)
        change = self.get_change(revision)
        file_path = urllib.parse.quote(file_path, safe="")
        query_string = f"/changes/{change['_number']}/revisions/{revision}/files/{file_path}/content"
        content = self.rest.get(query_string)

        if start_line is None and end_line is None:  # Default behavior is to return the full content
            return content
        start_line = max(start_line, 1)  # Ensure start_line is at least 1
        end_line = min(end_line, len(content.splitlines()) - 1)  # Ensure end_line is at least the length of the content

        if start_line > end_line:
            raise ValueError(
                f"Invalid line range: start_line ({start_line}) cannot be greater than end_line ({end_line})"
            )

        content = "\n".join(content.splitlines()[start_line - 1 : end_line])

        return content

    def set_commit_message(self, change_id: str, new_message: str, append: bool) -> bool:
        """
        Updates the commit message for a given Gerrit change.
        Args:
            change_id (str): Gerrit change id, commit sha, or refspec number
            new_message (str): The new commit message to set
            append (bool): If True, append new_message to the current message. If False, replace.
        """
        change_id = self.parse_change_id(change_id)
        change = self.get_change(change_id, "CURRENT_REVISION")
        if append:
            current_msg = self.get_commit_message(change_id)
            new_message = f"{current_msg.strip()}\n{new_message}"
        query_string = f"/changes/{change['_number']}/message"
        post_data = {"message": new_message}
        self.rest.put(query_string, json=post_data)

    def get_commit_message(self, change_id: str) -> str:
        """
        Get message from gerrit revision with a given change id
        Args:
            change_id: gerrit change id, commit sha, refspec number
        Returns:
            commit message
        """
        gerrit_change = self.get_change(change_id, "CURRENT_COMMIT", "CURRENT_REVISION")
        current_revision = gerrit_change["current_revision"]
        revision = gerrit_change["revisions"][current_revision]
        commit = revision["commit"]
        message = commit["message"]
        return message

    def get_branch_name(self, change_id: str) -> str:
        """
        Get branch name from gerrit revision with a given change id
        Args:
            change_id: gerrit change id, commit sha, refspec number
        Returns:
            commit branch name
        Raises:
            Exception in case of failed to get branch name from gerrit
        """
        gerrit_change = self.get_change(change_id, "CURRENT_COMMIT", "CURRENT_REVISION")
        return gerrit_change["branch"]

    def get_repo_name(self, change_id: str) -> str:
        """
        Get repository name from gerrit revision with a given change id
        Args:
            change_id: gerrit change id, commit sha, refspec number
        Returns:
            repository name (e.g. wifi_drv-dev, wcd_fw-dev)
        Raises:
            Exception in case of failed to get branch name from gerrit
        """
        gerrit_change = self.get_change(change_id, "CURRENT_COMMIT", "CURRENT_REVISION")
        return gerrit_change["project"]

    def get_commit_status(self, change_id: str) -> str:
        """
        Get commit status from gerrit revision with a given change id
        Args:
            change_id: gerrit change id, commit sha, refspec number
        Returns:
            status (e.g. MERGED, NEW, ABANDONED)
        Raises:
            Exception in case of failed to get branch name from gerrit
        """
        gerrit_change = self.get_change(change_id, "CURRENT_COMMIT", "CURRENT_REVISION")
        return gerrit_change["status"]


class RevisionNotFound(Exception):
    """A custom exception for when the refspec is found but the revision number is not"""

    def __init__(self, change, revision):
        super().__init__(f"Change number {change} has no revision number {revision}!")


class ChangeNotFound(Exception):
    """A custom exception for missing changes with the ChangeID in the data."""

    def __init__(self, change_id):
        super().__init__(f"ChangeID {change_id} not found!")
        self.change_id = change_id


class BadChangeID(Exception):
    """A custom exception when ChangeID is neither a SHA1 nor a RefSpec."""

    def __init__(self, change_id):
        super().__init__(f"ChangeID {change_id} is invalid! It needs to be either a SHA1 or a RefSpec!")
