import asyncio
import base64
import inspect
from functools import wraps
from typing import Literal
from urllib.parse import quote

from .api import GiteaClient
from .config import Settings, file_path, repo_endpoint, repository
from .errors import TeaError
from .git_backend import GitBackend, object_sha


def exposed(
    *,
    write: bool = False,
    destructive: bool = False,
    path: str | None = None,
    method: str = "get",
    fields: tuple[str, ...] = (),
):
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        async def wrapped(*args, **kwargs):
            if write:
                bound = signature.bind(*args, **kwargs)
                args[0].settings.authorize_write(bound.arguments["repo"])
            return await function(*args, **kwargs)

        wrapped.tea_meta = {
            "write": write,
            "destructive": destructive,
            "path": path,
            "method": method,
            "fields": fields,
        }
        return wrapped

    return decorate


class TeaService:
    def __init__(self, settings: Settings, *, api: GiteaClient | None = None, git: GitBackend | None = None):
        self.settings = settings
        self.api = api or GiteaClient(settings)
        self.git = git or GitBackend(settings)
        self._git_identity_ready = False

    async def close(self):
        await self.api.close()

    def methods(self) -> dict:
        return {
            name: getattr(self, name)
            for name in dir(self)
            if getattr(getattr(self, name), "tea_meta", None) is not None
        }

    def _link(self, repo: str) -> str:
        return self.settings.base_url + "/" + "/".join(quote(p, safe="") for p in repository(repo).split("/"))

    async def _get(self, repo: str, suffix: str = "", params: dict | None = None, text: bool = False):
        data = await self.api.request("GET", repo_endpoint(repo) + suffix, params=params, text=text)
        return {"repo": repo, "url": self._link(repo), "data": data}

    async def _page(self, repo: str, suffix: str, page: int, limit: int, params: dict | None = None):
        return {
            "repo": repo,
            "url": self._link(repo),
            **await self.api.page(repo_endpoint(repo) + suffix, page=page, limit=limit, params=params),
        }

    async def _write(self, repo: str, method: str, suffix: str, body: dict | None = None):
        result = await self.api.request(method, repo_endpoint(repo) + suffix, repo=repo, body=body)
        return {"repo": repo, "url": self._link(repo), "data": result}

    async def _ref(self, repo: str, ref: str | None) -> str:
        if ref:
            if any(c in ref for c in "\x00\r\n"):
                raise TeaError("invalid_input", "Invalid ref.")
            return ref
        metadata = (await self._get(repo))["data"]
        branch = metadata.get("default_branch")
        if not branch:
            raise TeaError("empty_repository", "Repository has no default branch.")
        return branch

    async def _git(self, repo: str, operation: str, *args):
        repository(repo)
        if not self._git_identity_ready:
            user = await self.api.request("GET", "/user")
            self.git.login = user["login"]
            self._git_identity_ready = True
        result = await asyncio.to_thread(getattr(self.git, operation), repo, *args)
        if isinstance(result, dict):
            result["url"] = self._link(repo)
        return result

    @exposed()
    async def get_capabilities(self) -> dict:
        """Report all 89 reference capabilities and the configured platform's advertised support."""
        from .parity import capability_report

        try:
            schema = await self.api.swagger()
        except TeaError as error:
            return {
                "platform": self.settings.base_url,
                "schema_verified": False,
                "error": error.as_dict(),
                "capabilities": capability_report(None, self.methods(), bool(self.git.git)),
            }
        return {
            "platform": self.settings.base_url,
            "schema_verified": True,
            "version": schema.get("info", {}).get("version"),
            "write_repositories": self.settings.write_repositories,
            "capabilities": capability_report(schema, self.methods(), bool(self.git.git)),
        }

    @exposed(path="/user")
    async def get_profile(self) -> dict:
        """Read the authenticated Gitea user's profile."""
        return await self.api.request("GET", "/user")

    @exposed(path="/user/repos")
    async def list_repositories(self, page: int = 1, limit: int = 50) -> dict:
        """List repositories accessible to the token, with explicit pagination."""
        return await self.api.page("/user/repos", page=page, limit=limit)

    @exposed(path="/user/repos")
    async def list_repositories_by_affiliation(
        self, affiliation: Literal["owned", "collaborator", "organization"], page: int = 1, limit: int = 50
    ) -> dict:
        """Filter a page of accessible repos by owner type; this is not GitHub's installation affiliation model."""
        user = await self.get_profile()
        result = await self.list_repositories(page, limit)
        orgs = await self.list_user_orgs(page=1, limit=100)
        org_names = {o.get("username") or o.get("name") or o.get("login") for o in orgs["items"]}

        def matches(repo):
            owner = repo.get("owner", {})
            owned = owner.get("login") == user["login"]
            organization = owner.get("type", "").lower() == "organization" or owner.get("login") in org_names
            return (
                owned
                if affiliation == "owned"
                else organization
                if affiliation == "organization"
                else not owned and not organization
            )

        result["items"] = [r for r in result["items"] if matches(r)]
        result["filter_scope"] = "current_page_owner_type"
        result["organization_memberships_truncated"] = orgs["next_page"] is not None
        return result

    @exposed(path="/repos/search")
    async def search_repositories(self, query: str, page: int = 1, limit: int = 50) -> dict:
        """Search repository names/descriptions using platform-native search."""
        return await self.api.page("/repos/search", page=page, limit=limit, params={"q": query})

    @exposed(path="/repos/{owner}/{repo}")
    async def get_repo(self, repo: str) -> dict:
        """Read repository metadata and permissions; repo is owner/name."""
        return await self._get(repo)

    @exposed(path="/user/orgs")
    async def list_user_orgs(self, page: int = 1, limit: int = 50) -> dict:
        """List the authenticated user's organizations."""
        return await self.api.page("/user/orgs", page=page, limit=limit)

    @exposed(path="/user/teams")
    async def list_user_teams(self, page: int = 1, limit: int = 50) -> dict:
        """Read team memberships; Gitea has no GitHub membership object equivalent."""
        return await self.api.page("/user/teams", page=page, limit=limit)

    @exposed(path="/repos/{owner}/{repo}/collaborators/{collaborator}/permission")
    async def get_repo_collaborator_permission(self, repo: str, username: str) -> dict:
        """Read a user's repository permission without changing access."""
        return await self._get(repo, "/collaborators/" + quote(username, safe="") + "/permission")

    @exposed()
    async def fetch(self, endpoint: str, params: dict | None = None) -> dict:
        """GET only from the configured platform's permitted repository/user/org endpoints."""
        path = self.api.safe_fetch_path(endpoint)
        if params and any(k.lower() in ("token", "access_token", "sudo") for k in params):
            raise TeaError(
                "invalid_input", "Authentication and impersonation query parameters are forbidden."
            )
        is_text = "/raw/" in path or path.endswith((".diff", ".patch"))
        data = await self.api.request("GET", path, params=params, text=is_text)
        if is_text:
            raw = data.encode()
            return {
                "endpoint": path,
                "data": raw[: self.settings.max_text_bytes].decode("utf-8", "ignore"),
                "truncated": len(raw) > self.settings.max_text_bytes,
            }
        return {"endpoint": path, "data": data}

    @exposed(path="/repos/{owner}/{repo}/contents")
    async def list_files(
        self, repo: str, path: str = "", ref: str | None = None, page: int = 1, limit: int = 50
    ) -> dict:
        """List one directory at a ref. Use returned paths for subsequent reads."""
        path = file_path(path, root=True)
        if page < 1 or not 1 <= limit <= 100:
            raise TeaError("invalid_input", "page must be positive and limit must be between 1 and 100.")
        suffix = "/contents" + ("/" + quote(path, safe="/") if path else "")
        ref = await self._ref(repo, ref)
        result = await self._get(repo, suffix, {"ref": ref})
        data = result.pop("data")
        if not isinstance(data, list):
            raise TeaError("invalid_input", "Path is not a directory; use read_file for files.")
        start = (page - 1) * limit
        return {
            "ref": ref,
            **result,
            "items": data[start : start + limit],
            "page": page,
            "limit": limit,
            "total": len(data),
            "next_page": page + 1 if start + limit < len(data) else None,
        }

    @exposed(path="/repos/{owner}/{repo}/contents/{filepath}")
    async def read_file(
        self, repo: str, path: str, ref: str | None = None, start_line: int = 1, end_line: int | None = None
    ) -> dict:
        """Read bounded UTF-8 text, optionally selecting a line range. Binary files are rejected."""
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise TeaError("invalid_input", "Invalid line range.")
        ref = await self._ref(repo, ref)
        response = await self._get(repo, "/contents/" + quote(file_path(path), safe="/"), {"ref": ref})
        data = response["data"]
        if not isinstance(data, dict) or data.get("type") != "file":
            raise TeaError("invalid_input", "Path is not a regular file.")
        if data.get("encoding") != "base64":
            raise TeaError("unsupported", "Platform did not return base64 file content.")
        try:
            raw = base64.b64decode("".join(data.get("content", "").split()), validate=True)
            if b"\x00" in raw:
                raise UnicodeDecodeError("utf-8", raw, 0, 1, "binary")
            text = raw.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            raise TeaError("binary_content", "File is not valid UTF-8 text.") from None
        lines = text.splitlines(keepends=True)
        selected = "".join(lines[start_line - 1 : end_line]).encode()
        bounded = selected[: self.settings.max_text_bytes].decode("utf-8", "ignore")
        return {
            "repo": repo,
            "path": path,
            "ref": ref,
            "sha": data.get("sha"),
            "last_commit_sha": data.get("last_commit_sha"),
            "url": data.get("html_url") or self._link(repo),
            "start_line": start_line,
            "total_lines": len(lines),
            "content": bounded,
            "truncated": len(selected) > self.settings.max_text_bytes,
        }

    @exposed(path="/repos/{owner}/{repo}/git/blobs/{sha}")
    async def fetch_blob(self, repo: str, sha: str) -> dict:
        """Read a Git blob by full SHA; content is returned in the platform's base64 representation."""
        try:
            result = await self._get(repo, "/git/blobs/" + object_sha(sha))
        except TeaError as error:
            if error.code != "not_found_or_hidden":
                raise
            await self.get_repo(repo)  # Never use cached objects to bypass revoked remote permissions.
            result = await self._git(repo, "read_object", sha, "blob")
        data = result["data"]
        if data.get("encoding") == "base64" and isinstance(data.get("content"), str):
            raw = base64.b64decode("".join(data["content"].split()), validate=True)
            data["content"] = base64.b64encode(raw[: self.settings.max_text_bytes]).decode()
            result["truncated"] = len(raw) > self.settings.max_text_bytes
        return result

    @exposed(path="/repos/{owner}/{repo}/commits")
    async def list_commits(self, repo: str, ref: str | None = None, page: int = 1, limit: int = 50) -> dict:
        """List commit history at a branch, tag, or SHA."""
        ref = await self._ref(repo, ref)
        return {"ref": ref, **await self._page(repo, "/commits", page, limit, {"sha": ref})}

    @exposed(path="/repos/{owner}/{repo}/git/commits/{sha}")
    async def fetch_commit(self, repo: str, sha: str) -> dict:
        """Read commit metadata by full SHA."""
        try:
            return await self._get(repo, "/git/commits/" + object_sha(sha))
        except TeaError as error:
            if error.code != "not_found_or_hidden":
                raise
            await self.get_repo(repo)
            return await self._git(repo, "read_object", sha, "commit")

    @exposed(path="/repos/{owner}/{repo}/git/commits/{sha}.{diffType}")
    async def get_diff(self, repo: str, sha: str) -> dict:
        """Read a bounded commit diff; sha is a full commit SHA."""
        try:
            data = (await self._get(repo, "/git/commits/" + object_sha(sha) + ".diff", text=True))["data"]
        except TeaError as error:
            if error.code != "not_found_or_hidden":
                raise
            await self.get_repo(repo)
            return await self._git(repo, "commit_diff", sha)
        return self._text_result(repo, data, sha=sha)

    def _text_result(self, repo: str, text: str, **metadata):
        raw = text.encode()
        return {
            "repo": repo,
            "url": self._link(repo),
            **metadata,
            "text": raw[: self.settings.max_text_bytes].decode("utf-8", "ignore"),
            "truncated": len(raw) > self.settings.max_text_bytes,
        }

    @exposed()
    async def compare_commits(self, repo: str, base: str, head: str, path: str | None = None) -> dict:
        """Compare two refs using bare Git; report resolved SHAs, file stats and bounded diff."""
        return await self._git(repo, "diff", base, head, path)

    @exposed()
    async def search(self, repo: str, query: str, ref: str | None = None, limit: int = 20) -> dict:
        """Literal case-sensitive code search in one authorized repository, using bare Git."""
        return await self._git(repo, "search_code", await self._ref(repo, ref), query, limit)

    @exposed()
    async def search_commits(
        self, repo: str, query: str = "", ref: str | None = None, limit: int = 20
    ) -> dict:
        """Search commit messages in one repository; empty query lists recent commits."""
        return await self._git(repo, "search_commits", await self._ref(repo, ref), query, limit)

    @exposed(path="/repos/{owner}/{repo}/branches")
    async def search_branches(self, repo: str, query: str = "", page: int = 1, limit: int = 50) -> dict:
        """List a page of branches and filter that page by literal name substring."""
        result = await self._page(repo, "/branches", page, limit)
        result["items"] = [b for b in result["items"] if query in b["name"]]
        result["filter_scope"] = "current_page"
        return result

    @exposed(write=True, path="/repos/{owner}/{repo}/branches", method="post")
    async def create_branch(self, repo: str, branch: str, from_ref: str) -> dict:
        """Create a branch from one exact existing ref; platform branch protections apply."""
        await asyncio.to_thread(self.git._branch, branch)
        schema = await self.api.swagger()
        props = self.api.body_properties(schema, schema["paths"]["/repos/{owner}/{repo}/branches"]["post"])
        key = "old_ref_name" if "old_ref_name" in props else "old_branch_name"
        if key == "old_branch_name" and (len(from_ref) in (40, 64) or from_ref.startswith("refs/")):
            return await self._git(repo, "create_branch", branch, from_ref)
        return await self._write(repo, "POST", "/branches", {"new_branch_name": branch, key: from_ref})

    @exposed(write=True, path="/repos/{owner}/{repo}/contents/{filepath}", method="post")
    async def create_file(self, repo: str, path: str, branch: str, content: str, message: str) -> dict:
        """Create one UTF-8 file on an existing branch; an existing path causes a conflict."""
        return await self._write(
            repo,
            "POST",
            "/contents/" + quote(file_path(path), safe="/"),
            {"branch": branch, "content": base64.b64encode(content.encode()).decode(), "message": message},
        )

    @exposed(write=True, path="/repos/{owner}/{repo}/contents/{filepath}", method="put")
    async def update_file(
        self, repo: str, path: str, branch: str, content: str, message: str, expected_sha: str
    ) -> dict:
        """Update one UTF-8 file with the current blob SHA as an optimistic concurrency check."""
        return await self._write(
            repo,
            "PUT",
            "/contents/" + quote(file_path(path), safe="/"),
            {
                "branch": branch,
                "content": base64.b64encode(content.encode()).decode(),
                "message": message,
                "sha": object_sha(expected_sha),
            },
        )

    @exposed(write=True, destructive=True, path="/repos/{owner}/{repo}/contents/{filepath}", method="delete")
    async def delete_file(self, repo: str, path: str, branch: str, message: str, expected_sha: str) -> dict:
        """Delete one file, rejecting a stale blob SHA."""
        return await self._write(
            repo,
            "DELETE",
            "/contents/" + quote(file_path(path), safe="/"),
            {"branch": branch, "message": message, "sha": object_sha(expected_sha)},
        )

    @exposed(write=True)
    async def create_blob(
        self, repo: str, content: str, encoding: Literal["utf-8", "base64"] = "utf-8"
    ) -> dict:
        """Create a local Git blob. It becomes remote-visible after a commit is published with update_ref."""
        return await self._git(repo, "create_blob", content, encoding)

    @exposed(write=True)
    async def create_tree(self, repo: str, entries: list[dict], base_tree: str | None = None) -> dict:
        """Build a local tree. Entries contain path and content/SHA or delete=true; mode defaults to 100644."""
        return await self._git(repo, "create_tree", entries, base_tree)

    @exposed(write=True)
    async def create_commit(
        self, repo: str, tree: str, parents: list[str], message: str, author_name: str, author_email: str
    ) -> dict:
        """Create a local commit from a tree and parents; update_ref publishes it."""
        return await self._git(repo, "create_commit", tree, parents, message, author_name, author_email)

    @exposed(write=True, destructive=True)
    async def update_ref(self, repo: str, branch: str, sha: str, expected_sha: str) -> dict:
        """Publish a fast-forward commit with an atomic expected-SHA lease. Rewrites are refused."""
        return await self._git(repo, "update_ref", branch, sha, expected_sha)

    @exposed(write=True)
    async def commit_files(
        self,
        repo: str,
        branch: str,
        expected_sha: str,
        changes: list[dict],
        message: str,
        author_name: str,
        author_email: str,
    ) -> dict:
        """Atomically commit multiple file changes and publish using a branch SHA lease."""
        return await self._git(
            repo, "commit_files", branch, expected_sha, changes, message, author_name, author_email
        )

    @exposed(path="/repos/{owner}/{repo}/issues")
    async def search_issues(
        self,
        repo: str,
        query: str = "",
        state: Literal["open", "closed", "all"] = "open",
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """Search issues in one repository using Gitea's native query, excluding PRs."""
        return await self._page(repo, "/issues", page, limit, {"q": query, "state": state, "type": "issues"})

    @exposed(path="/repos/issues/search")
    async def list_recent_issues(self, page: int = 1, limit: int = 50) -> dict:
        """List recent issues across repositories accessible to this user."""
        return await self.api.page(
            "/repos/issues/search", page=page, limit=limit, params={"type": "issues", "sort": "updated"}
        )

    @exposed(path="/repos/{owner}/{repo}/issues/{index}")
    async def fetch_issue(self, repo: str, index: int) -> dict:
        """Read one issue by its repository-local number."""
        return await self._get(repo, f"/issues/{self._id(index)}")

    @staticmethod
    def _id(value: int) -> int:
        if value < 1:
            raise TeaError("invalid_input", "Object IDs must be positive integers.")
        return value

    @exposed(write=True, path="/repos/{owner}/{repo}/issues", method="post")
    async def create_issue(
        self,
        repo: str,
        title: str,
        body: str = "",
        assignees: list[str] | None = None,
        labels: list[int] | None = None,
    ) -> dict:
        """Create an issue with optional assignees and existing numeric label IDs."""
        return await self._write(
            repo,
            "POST",
            "/issues",
            {"title": title, "body": body, "assignees": assignees or [], "labels": labels or []},
        )

    @exposed(write=True, path="/repos/{owner}/{repo}/issues/{index}", method="patch")
    async def update_issue(self, repo: str, index: int, fields: dict) -> dict:
        """Edit issue title/body/state/assignees/milestone. Use label tools to edit labels."""
        await self.api.require("/repos/{owner}/{repo}/issues/{index}", "patch", fields=list(fields))
        return await self._write(repo, "PATCH", f"/issues/{self._id(index)}", fields)

    @exposed(path="/repos/{owner}/{repo}/issues/{index}/comments")
    async def fetch_issue_comments(self, repo: str, index: int, page: int = 1, limit: int = 50) -> dict:
        """Read one page of issue or PR conversation comments."""
        return await self._page(repo, f"/issues/{self._id(index)}/comments", page, limit)

    @exposed(write=True, path="/repos/{owner}/{repo}/issues/{index}/comments", method="post")
    async def add_comment_to_issue(self, repo: str, index: int, body: str) -> dict:
        """Post a top-level issue/PR conversation comment."""
        return await self._write(repo, "POST", f"/issues/{self._id(index)}/comments", {"body": body})

    @exposed(write=True, path="/repos/{owner}/{repo}/issues/comments/{id}", method="patch")
    async def update_issue_comment(self, repo: str, comment_id: int, body: str) -> dict:
        """Edit a top-level issue/PR conversation comment, subject to platform ownership rules."""
        return await self._write(repo, "PATCH", f"/issues/comments/{self._id(comment_id)}", {"body": body})

    @exposed(path="/repos/{owner}/{repo}/labels")
    async def list_labels(self, repo: str, page: int = 1, limit: int = 50) -> dict:
        """List repository labels and their numeric IDs."""
        return await self._page(repo, "/labels", page, limit)

    @exposed(write=True, path="/repos/{owner}/{repo}/issues/{index}/labels", method="post")
    async def add_issue_labels(self, repo: str, index: int, labels: list[int]) -> dict:
        """Add existing numeric label IDs to an issue or PR."""
        return await self._write(repo, "POST", f"/issues/{self._id(index)}/labels", {"labels": labels})

    @exposed(
        write=True, destructive=True, path="/repos/{owner}/{repo}/issues/{index}/labels/{id}", method="delete"
    )
    async def remove_issue_label(self, repo: str, index: int, label_id: int) -> dict:
        """Remove one existing label from an issue or PR."""
        return await self._write(repo, "DELETE", f"/issues/{self._id(index)}/labels/{self._id(label_id)}")

    @exposed(write=True, path="/repos/{owner}/{repo}/issues/{index}", method="patch")
    async def add_issue_assignees(self, repo: str, index: int, usernames: list[str]) -> dict:
        """Read the current assignees, merge usernames, then PATCH the set; no atomic add API exists."""
        issue = (await self.fetch_issue(repo, index))["data"]
        names = {u["login"] for u in issue.get("assignees", [])} | set(usernames)
        return await self.update_issue(repo, index, {"assignees": sorted(names)})

    @exposed(write=True, destructive=True, path="/repos/{owner}/{repo}/issues/{index}", method="patch")
    async def remove_issue_assignees(self, repo: str, index: int, usernames: list[str]) -> dict:
        """Read the current assignees, remove usernames, then PATCH the set; concurrent edits can race."""
        issue = (await self.fetch_issue(repo, index))["data"]
        names = {u["login"] for u in issue.get("assignees", [])} - set(usernames)
        return await self.update_issue(repo, index, {"assignees": sorted(names)})

    @exposed(write=True, path="/repos/{owner}/{repo}/issues/{index}", method="patch", fields=("is_locked",))
    async def lock_issue_conversation(self, repo: str, index: int) -> dict:
        """Lock a conversation only if the platform advertises an is_locked mutation field."""
        await self.api.require("/repos/{owner}/{repo}/issues/{index}", "patch", fields=["is_locked"])
        return await self._write(repo, "PATCH", f"/issues/{self._id(index)}", {"is_locked": True})

    @exposed(write=True, path="/repos/{owner}/{repo}/issues/{index}", method="patch", fields=("is_locked",))
    async def unlock_issue_conversation(self, repo: str, index: int) -> dict:
        """Unlock a conversation only if is_locked is supported by the instance API."""
        await self.api.require("/repos/{owner}/{repo}/issues/{index}", "patch", fields=["is_locked"])
        return await self._write(repo, "PATCH", f"/issues/{self._id(index)}", {"is_locked": False})

    @exposed(path="/repos/{owner}/{repo}/issues/{index}/reactions")
    async def get_pr_reactions(self, repo: str, index: int, page: int = 1, limit: int = 50) -> dict:
        """Read issue/PR reactions."""
        return await self._page(repo, f"/issues/{self._id(index)}/reactions", page, limit)

    @exposed(write=True, path="/repos/{owner}/{repo}/issues/{index}/reactions", method="post")
    async def add_reaction_to_pr(self, repo: str, index: int, content: str) -> dict:
        """Add a platform-supported reaction to an issue/PR."""
        return await self._write(repo, "POST", f"/issues/{self._id(index)}/reactions", {"content": content})

    @exposed(
        write=True, destructive=True, path="/repos/{owner}/{repo}/issues/{index}/reactions", method="delete"
    )
    async def remove_reaction_from_pr(self, repo: str, index: int, content: str) -> dict:
        """Remove the authenticated user's reaction from an issue/PR."""
        return await self._write(repo, "DELETE", f"/issues/{self._id(index)}/reactions", {"content": content})

    @exposed(path="/repos/{owner}/{repo}/issues/comments/{id}/reactions")
    async def get_issue_comment_reactions(
        self, repo: str, comment_id: int, page: int = 1, limit: int = 50
    ) -> dict:
        """Read reactions on a platform comment ID."""
        return await self._page(repo, f"/issues/comments/{self._id(comment_id)}/reactions", page, limit)

    @exposed(write=True, path="/repos/{owner}/{repo}/issues/comments/{id}/reactions", method="post")
    async def add_reaction_to_issue_comment(self, repo: str, comment_id: int, content: str) -> dict:
        """Add a reaction to a platform comment, including review comments accepted by this instance."""
        return await self._write(
            repo, "POST", f"/issues/comments/{self._id(comment_id)}/reactions", {"content": content}
        )

    @exposed(
        write=True,
        destructive=True,
        path="/repos/{owner}/{repo}/issues/comments/{id}/reactions",
        method="delete",
    )
    async def remove_reaction_from_issue_comment(self, repo: str, comment_id: int, content: str) -> dict:
        """Remove the authenticated user's reaction from a platform comment."""
        return await self._write(
            repo, "DELETE", f"/issues/comments/{self._id(comment_id)}/reactions", {"content": content}
        )

    @exposed(path="/repos/{owner}/{repo}/pulls")
    async def search_prs(
        self,
        repo: str,
        query: str = "",
        state: Literal["open", "closed", "all"] = "open",
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """Search PRs through the issues search API with type=pulls; query syntax is Gitea-native."""
        return await self._page(repo, "/issues", page, limit, {"q": query, "state": state, "type": "pulls"})

    @exposed(path="/repos/{owner}/{repo}/issues")
    async def get_users_recent_prs_in_repo(self, repo: str, page: int = 1, limit: int = 50) -> dict:
        """List a page of the authenticated user's recent PRs."""
        return await self._page(
            repo,
            "/issues",
            page,
            limit,
            {"type": "pulls", "created": "true", "state": "all", "sort": "updated"},
        )

    @exposed(path="/repos/{owner}/{repo}/pulls/{index}")
    async def fetch_pr(self, repo: str, index: int) -> dict:
        """Read PR metadata. Use get_pr_diff/list_pr_changed_filenames for actual changes."""
        return await self._get(repo, f"/pulls/{self._id(index)}")

    @exposed(write=True, path="/repos/{owner}/{repo}/pulls", method="post")
    async def create_pull_request(self, repo: str, title: str, head: str, base: str, body: str = "") -> dict:
        """Open a pull request between named branches."""
        return await self._write(
            repo, "POST", "/pulls", {"title": title, "head": head, "base": base, "body": body}
        )

    @exposed(write=True, path="/repos/{owner}/{repo}/pulls/{index}", method="patch")
    async def update_pull_request(self, repo: str, index: int, fields: dict) -> dict:
        """Edit platform-supported PR metadata, base branch or open/closed state."""
        await self.api.require("/repos/{owner}/{repo}/pulls/{index}", "patch", fields=list(fields))
        return await self._write(repo, "PATCH", f"/pulls/{self._id(index)}", fields)

    @exposed(path="/repos/{owner}/{repo}/pulls/{index}.{diffType}")
    async def get_pr_diff(self, repo: str, index: int, format: Literal["diff", "patch"] = "diff") -> dict:
        """Read a bounded PR diff. Return a truncation indicator instead of silently dropping changes."""
        text = (await self._get(repo, f"/pulls/{self._id(index)}.{format}", text=True))["data"]
        return self._text_result(repo, text, index=index)

    @exposed(path="/repos/{owner}/{repo}/pulls/{index}/files")
    async def list_pr_changed_filenames(self, repo: str, index: int, page: int = 1, limit: int = 50) -> dict:
        """Read a page of changed PR files, including paths and platform patch/stat fields."""
        return await self._page(repo, f"/pulls/{self._id(index)}/files", page, limit)

    @exposed()
    async def fetch_pr_file_patch(self, repo: str, index: int, path: str) -> dict:
        """Compare the PR merge base and head for one changed path; unrelated base changes yield patch=null."""
        file_path(path)
        pr = (await self.fetch_pr(repo, index))["data"]
        base_repo = pr["base"].get("repo", {}).get("full_name", repo)
        head_repo = pr["head"].get("repo", {}).get("full_name", repo)
        if base_repo != repo:
            raise TeaError("invalid_response", "PR base does not belong to the requested repository.")
        if head_repo != repo:
            await self._git(repo, "import_commit", head_repo, pr["head"]["sha"])
        result = await self._git(repo, "diff", pr["base"]["sha"], pr["head"]["sha"], path, True)
        patch = result.pop("diff")
        return {**result, "patch": patch if result["files"] else None}

    @exposed(path="/repos/{owner}/{repo}/issues/{index}/timeline")
    async def fetch_pr_comments(self, repo: str, index: int, page: int = 1, limit: int = 50) -> dict:
        """Read a page of the native PR timeline, including conversation and review events."""
        return await self._page(repo, f"/issues/{self._id(index)}/timeline", page, limit)

    @exposed(path="/repos/{owner}/{repo}/pulls/{index}/reviews")
    async def list_pull_request_reviews(self, repo: str, index: int, page: int = 1, limit: int = 50) -> dict:
        """List a page of submitted PR reviews."""
        return await self._page(repo, f"/pulls/{self._id(index)}/reviews", page, limit)

    @exposed(path="/repos/{owner}/{repo}/pulls/{index}/reviews/{id}/comments")
    async def list_review_comments(
        self, repo: str, index: int, review_id: int, page: int = 1, limit: int = 50
    ) -> dict:
        """List a review's inline comments; this instance exposes no resolved-thread lifecycle API."""
        return await self._page(
            repo, f"/pulls/{self._id(index)}/reviews/{self._id(review_id)}/comments", page, limit
        )

    @exposed(write=True, path="/repos/{owner}/{repo}/pulls/{index}/reviews", method="post")
    async def add_review_to_pr(
        self,
        repo: str,
        index: int,
        event: Literal["COMMENT", "APPROVED", "REQUEST_CHANGES", "PENDING"],
        body: str = "",
        commit_sha: str | None = None,
        comments: list[dict] | None = None,
    ) -> dict:
        """Submit a Gitea review. Inline entries use path, new_position or old_position, and body."""
        payload = {"event": event, "body": body, "comments": comments or []}
        if commit_sha:
            payload["commit_id"] = object_sha(commit_sha)
        return await self._write(repo, "POST", f"/pulls/{self._id(index)}/reviews", payload)

    @exposed(write=True, path="/repos/{owner}/{repo}/pulls/{index}/reviews/{id}/dismissals", method="post")
    async def dismiss_pull_request_review(self, repo: str, index: int, review_id: int, message: str) -> dict:
        """Dismiss a submitted review, subject to platform permissions."""
        return await self._write(
            repo,
            "POST",
            f"/pulls/{self._id(index)}/reviews/{self._id(review_id)}/dismissals",
            {"message": message},
        )

    @exposed(write=True, path="/repos/{owner}/{repo}/pulls/{index}/requested_reviewers", method="post")
    async def request_pull_request_reviewers(
        self, repo: str, index: int, reviewers: list[str], team_reviewers: list[str] | None = None
    ) -> dict:
        """Request individual or team reviewers using Gitea's native identifiers."""
        return await self._write(
            repo,
            "POST",
            f"/pulls/{self._id(index)}/requested_reviewers",
            {"reviewers": reviewers, "team_reviewers": team_reviewers or []},
        )

    @exposed(
        write=True,
        destructive=True,
        path="/repos/{owner}/{repo}/pulls/{index}/requested_reviewers",
        method="delete",
    )
    async def remove_pull_request_reviewers(
        self, repo: str, index: int, reviewers: list[str], team_reviewers: list[str] | None = None
    ) -> dict:
        """Remove reviewer requests; this does not delete submitted reviews."""
        return await self._write(
            repo,
            "DELETE",
            f"/pulls/{self._id(index)}/requested_reviewers",
            {"reviewers": reviewers, "team_reviewers": team_reviewers or []},
        )

    @exposed(
        write=True,
        path="/repos/{owner}/{repo}/pulls/{index}/merge",
        method="post",
        fields=("head_commit_id",),
    )
    async def merge_pull_request(
        self,
        repo: str,
        index: int,
        expected_head_sha: str,
        strategy: Literal["merge", "squash", "rebase", "rebase-merge"] = "merge",
        delete_branch: bool = False,
    ) -> dict:
        """Merge the expected PR head, preserving platform checks and branch protections; never force merge."""
        await self.api.require("/repos/{owner}/{repo}/pulls/{index}/merge", "post", fields=["head_commit_id"])
        return await self._write(
            repo,
            "POST",
            f"/pulls/{self._id(index)}/merge",
            {
                "Do": strategy,
                "head_commit_id": object_sha(expected_head_sha),
                "delete_branch_after_merge": delete_branch,
                "force_merge": False,
            },
        )

    @exposed(
        write=True,
        path="/repos/{owner}/{repo}/pulls/{index}/merge",
        method="post",
        fields=("merge_when_checks_succeed", "head_commit_id"),
    )
    async def enable_auto_merge(
        self,
        repo: str,
        index: int,
        expected_head_sha: str,
        strategy: Literal["merge", "squash", "rebase", "rebase-merge"] = "merge",
    ) -> dict:
        """Request native merge-when-checks-succeed, if this instance supports it."""
        await self.api.require(
            "/repos/{owner}/{repo}/pulls/{index}/merge",
            "post",
            fields=["merge_when_checks_succeed", "head_commit_id"],
        )
        return await self._write(
            repo,
            "POST",
            f"/pulls/{self._id(index)}/merge",
            {
                "Do": strategy,
                "head_commit_id": object_sha(expected_head_sha),
                "merge_when_checks_succeed": True,
                "force_merge": False,
            },
        )

    @exposed(path="/repos/{owner}/{repo}/commits/{ref}/status")
    async def get_commit_combined_status(self, repo: str, ref: str) -> dict:
        """Read combined commit status and checks; workflow execution is a separate capability."""
        return await self._get(repo, "/commits/" + quote(ref, safe="") + "/status")
