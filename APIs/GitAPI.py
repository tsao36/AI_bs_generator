"""
An API module for common git actions
"""

import os
import re
import json
import logging
import tarfile
import threading
import traceback
import tempfile
from datetime import datetime
import uuid
from shutil import rmtree
from git import Repo, GitCommandError, BadName, Commit

log = logging.getLogger("GitAPI")


class GitAPI:
    """Inits the instance of the GitAPI

    Args:
        repo_path (str): Path to the repo
    """

    def __init__(self, repo_path, is_bare=False):
        self.repo_path = repo_path
        self.lock_path = os.path.join(repo_path, "lock")
        if is_bare:
            self.repo = Repo.init(repo_path, bare=True)
            self.name = repo_path.rsplit("/", 1)[-1]  # Last part of the path
        else:
            self.repo = Repo(repo_path)
            self.name = self.repo.remotes.origin.url.split("/")[-1].replace(".git", "")  # Last part of the URL

        logging.getLogger("git.remote").setLevel(logging.ERROR)

    def get_last_commit_details(self, file_path: str, line: int) -> dict:
        """
        using git blame, get the latest commit for this file and line
        returns dict of author, date, sha1
        """
        # run git blame
        # use "realpath" since sometimes, the path has incorrect case and even if reachable, GIT doesn't like it
        res = self.repo.blame("HEAD", file=os.path.realpath(file_path), incremental=False, L=f"{line},+1")

        # get the top (latest) commit
        commit = res[0][0]

        # fill params
        return {
            "author": commit.author.name,
            "date": commit.committed_datetime.replace(tzinfo=None),
            "sha1": commit.hexsha,
        }

    def checkout_branch(self, branch, commit_hash=None, gerrit_refspec=None):
        """Forwarder for `checkout_branch` for the current repo"""
        return checkout_branch(self.repo_path, branch, commit_hash, gerrit_refspec)

    def get_commit(self, sha1):
        """Shortcut for getting the commit object"""
        return self.repo.commit(sha1)

    @staticmethod
    def _sanitize_branch_name(branch: str) -> str:
        return branch.replace("*", "").replace("origin/", "").strip()

    def get_branches(self, fetch=True, remote=False):
        """Get a list of branches in the repository

        Args:
            fetch (bool, optional): Whether or not to fetch from the remote. Also prunes stale branches.
                                    Defaults to True.
            remote (bool, optional): Whether or not to get remote branches or only local.
                                     Defaults to False (only local).

        Returns:
            list: A list of branches in the repository
        """
        if fetch:
            log.info("Fetching from remote with branches...")
            self.repo.remotes.origin.fetch(["--no-tags", "--prune"])
        if remote:
            return [self._sanitize_branch_name(branch) for branch in self.repo.git.branch("-r").split("\n")]
        return [self._sanitize_branch_name(branch) for branch in self.repo.git.branch().split("\n")]

    def get_commits(self, sha1_old, sha1_new):
        """
        Gets a list of commits (represented as a sha/subject tuple) between two commits.
        They need to be in the same continuity (sha1_old has to be an ancestor of sha1_new)
        Does not fetch! Be sure to fetch beforehand!

        Args:
            sha1_old (str): The SHA1 of the older commit chronologically
            sha1_new (str): The SHA1 of the newer commit chronologically

        Returns:
            list of tuples: 0: sha1, 1: subject
        """
        log.info("Getting commits between %s and %s...", sha1_old, sha1_new)

        # Get git log with pretty format as json
        output = self.repo.git.log(f"{sha1_old}..{sha1_new}", "--oneline", "--pretty=%H %s")
        commits = output.split("\n")
        log.info("Found: %s commits!", len(commits))
        return list(map(lambda x: (x[:40], x[41:]), commits))

    def get_commit_message(self, sha1):
        """
        Gets commit message by SHA1

        Args:
            sha1 (str): The SHA1

        Returns:
            list of string: Commit message split by newline
        """
        return self.get_commit(sha1).message

    def get_commit_date(self, sha1):
        """Returns the committed date as a datetime object

        Args:
            sha1 (str): The SHA1 of the commit to get the date for

        Returns:
            datetime: The datetime object representing the time of the commit
        """
        return self.get_commit(sha1).committed_datetime

    def get_commit_author(self, sha1):
        """Returns commit author (not commiter) as an Actor object

        Args:
            sha1 (str): The SHA1 of the commit to get the author for.

        Returns:
            Actor: The Git.Actor object representing the author.
                   See https://gitpython.readthedocs.io/en/stable/reference.html#git.util.Actor for more details.
        """
        return self.get_commit(sha1).committer

    def get_list_of_changed_files(self, sha1):
        """
        Gets list of changes files for a given commit SHA1

        Args:
            sha1 (str): The SHA1

        Returns:
            list of string: file names list
        """
        log.info("Getting list of changed files for commit %s ", sha1)
        output = self.repo.git.log(f"{sha1}", "-1", "--pretty=format:", "--name-only")
        changed_file_names = output.split("\n")
        return changed_file_names

    def get_diff_from_branch(self, branch, modes=["A", "M", "R"], show_old=False, show_new=True, files_only=True):
        """Get a list of changed files between the current index in the remote branch head.
        For some reason, doesn't detect at untracked files. Make sure to at least stage if not commit your files first.

        Args:
            branch (str): The name of the remote branch to diff against
            modes (list, optional): What diff modes to include.
                                    Options are:
                                        `A` for added paths
                                        `D` for deleted paths
                                        `R` for renamed paths
                                        `M` for paths with modified data
                                        `T` for changed in the type paths
                                    Defaults to ["A", "M", "R"].
            show_old (bool, optional): Include the old filename of deleted or renamed paths. Defaults to False.
            show_new (bool, optional): Include the new filename of added or renamed paths. Defaults to True.
            files_only (bool, optional): Only include files, not folders. Defaults to True.

        Returns:
            list: List of files that have changed.
        """
        remote_head_sha1 = self.get_branch_head(branch)
        try:
            remote_head = self.repo.commit(remote_head_sha1)
        except ValueError:
            log.warning("Couldn't find remote head in local index. Fetching...")
            self.repo.remotes.origin.fetch()
            remote_head = self.repo.commit(remote_head_sha1)
        diff_list = self.repo.index.diff(remote_head, R=True)  # diffs are reverse, so we need R=True to re-reverse
        file_list = []
        for diff in diff_list:
            if diff.change_type in modes:
                path_to_add = diff.b_path
                if diff.change_type in ["D", "R"] and show_old:
                    path_to_add = diff.a_path
                if diff.change_type in ["A", "R"] and show_new:
                    path_to_add = diff.b_path
                if files_only and not os.path.isfile(os.path.join(self.repo_path, path_to_add)):
                    continue
                file_list.append(path_to_add)
        return file_list

    def find_last_common_ancestor(self, sha1_a, sha1_b):
        """Finds the last common ancestor of two commits - if it exists

        Args:
            sha1_a (str): SHA1 of one of the commits
            sha1_b (str): SHA1 of the other commit

        Returns:
            str: SHA1 of the least common ancestor if exists - or None if not
        """
        log.info("Finding last common ancestor of %s and %s...", sha1_a, sha1_b)
        log.info("First we need to find the branches...")
        a_branch = self.get_branch_from_commit(sha1_a)
        b_branch = self.get_branch_from_commit(sha1_b)
        if not (a_branch and b_branch):
            log.error("Couldn't find branch of one of the commits. See previous error for details.")
            return None

        common = self.repo.git.merge_base("origin/" + a_branch[0], "origin/" + b_branch[0])
        if common and common != "":
            log.info("Found latest common ancestor: %s", common)
            return common
        log.error("Last common ancestor not found!")
        return None

    def is_last_common_ancestor_needed(self, old_sha1, new_sha1):
        """
        Last common ancestor is required only if both SHA1 are from different branches.
        This checks if it is needed or not.

        Args:
            old_sha1 (str): SHA1 of the older commit
            new_sha1 (str): SHA1 of the newer commit

        Returns:
            bool: Whether it is needed or not
        """

        old_driver_branch = self.get_branch_from_commit(old_sha1)
        new_driver_branch = self.get_branch_from_commit(new_sha1)

        common_drv_branch = [value for value in old_driver_branch if value in new_driver_branch]
        return bool(common_drv_branch)

    def clean_repository(self, repo_name):
        """
        Cleans the repository using git clean

        Args:
            repo_name (str): For logging purposes, the name of the repo
        """
        log.info("Cleaning %s repository ", repo_name)
        try:
            self.repo.git.clean("-xdf")
            self.repo.git.checkout("--", ".")
        except Exception as exc:
            log.error("Failed to clean repository")
            log.error(exc)

    def reset_repository(self):
        """Runs a hard git reset on the repo"""
        log.info("Reset repository ")
        try:
            self.repo.git.reset("--hard")
        except Exception as exc:
            log.error("Failed to reset repository")
            log.error(exc)

    def checkout_to_new_local_branch(self, br_name):
        """
        Checks out the branch locally

        Args:
            br_name (str): Name of the branch to check out
        """
        log.info("Create and checkout to new branch %s", br_name)
        try:
            self.repo.git.checkout("-b", br_name)
        except Exception as exc:
            log.error("Failed to create and checkout to new branch %s", br_name)
            log.error(exc)

    def delete_local_branch(self, br_name):
        """
        Delete the branch locally

        Args:
            br_name (str): Name of the branch to delete.
        """
        log.info("Delete local branch %s", br_name)
        try:
            self.reset_repository()
            self.repo.git.checkout("master")
            self.repo.git.branch("-D", br_name)

        except Exception as exc:
            log.error("Failed to delete local branch %s", br_name)
            log.error(exc)

    def get_branch_from_commit(self, sha1, fetched=False, fail_if_not_found=False):
        """Returns the remote branch (or branches) the commit exists in.
        Strips away all prefixes like remote name, origin, etc.

        Args:
            sha1 (str): Sha1 of the commit to search for
            fetched (bool, optional): Whether or not the repo has already been fetched from.
                            Defaults to False.
            fail_if_not_found (bool, optional): If commit is not found, raise an exception.
                                                Defaults to False for backwards compatibility.

        Raises:
            CommitNotFound: If the commit was not found and `fail_if_not_found` is True.
            GitCommandError: For any other unexpected git error

        Returns:
            list: A list of strings representing the branches the commit is in - not including HEAD.
                  None if not found
        """
        log.info("Finding branch of %s...", sha1)

        try:
            output = self.repo.git.branch("-r", "--contains", sha1, "--format=%(refname)")
        except GitCommandError as gce:
            if any(x in gce.stderr for x in ["no such commit", "malformed object name"]):
                output = None
            else:
                raise gce

        if not output or output == "":
            if not fetched:
                log.warning("Couldn't find commit %s. Fetching and trying again...", sha1)
                self.repo.git.fetch("--no-tags", "--prune", "--recurse-submodules=no")
                return self.get_branch_from_commit(sha1, True, fail_if_not_found)
            log.error("No commit found with SHA1 %s!", sha1)
            if fail_if_not_found:
                raise CommitNotFound(sha1, self.repo.remotes.origin.url.split("/")[-1])
            return None

        refs = output.split("\n")
        refs = list(filter(lambda x: (not "origin/HEAD" in x) and (x != ""), refs))
        refs = list(map(lambda x: re.sub(r"\s*(refs\/)?(remotes\/)?origin\/", "", x), refs))
        log.info("Found %s in %s branch%s: %s", sha1, len(refs), "" if len(refs) == 1 else "s", ", ".join(refs))
        if not refs:
            log.error("No commit found with SHA1 %s", sha1)
            return None
        return refs

    def short_to_long(self, short_sha1, fetched=False):
        """Convert a short SHA1 to a long SHA1

        Args:
            short_sha1 (str): The short SHA1 to try and resolve
            fetched (bool): Whether or not we already fetched from the repository.
                            If false, will try to fetch if SHA1 is not found before returning an error

        Returns:
            str: The full (40 character) SHA1 of the resolved commit - if found

        Raises:
            git.BadName: If the commit is not found.
                         The provided short SHA1 will be in the commit's args (ex.args[0])
        """
        try:
            return str(self.repo.rev_parse(short_sha1))
        except BadName as bne:
            if not fetched:
                log.warning("Couldn't find commit %s. Fetching and trying again...", short_sha1)
                self.repo.git.fetch("--no-tags", "--prune", "--recurse-submodules=no")
                return self.short_to_long(short_sha1, True)
            # Check for ambiguity
            try:
                self.repo.git.rev_parse(short_sha1)
            except GitCommandError as gce:
                if "is ambiguous" in gce.stderr:
                    commits = [
                        re.match(r"hint:\s{3}(\S*)", x).group(1)
                        for x in gce.stderr.split("\n")
                        if re.match(r"hint:\s{3}(\S*)", x)
                    ]
                    raise AmbiguousShortCommit(short_sha1, commits) from gce
            raise bne

    def get_branch_head(self, branch_name):
        """Just a redirect to the global get_branch_head with the origin url"""
        return get_branch_head(branch_name, self.repo.remotes.origin.url)

    def add_notes(self, sha1, notes):
        """
        Add the given notes string to the SHA1 as git-notes.
        For more information, see: https://git-scm.com/docs/git-notes

        Args:
            sha1 (sha1): The SHA1 to add the notes to.
            notes (str): The notes string
        """
        temp_path = os.path.join(tempfile.gettempdir(), sha1[:8] + ".json")
        with open(temp_path, "w", encoding="utf-8") as notes_file:
            notes_file.write(notes)
        log.info("Updating notes from origin...")
        self.repo.git.fetch("origin", "refs/notes/*:refs/notes/*")
        log.info("Saving new notes to %s...", sha1[:8])
        self.repo.git.notes("add", "--force", f"--file={temp_path}", sha1)
        log.info("Pushing new notes to origin...")
        self.repo.git.push("origin", "refs/notes/*")
        log.info("Notes for %s saved successfully!", sha1[:8])

    def get_commits_with_notes(self, fetch=True):
        """
        Gets a list of all commits in the current repository that have notes.
        """
        if fetch:
            log.info("Updating notes from origin...")
            self.repo.remotes.origin.fetch("refs/notes/*:refs/notes/*")
        return [x.split(" ")[1] for x in self.repo.git.notes("list").split("\n")]

    def get_notes(self, sha1, return_type=str, verify=True, fetch=True):
        """
        Get notes for the given SHA1.
        Does not fetch or verify existence, recommended to use "get_commits_with_notes" first.

        Args:
            sha1 (str): SHA1 of the commit to get notes for.
            return_type (type): The type of the result to return. Either str or dict.
            verify (bool): Whether or not to check if the commit has notes before trying to get it.
                           If commit has no notes and verify is True, None will be returned.
                           If Commit has no notes and verify is False, a GitCommandError will be raised.
                           Defaults to True.
            fetch (bool): Whether or not to fetch from origin before trying to get the notes.

        Returns:
            dict, str: The notes in the requested format.
            None: If verify is True and no notes were found for the SHA1.

        Raises:
            GitCommandError: If Verify is False and no notes were found for the SHA1.
        """
        if not isinstance(return_type, type):
            raise TypeError("return_type must be a type, not a variable!")
        if return_type not in (str, dict):
            raise ValueError(f"return_type must be either str or dict, not {return_type.__name__}")
        if fetch:
            log.info("Updating notes from origin...")
            self.repo.remotes.origin.fetch("refs/notes/*:refs/notes/*")
        if verify:
            if sha1 not in self.get_commits_with_notes(fetch=False):  # We fetched earlier, or we don't want to
                return None
        notes = self.repo.git.notes("show", sha1)
        if return_type == dict:
            return json.loads(notes)
        return notes

    def get_tags(
        self,
        fetch=True,
        search_pattern=None,
        branch=None,
        from_date: datetime = None,
        to_date: datetime = None,
        depth: int = None,
        verbose=False,
    ):
        """Get a list of tag reference for the repositories.
        Tags are always sorted by a descending order of commit date.

        Args:
            fetch (bool, optional): Whether or not to fetch from git first. Defaults to True.
            branch (str, optional): Limit to this branch
            search_pattern (str, optional): The search pattern to apply to the tags.
                                            Use * for wildcards. (e.g. "jenkins-Core_build_nightly-*")
                                            Defaults to None.
            from_date (datetime, optional): Only show tags whose commits are from after this datetime.
                                            Defaults to None.
            to_date (datetime, optional): Only show tags whose commits are from before this datetime.
                                          Defaults to None.
            depth (int, optional): Only return this number of tags.
                                   Defaults to None (no limit).
            verbose (bool): Whether to print all the tags (if True) or just new ones (if False)
                            Highly discouraged to the amount of tags.
                            Defaults to False.

        Returns:
            list<TagReference>: A list of all the matching TagReference objects.
                                Learn more about TagReferences at:
                                https://gitpython.readthedocs.io/en/stable/reference.html#git.refs.tag.TagReference
        """
        if (to_date and from_date) and to_date <= from_date:
            raise InvalidDateRange(from_date, to_date)

        if not verbose:
            # The 'git.remote' logger is too verbose and ignores the 'verbose' flag.
            # Need to suppress it manually.
            git_logger = logging.getLogger("git.remote")
            old_log_level = git_logger.level
            git_logger.setLevel(logging.ERROR)

        if fetch:
            log.info("Fetching from remote with tags...")
            self.repo.remotes.origin.fetch(refspec="refs/tags/*:refs/tags/*", verbose=verbose, quiet=(not verbose))

        kwargs = ["--list"]
        if branch:
            kwargs.append(f"--merged=origin/{branch}")

        if search_pattern:
            kwargs.append(search_pattern)

        kwargs += ["--sort=-committerdate", "--format=%(refname)"]  # We always sort by descending date

        taglines = [x for x in self.repo.git.tag(*kwargs).split("\n") if x]  # Don't include empty strings
        tags = []
        log.info("Processing tags...")
        for tagline in taglines:
            tag = self.repo.tag(tagline)
            if to_date and tag.commit.committed_date > to_date.timestamp():
                continue  # Since we're descending date, to get to the max date we just continue until we pass it
            if from_date and tag.commit.committed_date < from_date.timestamp():
                break  # Since we're descending date, to get to min we just break once we pass it
            tags.append(tag)
            if depth and len(tags) >= depth:
                break

        if not verbose:
            git_logger.setLevel(old_log_level)

        return tags

    def get_file_content(self, source_ref, file_path):
        """Gets the content of a file as it exists in a specific commit.

        Args:
            source_ref (str): A valid git ref (SHA1, branch, etc.)
            file_path (str): The file path as it is in git (relative and with "/" slashes)

        Returns:
            str: The file content
        """
        commit = self.repo.commit(source_ref)
        return self.repo.git.show(f"{commit}:{file_path}")

    def get_blame_for_line(self, commit_id, file_path, line_no: int) -> Commit:
        """Find which commit a certain line was changed in last.
        Note that this function does not fetch on its own - so fetch before calling it if needed.

        Args:
            commit_id (str): The commit to start looking backwards from.
                             Can be any valid revision string, as seen here:
                             https://git-scm.com/docs/git-rev-parse#_specifying_revisions
            file_path (str): The file path (relative to the repository root) to check the blame for.
            line_no (int): The line number of the file (as it is in the given commit) to check the blame for.

        Raises:
            ValueError: If the commit doesn't exist.

        Returns:
            Commit: The commit where the given line was last changed.
                    For a detail on what the commit object contains, see:
                    https://gitpython.readthedocs.io/en/stable/reference.html#module-git.objects.commit
        """
        self.repo.rev_parse(commit_id)  # Just to check that the commit exists - will throw a ValueError if not.
        blame_gen = self.repo.blame_incremental(commit_id, file_path)
        for blame_entry in blame_gen:
            if line_no in blame_entry.linenos:
                return blame_entry.commit
        raise Exception(
            f"No blame was found for line {line_no} in {file_path}! "
            "It might be out of range or doesn't yet exist in this commit."
        )

    def get_list_of_files_on_remote(self, ref="HEAD"):
        """Get a list of files on a remote repository at a specific ref.
        Does so by archiving the repository and getting the list of files in the archive.
        Strangely, this is ths simplest way to achieve this.

        Args:
            ref (str, optional): The ref to get the list of files at.
                                 Defaults to "HEAD" (repository default branch).

        Returns:
            list: A list of files in the repository
        """
        return get_list_of_files_on_remote(self.repo.remotes.origin.url, ref)


def get_branch_head(branch_name, url):
    """
    Retrieves the commit at the top of the branch remote.

    Args:
        branch_name (str): Name of branch for which we want to retrieve the commit.
        url (str): The URL of the repository we're querying.

    Returns:
        str: SHA1 of the head of the branch, or None if not found
    """
    log.info("Getting head of %s from %s...", branch_name, url)
    temp_path = os.path.join(tempfile.gettempdir(), str(uuid.uuid1()))
    os.makedirs(temp_path)
    temp_repo = Repo.init(temp_path)
    result = (temp_repo.git.ls_remote("--heads", url, f"refs/heads/{branch_name}")).split("\t", 1)[0]
    log.debug("[%s] Lock released!", threading.get_ident())
    rmtree(temp_path)
    if not result:
        log.error("Branch or branch head not found!")
        return None

    log.info("Head of %s seems to be %s.", branch_name, result)
    return result


def get_branch_list(url):
    """
    Get a list of branches from a remote repository

    Args:
        url (str): The repo URL to get the branch list of

    Returns:
        list: The list of branches
    """
    temp_path = os.path.join(tempfile.gettempdir(), str(uuid.uuid1()))
    os.makedirs(temp_path)
    temp_repo = Repo.init(temp_path)
    result_list = temp_repo.git.ls_remote("--heads", url).split("\n")
    results = list(map(lambda x: x.split("\t")[1].replace("refs/heads/", ""), result_list))
    rmtree(temp_path)
    return results


def clone_repo(repo, url, **kwargs):
    """Clone a repository to a local directory

    Args:
        repo (str): The local directory to clone the repository to
        url (str): The URL of the repository to clone
        **kwargs: Additional arguments to pass to the clone command (branch, depth, etc.)
                  for more information see: https://git-scm.com/docs/git-clone

    Returns:
        Repo: The gitpython Repo object representing the cloned repository
    """
    if os.path.exists(repo):
        log.warning("Repository folder already exist %s, cleaning workspace", repo)
        change_folder_permission(repo)
        rmtree(repo, ignore_errors=True)
    log.info("Cloning %s to %s", url, repo)
    repo = Repo.clone_from(url=url, to_path=repo, **kwargs)
    log.info("Repo was cloned successfully")
    return repo


def change_folder_permission(path):
    """Revursively change the permissions of a given path to all 777"""
    for root, dirs, files in os.walk(path):
        for f in dirs + files:
            os.chmod(os.path.join(root, f), 0o777)


def mirror_branch(source_url, target_url, branch, temp_dir, depth=50):
    """
    Mirror a specific branch from source repository to target repository.
    Uses shallow fetch for efficiency.

    Args:
        source_url (str): Source repository URL (e.g., Gerrit)
        target_url (str): Target repository URL with authentication (e.g., GitHub with token)
        branch (str): Branch name to mirror
        temp_dir (str): Temporary directory for the repository
        depth (int): Number of commits to fetch (default: 50)

    Returns:
        Repo: The gitpython Repo object

    Raises:
        GitCommandError: If any git operation fails
    """
    log.info("Initializing git repository in %s", temp_dir)
    repo = Repo.init(temp_dir)

    # Add source as origin
    log.info("Adding source remote: %s", source_url)
    origin = repo.create_remote("origin", source_url)

    # Fetch branch with shallow depth
    log.info("Fetching branch %s with depth %d from source", branch, depth)
    origin.fetch(refspec=f"+refs/heads/{branch}:refs/remotes/origin/{branch}", depth=depth)

    # Checkout the branch
    log.info("Checking out branch %s", branch)
    repo.git.checkout("-b", branch, f"origin/{branch}")

    # Add target as remote
    log.info("Adding target remote")
    repo.create_remote("target", target_url)

    return repo


def checkout_branch(repo_path, branch, commit_hash=None, gerrit_refspec=None):
    """Check out branch head. DOES NOT LOCK GIT! Lock outside!

    Args:
        branch (string): Branch name

    Return:
        None
    """
    is_gerrit_changes = False

    try:
        repo = Repo(repo_path, search_parent_directories=True)
        # Fetch all
        log.info("Fetching all...")
        # create fetch string
        if gerrit_refspec:
            repo.remotes.origin.fetch(refspec=gerrit_refspec)
            is_gerrit_changes = True
        else:
            repo.git.fetch("--no-tags")

        # Checkout branch or gerrit changes
        if is_gerrit_changes:
            log.info("Checking out FETCH_HEAD")
            repo.git.checkout("FETCH_HEAD")
        else:
            log.info('Checking out branch "%s"...', branch)
            repo.git.checkout(branch)

            # Reset to remote
            if commit_hash:
                reset_to = commit_hash
            else:
                reset_to = "origin/" + branch

            repo.git.reset("--hard", reset_to)

            # Pull latest only if no specific commit was asked
            if not commit_hash:
                log.info("Pull latest...")
                repo.git.pull()

        log.info("Check-out branch %s, commit_hash: %s succeeded", branch, commit_hash)
        return True
    except Exception as exc:
        log.error("Check-out branch %s failed.\n%s", branch, exc)
        log.error(traceback.print_exc())
        return False


def commit_branch(repo_path, commit_msg, branch_name):
    """commit branch with the new changes

    Args:
        repo (str): project local repository to checkout
        commit_msg (str): the git commit message
    Return:
        sha1 (str): if successfully commited
        False (bool): otherwise
    """
    try:
        repo = Repo(repo_path, search_parent_directories=True)
        # commit brabch
        log.info("Commit Branch.. ")
        repo.git.commit("-m", commit_msg)

        # push brach
        log.info("Push Branch...")
        repo.git.push("--set-upstream", "origin", branch_name)

        # Fetch all
        log.info("Fetching all...")
        repo.git.fetch("--no-tags")

        # return sha1
        return repo.head.commit.binsha.hex()
    except Exception as exc:
        log.info("an error occured while commiting the changes..")
        log.error(exc)
        return False


def get_list_of_files_on_remote(remote_url, ref="HEAD"):
    """Get a list of files on a remote repository at a specific ref.
    Does so by archiving the repository and getting the list of files in the archive.
    Strangely, this is ths simplest way to achieve this.

    Args:
        remote_url (str): The URL of the remote repository
        ref (str, optional): The ref to get the list of files at.
                             Defaults to "HEAD" (repository default branch).

    Returns:
        list: A list of files in the repository
    """
    temp_path = os.path.join(tempfile.gettempdir(), str(uuid.uuid1()))
    os.makedirs(temp_path)
    temp_repo = Repo.init(temp_path)
    temp_repo.create_remote("origin", remote_url)
    temp_file = os.path.join(temp_path, "archive.tar")
    log.info("Getting file names for repository %s at ref %s - this could take a while...", remote_url, ref)
    with open(temp_file, "wb") as temp_file:
        temp_repo.archive(ostream=temp_file, format="tar", treeish=ref, remote=remote_url)
    with tarfile.open(os.path.join(temp_path, "archive.tar"), "r") as tar:
        files = tar.getnames()
    rmtree(temp_path)  # Cleanup
    log.info("Successfully got %d file names from %s at ref %s", len(files), remote_url, ref)
    return files


class InvalidDateRange(Exception):
    """Exception when trying to use an invalid date range"""

    def __init__(self, from_date: datetime, to_date: datetime):
        if from_date == to_date:
            self.message = "You can't use the same datetime as both the beginning and the end of a date range."
        elif from_date > to_date:
            self.message = f"Your dates are reversed. {from_date} comes after {to_date}."
        else:
            self.message = "This exception should not have been thrown, the date range is actually fine."
        super().__init__(self.message)
        self.from_date = from_date
        self.to_date = to_date


class AmbiguousShortCommit(Exception):
    """Exception when trying to convert short commit to long commit but more than one option exists"""

    def __init__(self, short_sha1, matching_commits):
        self.message = (
            f"Short SHA1 {short_sha1} resolves to {len(matching_commits)} different commits. "
            "Try using more characters to avoid conflicts. "
            "The matching commits are: " + (", ".join(matching_commits))
        )
        super().__init__(self.message)
        self.short_sha1 = short_sha1
        self.matching_commits = matching_commits


class CommitNotFound(Exception):
    """Exception for when trying to find something based on a commit that can not be found"""

    def __init__(self, sha1: str, repository: str):
        self.message = f"No commit could be found on {repository} with SHA1 {sha1}"
        self.sha1 = sha1
        self.repository = repository
        super().__init__(self.message)
