"""Wrapper for PyGitHub for easier usage"""

import re
import logging
import json
from github import Auth, Github, Repository, UnknownObjectException, PullRequest
from git import Repo as GitRepo


logging.basicConfig(format="%(asctime)s [%(levelname)s][%(name)s] %(message)s")
log = logging.getLogger("GitHubAPI")


class GitHub:
    """A GitHub API Instance.
    Supplies commonly needed shortcuts for various PyGithub APIs.
    The PyGithub documentation can be found at https://pygithub.readthedocs.io/en/stable/
    Note that this is not like GitAPI in that it doesn't control local files.

    Args:
        repo (str): The repository name. Must be in the format `owner/repo_name`
                    e.g. `intel-innersource/drivers.wireless.wifi.windows.potatofarm`
        token (str): The API Token to use for authentication.

    Raises:
        ValueError: If the repo name is not in the right format
    """

    def __init__(self: Repository, repo: str, token: str):
        if not re.match(r"^\S+/\S+$", repo):
            raise ValueError(f"The repo must be in the format owner/repo_name, which the supplied {repo} is not!")

        auth = Auth.Token(token)
        self._github = Github(auth=auth)
        self._token = token
        self.username = self._github.get_user().login

        # Init some useful stuff
        self.repo = self._github.get_repo(repo)
        """A shortcut to the Repository object.\n
The full documentation of the Repository object can be found at
https://pygithub.readthedocs.io/en/stable/github_objects/Repository.html"""

        log.info("GitHub API Initialized for user %s in %s", self.username, self.repo)

    def get_workflow_id(self, name: str) -> int:
        """Returns the ID associated with a specific workflow name.
        Assumes names are unique.

        Args:
            name (str): The name to retrieve

        Raises:
            NoSuchWorkflow: If no workflow with this name exists in this repo.
        """
        log.info('Trying to match an ID to a workflow named "%s"...', name)
        for workflow in self.repo.get_workflows():
            if workflow.name == name:
                log.info('Matched workflow "%s" to ID %d!', name, workflow.id)
                return workflow.id
        raise NoSuchWorkflow(name, self.repo.full_name)

    def trigger_workflow(self, name: str, ref: str, inputs: dict = None):
        """Trigger a workflow in the repository

        Args:
            name (str): The name of the workflow (`name` in the dispatch file)
            ref (str): The ref (SHA1, Branch, or Ref) to trigger the workflow on.
            inputs (dict, optional): _description_. Defaults to None.

        Returns:
            bool: True if triggered successfully, false otherwise.
        """
        log.info(
            'Triggering workflow "%s" on "%s" in repo "%s" %s',
            name,
            ref,
            self.repo.full_name,
            ("with inputs: \n" + json.dumps(inputs, indent=4)) if inputs else "with no inputs...",
        )
        if not self.repo.get_workflow(self.get_workflow_id(name)).create_dispatch(ref, inputs):
            raise FailedToTrigger(name, self.repo.full_name)

    def create_pull_request(self, git_repo: GitRepo, target_branch: str = None, force: bool = False) -> PullRequest:
        """
        Create a pull request by pushing the changes from the head of the git repo to a fork and then creating the PR.

        Args:
            git_repo (GitRepo): A git repository instance to create the PR from.
            target_branch (str, optional): The remote branch name to merge to.
                                           If `None`, assumes currently checked out branch.
            force (bool, optional): Whether to force push the changes. Defaults to False.

        Raises:
            NoCheckedOutBranch: If no branch is currently checked out.

        Returns:
            PullRequest: The created Pull Request object.
        """
        if git_repo.head.is_detached:
            raise NoCheckedOutBranch()
        target_branch = target_branch or git_repo.active_branch

        # First check if we have a fork
        fork_name = f"{self.username}/{self.repo.name}"
        log.info("Checking if we have a fork...")
        try:
            fork = self._github.get_repo(fork_name)
        except UnknownObjectException:
            log.info("No fork found, creating one at %s...", fork_name)
            fork = self.repo.create_fork()

        # Define Push URL
        push_url = fork.clone_url.replace("https://github", f"https://{self._token}@github")

        # Push the changes to the fork using the active branch name (*not* the target, that's what the PR is for)
        log.info("Pushing changes to %s...", fork_name)
        git_repo.git.push(push_url, f"HEAD:{git_repo.active_branch}", force=force)

        # Create the PR
        log.info("Creating Pull Request...")
        pull_request = self.repo.create_pull(
            title=git_repo.head.commit.message.split("\n")[0],
            body=git_repo.head.commit.message,
            head=f"{self.username}:{git_repo.active_branch}",
            base=target_branch,
        )

        log.info("Pull Request created at %s", pull_request.html_url)
        return pull_request


class NoCheckedOutBranch(Exception):
    """Exception when trying to create a PR without a branch checked out."""

    def __init__(self):
        super().__init__("No branch is currently checked out! You must check out a branch before creating a PR!")


class FailedToTrigger(Exception):
    """Exception when trying to get a workflow that doesn't exist."""

    def __init__(self, workflow: str, repo: str):
        self.repo = repo
        self.workflow = workflow
        super().__init__(f'Failed to trigger workflow "{workflow}" in the repository "{repo}"!')


class NoSuchWorkflow(Exception):
    """Exception when trying to get a workflow that doesn't exist."""

    def __init__(self, workflow: str, repo: str):
        self.repo = repo
        self.workflow = workflow
        super().__init__(f'The workflow "{workflow}" does not exist in the repository "{repo}"!')
