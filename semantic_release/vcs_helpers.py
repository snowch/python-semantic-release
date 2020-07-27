"""VCS Helpers
"""
import logging
import os
import re
from functools import wraps
from pathlib import PurePath
from typing import Optional, Tuple
from urllib.parse import urlsplit

from git import GitCommandError, InvalidGitRepositoryError, Repo, TagObject
from git.exc import BadName

from .errors import GitError, HvcsRepoParseError
from .helpers import LoggedFunction
from .settings import config

try:
    repo = Repo(".", search_parent_directories=True)
except InvalidGitRepositoryError:
    repo = None

logger = logging.getLogger(__name__)


def check_repo(func):
    """Decorator which checks that we are in a git repository."""

    @wraps(func)
    def function_wrapper(*args, **kwargs):
        if repo is None:
            raise GitError("Not in a valid git repository")
        return func(*args, **kwargs)

    return function_wrapper


@check_repo
def get_commit_log(from_rev=None):
    """Yield all commit messages from last to first."""
    rev = None
    if from_rev:
        try:
            repo.commit(from_rev)
            rev = "...{from_rev}".format(from_rev=from_rev)
        except BadName:
            logger.debug(
                "Reference {} does not exist, considering entire history".format(
                    from_rev
                )
            )

    for commit in repo.iter_commits(rev):
        yield (commit.hexsha, commit.message.replace("\r\n", "\n"))


@check_repo
@LoggedFunction(logger)
def get_last_version(skip_tags=None) -> Optional[str]:
    """
    Find the latest version using repo tags.

    :return: A string containing a version number.
    """
    skip_tags = skip_tags or []

    def version_finder(tag):
        if isinstance(tag.commit, TagObject):
            return tag.tag.tagged_date
        return tag.commit.committed_date

    for i in sorted(repo.tags, reverse=True, key=version_finder):
        if re.match(r"v\d+\.\d+\.\d+", i.name):  # Matches vX.X.X
            if i.name in skip_tags:
                continue
            return i.name[1:]  # Strip off 'v'

    return None


@check_repo
@LoggedFunction(logger)
def get_version_from_tag(tag_name: str) -> Optional[str]:
    """
    Get the git commit hash corresponding to a tag name.

    :param tag_name: Name of the git tag (i.e. 'v1.0.0')
    :return: sha1 hash of the commit
    """
    for i in repo.tags:
        if i.name == tag_name:
            return i.commit.hexsha
    return None


@check_repo
@LoggedFunction(logger)
def get_repository_owner_and_name() -> Tuple[str, str]:
    """
    Check the 'origin' remote to get the owner and name of the remote repository.

    :return: A tuple of the owner and name.
    """
    url = repo.remote("origin").url
    split_url = urlsplit(url)
    # Select the owner and name as regex groups
    parts = re.search(r"[:/]([^:]+)/([^/]*?)(.git)?$", split_url.path)
    if not parts:
        raise HvcsRepoParseError

    return parts.group(1), parts.group(2)


@check_repo
def get_current_head_hash() -> str:
    """
    Get the commit hash of the current HEAD.

    :return: The commit hash.
    """
    return repo.head.commit.name_rev.split(" ")[0]


@check_repo
@LoggedFunction(logger)
def commit_new_version(version: str):
    """
    Commit the file containing the version number variable.

    The commit message will be generated from the configured template.

    :param version: Version number to be used in the commit message.
    """
    from .history import load_version_patterns

    commit_subject = config.get("commit_subject")
    message = commit_subject.format(version=version)

    # Add an extended message if one is configured
    commit_message = config.get("commit_message")
    if commit_message:
        message += "\n\n"
        message += commit_message.format(version=version)

    commit_author = config.get("commit_author", "semantic-release <semantic-release>",)

    for pattern in load_version_patterns():
        git_path = PurePath(os.getcwd(), pattern.path).relative_to(repo.working_dir)
        repo.git.add(str(git_path))

    return repo.git.commit(m=message, author=commit_author)


@check_repo
@LoggedFunction(logger)
def tag_new_version(version: str):
    """
    Create a new tag with the version number, prefixed with v.

    :param version: The version number used in the tag as a string.
    """
    return repo.git.tag("-a", "v{0}".format(version), m="v{0}".format(version))


@check_repo
@LoggedFunction(logger)
def push_new_version(
    auth_token: str = None,
    owner: str = None,
    name: str = None,
    branch: str = "master",
    domain: str = "github.com",
):
    """
    Run git push and git push --tags.

    :param auth_token: Authentication token used to push.
    :param owner: Organisation or user that owns the repository.
    :param name: Name of repository.
    :param branch: Branch to push to
    :param server_url: Name of the server. Will be used to identify a gitlab instance.
    :raises GitError: if GitCommandError is raised
    """
    server = "origin"
    if auth_token:
        token = auth_token
        if config.get("hvcs") == "gitlab":
            token = "gitlab-ci-token:" + token
        actor = os.environ.get("GITHUB_ACTOR")
        if actor:
            server = "https://{actor}:{token}@{server_url}/{owner}/{name}.git".format(
                token=token, server_url=domain, owner=owner, name=name, actor=actor
            )
        else:
            server = "https://{token}@{server_url}/{owner}/{name}.git".format(
                token=token, server_url=domain, owner=owner, name=name,
            )

    try:
        repo.git.push(server, branch, force=True)
        repo.git.push("--tags", server, branch)
    except GitCommandError as error:
        message = str(error)
        if auth_token:
            message = message.replace(auth_token, auth_token[0:1] + "#[AUTH_TOKEN]#" + auth_token[-1])
        raise GitError(message)


@check_repo
@LoggedFunction(logger)
def checkout(branch: str):
    """
    Check out the given branch in the local repository.

    :param branch: The branch to checkout.
    """
    return repo.git.checkout(branch)
