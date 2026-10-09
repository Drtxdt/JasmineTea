import base64
import hashlib
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from filelock import FileLock, Timeout

from .config import Settings, file_path, repository
from .errors import TeaError


def object_sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        raise TeaError("invalid_input", "Expected a full Git object SHA.")
    return value.lower()


class GitBackend:
    """Bare object storage; never checks out or executes repository code."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.login = "token"
        self.git = shutil.which("git")

    def _environment(self) -> dict[str, str]:
        token = self.settings.token()
        authorization = base64.b64encode(f"{self.login}:{token}".encode()).decode()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_COUNT": "4",
                "GIT_CONFIG_KEY_0": f"http.{self.settings.base_url}/.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: Basic {authorization}",
                "GIT_CONFIG_KEY_1": "credential.helper",
                "GIT_CONFIG_VALUE_1": "",
                "GIT_CONFIG_KEY_2": "core.hooksPath",
                "GIT_CONFIG_VALUE_2": os.devnull,
                "GIT_CONFIG_KEY_3": "http.followRedirects",
                "GIT_CONFIG_VALUE_3": "false",
            }
        )
        return env

    def _run(
        self,
        directory: Path | None,
        args: list[str],
        *,
        data: bytes | None = None,
        env: dict | None = None,
        check: bool = True,
        allowed_codes: tuple[int, ...] = (),
        writing: bool = False,
    ) -> bytes:
        if not self.git:
            raise TeaError("git_unavailable", "Install Git to use this capability.")
        command = [self.git] + (["--git-dir", str(directory)] if directory else []) + args
        try:
            result = subprocess.run(
                command,
                input=data,
                capture_output=True,
                env=env or self._environment(),
                timeout=self.settings.timeout_seconds * 3,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise TeaError(
                "git_timeout", "Git timed out; inspect the remote branch before retrying.", uncertain=writing
            ) from None
        except OSError:
            raise TeaError("git_unavailable", "Git could not be started.") from None
        if result.returncode and (check or (allowed_codes and result.returncode not in allowed_codes)):
            # Git output may contain credentials through a remote/proxy; never return stderr.
            code = "conflict" if writing else "git_error"
            raise TeaError(
                code,
                "Git operation failed. Check access, ref existence, and the expected SHA.",
                uncertain=writing,
            )
        if len(result.stdout) > self.settings.max_response_bytes:
            raise TeaError(
                "response_too_large", "Git output exceeds the configured byte limit.", uncertain=writing
            )
        return result.stdout

    def _remote_url(self, repo: str) -> str:
        owner, name = repository(repo).split("/")
        return self.settings.base_url + "/" + quote(owner, safe="") + "/" + quote(name, safe="") + ".git"

    @contextmanager
    def _cache(self, repo: str, *, refresh: bool = False):
        repo = repository(repo)
        identity = self.settings.base_url + "|" + repo + "|" + self.settings.token()
        key = hashlib.sha256(identity.encode()).hexdigest()
        root = self.settings.cache_dir.resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise TeaError(
                "cache_unavailable", "Repository cache is not writable; configure cache_dir."
            ) from None
        directory = root / (key + ".git")
        try:
            with FileLock(str(root / (key + ".lock")), timeout=self.settings.timeout_seconds):
                if not (directory / "HEAD").exists():
                    self._run(None, ["init", "--bare", str(directory)])
                    self._run(directory, ["remote", "add", "origin", self._remote_url(repo)])
                    refresh = True
                if refresh:
                    self._run(
                        directory,
                        [
                            "fetch",
                            "--no-tags",
                            "origin",
                            "+refs/heads/*:refs/remotes/origin/*",
                            "+refs/tags/*:refs/tags/*",
                        ],
                    )
                yield directory
        except Timeout:
            raise TeaError("busy", "Repository cache is busy; try again later.") from None

    def _resolve(self, directory: Path, ref: str) -> str:
        if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", ref):
            return (
                self._run(directory, ["rev-parse", "--verify", "--end-of-options", ref + "^{commit}"])
                .decode()
                .strip()
            )
        self._branch(ref.removeprefix("refs/heads/").removeprefix("refs/tags/"))
        candidates = (
            [ref]
            if ref.startswith("refs/tags/")
            else ["refs/remotes/origin/" + ref.removeprefix("refs/heads/"), "refs/tags/" + ref]
        )
        for candidate in candidates:
            value = (
                self._run(
                    directory,
                    ["rev-parse", "--verify", "--end-of-options", candidate + "^{commit}"],
                    check=False,
                )
                .decode()
                .strip()
            )
            if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
                return value
        raise TeaError("not_found_or_hidden", "Git ref could not be resolved.")

    def _branch(self, branch: str) -> None:
        if branch.startswith("-") or branch == "HEAD" or any(c in branch for c in "\x00\r\n"):
            raise TeaError("invalid_input", "Invalid branch name.")
        self._run(None, ["check-ref-format", "refs/heads/" + branch])

    def resolve(self, repo: str, ref: str) -> str:
        with self._cache(repo, refresh=True) as directory:
            return self._resolve(directory, ref)

    def import_commit(self, repo: str, source_repo: str, sha: str) -> dict:
        """Fetch a fork PR's exact head from the same configured platform."""
        object_sha(sha)
        with self._cache(repo) as directory:
            self._run(directory, ["fetch", "--no-tags", self._remote_url(source_repo), sha])
            return {"repo": repo, "source_repo": source_repo, "sha": self._resolve(directory, sha)}

    def read_object(self, repo: str, sha: str, kind: str) -> dict:
        sha = object_sha(sha)
        with self._cache(repo) as directory:
            if self._run(directory, ["cat-file", "-t", sha]).decode().strip() != kind:
                raise TeaError("invalid_input", "Git object type does not match the requested operation.")
            data = self._run(directory, ["cat-file", kind, sha])
            return {
                "repo": repo,
                "storage": "local_git_cache",
                "data": {
                    "sha": sha,
                    "type": kind,
                    "encoding": "base64",
                    "content": base64.b64encode(data).decode(),
                    "size": len(data),
                },
            }

    def commit_diff(self, repo: str, sha: str) -> dict:
        sha = object_sha(sha)
        with self._cache(repo) as directory:
            self._resolve(directory, sha)
            raw = self._run(directory, ["show", "--format=", "--no-ext-diff", "--no-textconv", sha, "--"])
            return {
                "repo": repo,
                "sha": sha,
                "text": raw[: self.settings.max_text_bytes].decode("utf-8", "replace"),
                "truncated": len(raw) > self.settings.max_text_bytes,
                "storage": "local_git_cache",
            }

    def diff(
        self, repo: str, base: str, head: str, path: str | None = None, merge_base: bool = False
    ) -> dict:
        if path is not None:
            file_path(path)
        with self._cache(repo, refresh=True) as directory:
            base_sha, head_sha = self._resolve(directory, base), self._resolve(directory, head)
            comparison_base = (
                self._run(directory, ["merge-base", base_sha, head_sha]).decode().strip()
                if merge_base
                else base_sha
            )
            stats = self._run(directory, ["diff", "-M", "--numstat", "-z", comparison_base, head_sha])
            entries, index, fields = [], 0, stats.split(b"\0")
            while index < len(fields) and fields[index]:
                added, removed, name = fields[index].split(b"\t", 2)
                index += 1
                old_name = None
                if not name:
                    old_name, name = fields[index : index + 2]
                    index += 2
                entry = {
                    "path": name.decode("utf-8", "replace"),
                    "previous_path": old_name.decode("utf-8", "replace") if old_name else None,
                    "additions": int(added) if added != b"-" else None,
                    "deletions": int(removed) if removed != b"-" else None,
                }
                if path is None or path in (entry["path"], entry["previous_path"]):
                    entries.append(entry)
            paths = []
            if path is not None:
                paths = ["--", path]
                for entry in entries:
                    if entry["previous_path"] and entry["previous_path"] != path:
                        paths.append(entry["previous_path"])
            raw = self._run(
                directory, ["diff", "-M", "--no-ext-diff", "--no-textconv", comparison_base, head_sha, *paths]
            )
            maximum = self.settings.max_text_bytes
            return {
                "repo": repo,
                "base_sha": base_sha,
                "comparison_base_sha": comparison_base,
                "head_sha": head_sha,
                "files": entries,
                "diff": raw[:maximum].decode("utf-8", "replace"),
                "truncated": len(raw) > maximum,
            }

    def search_code(self, repo: str, ref: str, query: str, limit: int = 20) -> dict:
        if not query or "\x00" in query or len(query) > 1024 or not 1 <= limit <= 100:
            raise TeaError("invalid_input", "Use a nonempty query and a limit between 1 and 100.")
        with self._cache(repo, refresh=True) as directory:
            sha = self._resolve(directory, ref)
            raw = self._run(
                directory,
                ["grep", "-n", "-I", "-F", "--null", f"--max-count={limit}", "-e", query, sha, "--"],
                check=False,
                allowed_codes=(0, 1),
            )
            hits = []
            for record in raw.splitlines():
                parts = record.split(b"\0", 2)
                if len(parts) == 3:
                    name, line, content = parts
                    hits.append(
                        {
                            "path": name.decode("utf-8", "replace").removeprefix(sha + ":"),
                            "line": int(line),
                            "excerpt": content[:1000].decode("utf-8", "replace"),
                        }
                    )
            return {"repo": repo, "sha": sha, "items": hits[:limit], "truncated": len(hits) > limit}

    def search_commits(self, repo: str, ref: str, query: str = "", limit: int = 20) -> dict:
        if not 1 <= limit <= 100:
            raise TeaError("invalid_input", "limit must be between 1 and 100.")
        with self._cache(repo, refresh=True) as directory:
            sha = self._resolve(directory, ref)
            args = ["log", sha, f"--max-count={limit}", "--format=%H%x00%an%x00%aI%x00%s%x00"]
            if query:
                args.extend(["--fixed-strings", "--grep=" + query])
            fields = self._run(directory, args).decode("utf-8", "replace").split("\0")
            items = [
                {
                    "sha": fields[i].lstrip("\n"),
                    "author": fields[i + 1],
                    "date": fields[i + 2],
                    "subject": fields[i + 3],
                }
                for i in range(0, len(fields) - 1, 4)
                if i + 3 < len(fields)
            ]
            return {"repo": repo, "sha": sha, "items": items}

    def _blob(self, directory: Path, content: str, encoding: str = "utf-8") -> str:
        if encoding not in ("utf-8", "base64"):
            raise TeaError("invalid_input", "encoding must be utf-8 or base64.")
        try:
            data = (
                content.encode("utf-8") if encoding == "utf-8" else base64.b64decode(content, validate=True)
            )
        except ValueError:
            raise TeaError("invalid_input", "Invalid base64 content.") from None
        if len(data) > self.settings.max_response_bytes:
            raise TeaError("response_too_large", "Object exceeds the byte limit.")
        return self._run(directory, ["hash-object", "-w", "--stdin"], data=data).decode().strip()

    def create_blob(self, repo: str, content: str, encoding: str = "utf-8") -> dict:
        self.settings.authorize_write(repo)
        with self._cache(repo) as directory:
            return {
                "repo": repo,
                "sha": self._blob(directory, content, encoding),
                "storage": "local_git_cache",
            }

    def _tree(self, directory: Path, entries: list[dict], base_tree: str | None = None) -> str:
        if not entries:
            raise TeaError("invalid_input", "Provide at least one tree change.")
        index = directory / ("index-" + uuid4().hex)
        env = {**self._environment(), "GIT_INDEX_FILE": str(index)}
        try:
            self._run(
                directory,
                ["read-tree", object_sha(base_tree)] if base_tree else ["read-tree", "--empty"],
                env=env,
            )
            seen = set()
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) - {
                    "path",
                    "sha",
                    "content",
                    "encoding",
                    "mode",
                    "type",
                    "delete",
                }:
                    raise TeaError("invalid_input", "Invalid tree entry fields.")
                if "delete" in entry and not isinstance(entry["delete"], bool):
                    raise TeaError("invalid_input", "delete must be a boolean.")
                if entry.get("delete") and (entry.get("sha") is not None or entry.get("content") is not None):
                    raise TeaError("invalid_input", "Deletion entries cannot also contain content or a SHA.")
                path = file_path(entry["path"])
                if path in seen:
                    raise TeaError("invalid_input", "Duplicate paths in a tree change.")
                seen.add(path)
                if entry.get("delete"):
                    line = "0 " + "0" * 40 + "\t" + path + "\0"
                else:
                    mode = entry.get("mode", "100644")
                    if mode not in ("100644", "100755", "120000", "160000"):
                        raise TeaError("invalid_input", "Unsupported Git mode.")
                    if not entry.get("sha") and not isinstance(entry.get("content"), str):
                        raise TeaError("invalid_input", "Tree content must be a string.")
                    sha = (
                        object_sha(entry["sha"])
                        if entry.get("sha")
                        else self._blob(directory, entry["content"], entry.get("encoding", "utf-8"))
                    )
                    kind = self._run(directory, ["cat-file", "-t", sha]).decode().strip()
                    if kind != ("commit" if mode == "160000" else "blob"):
                        raise TeaError("invalid_input", "Tree mode does not match object type.")
                    if entry.get("type", kind) != kind:
                        raise TeaError("invalid_input", "Tree entry type does not match the object.")
                    line = f"{mode} {sha}\t{path}\0"
                self._run(directory, ["update-index", "-z", "--index-info"], data=line.encode(), env=env)
            return self._run(directory, ["write-tree"], env=env).decode().strip()
        except (KeyError, TypeError):
            raise TeaError(
                "invalid_input", "Each tree entry needs a path and content, SHA, or delete=true."
            ) from None
        finally:
            index.unlink(missing_ok=True)
            Path(str(index) + ".lock").unlink(missing_ok=True)

    def create_tree(self, repo: str, entries: list[dict], base_tree: str | None = None) -> dict:
        self.settings.authorize_write(repo)
        with self._cache(repo) as directory:
            return {
                "repo": repo,
                "sha": self._tree(directory, entries, base_tree),
                "storage": "local_git_cache",
            }

    def _commit(
        self,
        directory: Path,
        tree: str,
        parents: list[str],
        message: str,
        author_name: str,
        author_email: str,
    ) -> str:
        if (
            not message
            or not author_name
            or not author_email
            or any(c in author_name + author_email for c in "\r\n\x00<>")
        ):
            raise TeaError("invalid_input", "Provide a message and valid author name/email.")
        if "\x00" in message:
            raise TeaError("invalid_input", "Commit message cannot contain NUL.")
        tree = object_sha(tree)
        if self._run(directory, ["cat-file", "-t", tree]).strip() != b"tree":
            raise TeaError("invalid_input", "Expected a tree object.")
        args = ["commit-tree", tree]
        for parent in parents:
            args.extend(["-p", self._resolve(directory, object_sha(parent))])
        env = {
            **self._environment(),
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
        return self._run(directory, args, data=message.encode(), env=env).decode().strip()

    def create_commit(
        self, repo: str, tree: str, parents: list[str], message: str, author_name: str, author_email: str
    ) -> dict:
        self.settings.authorize_write(repo)
        with self._cache(repo) as directory:
            sha = self._commit(directory, tree, parents, message, author_name, author_email)
            return {"repo": repo, "sha": sha, "storage": "local_git_cache", "published": False}

    def _push(self, directory: Path, branch: str, sha: str, expected_sha: str) -> dict:
        self._branch(branch)
        sha, expected_sha = object_sha(sha), object_sha(expected_sha)
        remote = self._run(directory, ["ls-remote", "origin", "refs/heads/" + branch]).decode().strip()
        current = remote.split()[0] if remote else ""
        if current != expected_sha:
            raise TeaError("conflict", "Remote branch changed; read its current SHA before retrying.")
        # Fast-forward only, even if the caller knows the current SHA.
        self._run(directory, ["merge-base", "--is-ancestor", expected_sha, sha])
        self._run(
            directory,
            [
                "push",
                "--porcelain",
                "--force-with-lease=refs/heads/" + branch + ":" + expected_sha,
                "origin",
                sha + ":refs/heads/" + branch,
            ],
            writing=True,
        )
        return {"branch": branch, "previous_sha": expected_sha, "sha": sha, "published": True}

    def update_ref(self, repo: str, branch: str, sha: str, expected_sha: str) -> dict:
        self.settings.authorize_write(repo)
        with self._cache(repo, refresh=True) as directory:
            self._resolve(directory, object_sha(sha))
            return {"repo": repo, **self._push(directory, branch, sha, expected_sha)}

    def create_branch(self, repo: str, branch: str, from_ref: str) -> dict:
        self.settings.authorize_write(repo)
        self._branch(branch)
        with self._cache(repo, refresh=True) as directory:
            sha = self._resolve(directory, from_ref)
            if self._run(directory, ["ls-remote", "origin", "refs/heads/" + branch]).strip():
                raise TeaError("conflict", "Destination branch already exists.")
            output = self._run(
                directory,
                [
                    "push",
                    "--porcelain",
                    "--force-with-lease=refs/heads/" + branch + ":",
                    "origin",
                    sha + ":refs/heads/" + branch,
                ],
                writing=True,
            )
            if not any(line.startswith(b"*") for line in output.splitlines()):
                raise TeaError("conflict", "Git did not create a new branch; inspect the destination.")
            return {"repo": repo, "branch": branch, "sha": sha, "published": True}

    def delete_branch(self, repo: str, branch: str, expected_sha: str) -> dict:
        """Used by test cleanup; deletion is conditional on the exact owned branch head."""
        self.settings.authorize_write(repo)
        self._branch(branch)
        expected_sha = object_sha(expected_sha)
        with self._cache(repo) as directory:
            self._run(
                directory,
                [
                    "push",
                    "--porcelain",
                    "--force-with-lease=refs/heads/" + branch + ":" + expected_sha,
                    "origin",
                    ":refs/heads/" + branch,
                ],
                writing=True,
            )
            return {"repo": repo, "branch": branch, "deleted": True}

    def commit_files(
        self,
        repo: str,
        branch: str,
        expected_sha: str,
        changes: list[dict],
        message: str,
        author_name: str,
        author_email: str,
    ) -> dict:
        self.settings.authorize_write(repo)
        with self._cache(repo, refresh=True) as directory:
            base = self._resolve(directory, branch)
            if base != object_sha(expected_sha):
                raise TeaError("conflict", "Branch SHA does not match expected_sha.")
            tree = self._tree(directory, changes, base)
            sha = self._commit(directory, tree, [base], message, author_name, author_email)
            return {"repo": repo, **self._push(directory, branch, sha, base)}
