"""Private, task-local write permits, shared by the API and Git backends."""

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .errors import TeaError


@dataclass(frozen=True)
class WritePermit:
    settings_id: int
    repo: str
    operation: str
    branch: str | None = None
    path: str | None = None
    method: str = "get"


_current: ContextVar[WritePermit | None] = ContextVar("jasminetea_write_permit", default=None)
cancel_event: ContextVar[object | None] = ContextVar("jasminetea_git_cancel", default=None)


@contextmanager
def permitted(permit: WritePermit):
    token = _current.set(permit)
    try:
        yield
    finally:
        _current.reset(token)


def api_path_pattern(path: str) -> str:
    # Gitea's contents filepath is a greedy final path, unlike owner/index/ref segments.
    escaped = re.escape(path).replace(re.escape("{filepath}"), ".+")
    return re.sub(r"\\\{[^}]+\\\}", "[^/]+", escaped)


def require_permit(settings, repo, *, operation=None, branch=None, endpoint=None, method=None):
    settings.authorize_write(repo)
    permit = _current.get()
    if permit is None or permit.repo != repo or permit.settings_id != id(settings):
        raise TeaError("write_disabled", "A validated service write permit is required.")
    settings.authorize_operation(repo, permit.operation)
    if operation is not None and operation != permit.operation:
        raise TeaError("write_disabled", "Write operation does not match its permit.")
    if branch is not None:
        branch = settings.normalize_branch(branch)
        if permit.branch != branch:
            raise TeaError("write_disabled", "Write branch does not match its permit.")
        settings.authorize_branch(branch)
    if endpoint is not None:
        pattern = api_path_pattern(permit.path or "")
        if method.lower() != permit.method or not re.fullmatch(pattern, endpoint):
            raise TeaError("write_disabled", "Mutation endpoint does not match its operation permit.")
    return permit
