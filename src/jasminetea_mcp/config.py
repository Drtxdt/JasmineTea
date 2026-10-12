import os
import re
import tomllib
import warnings
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import proxy_bypass_environment

from dotenv import dotenv_values
from platformdirs import user_cache_path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import TeaError
from .profiles import MERGES, SAFE_WRITES, WRITE_OPERATIONS

NETWORK_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR")


def repository(value: str) -> str:
    if not isinstance(value, str):
        raise TeaError("invalid_input", "Repository must be a string.")
    parts = value.split("/")
    if len(parts) != 2 or any(not p or p in (".", "..") for p in parts):
        raise TeaError("invalid_input", "Repository must be owner/name.")
    if any(any(c in p for c in "\\%?#\x00\r\n") for p in parts):
        raise TeaError("invalid_input", "Invalid repository name.")
    return value


def file_path(value: str, *, root: bool = False) -> str:
    if not isinstance(value, str):
        raise TeaError("invalid_input", "File path must be a string.")
    if root and value == "":
        return ""
    if not value or "\\" in value or "\x00" in value or value.startswith("/"):
        raise TeaError("invalid_input", "Expected a relative POSIX file path.")
    if any(p in ("", ".", "..") for p in value.split("/")):
        raise TeaError("invalid_input", "Path traversal and empty path components are forbidden.")
    return value


def repo_endpoint(repo: str) -> str:
    owner, name = repository(repo).split("/")
    return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = "https://bdgit.educoder.net"
    token_env: str = "BDGIT_TOKEN"
    write_repositories: list[str] = Field(default_factory=list)
    tool_profile: str = "core"
    allowed_write_operations: list[str] = Field(default_factory=lambda: list(SAFE_WRITES))
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master"])
    protect_default_branch: bool = True
    git_refresh_ttl_seconds: float = Field(default=30, ge=0)
    git_fetch_timeout_seconds: float = Field(default=120, gt=0, le=3600)
    cache_dir: Path = Field(default_factory=lambda: user_cache_path("jasminetea-mcp"))
    timeout_seconds: float = Field(default=20, gt=0, le=300)
    trust_env: bool = False
    max_text_bytes: int = Field(default=524288, gt=0, le=16777216)
    max_tool_bytes: int = Field(default=1048576, gt=0, le=16777216)
    max_response_bytes: int = Field(default=16777216, gt=0, le=67108864)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use a platform URL without credentials, query, or fragment")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError("HTTPS is required except for local test servers")
        if any(part in (".", "..") for part in parsed.path.split("/")) or "%" in parsed.path:
            raise ValueError("Invalid platform path")
        return value.rstrip("/")

    @field_validator("token_env")
    @classmethod
    def validate_env(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("token_env must be an environment variable name")
        return value

    @field_validator("write_repositories")
    @classmethod
    def validate_repos(cls, values: list[str]) -> list[str]:
        return sorted(set(repository(v) for v in values))

    def token(self) -> str:
        value = os.environ.get(self.token_env, "").strip()
        if not value or value == "replace-with-your-personal-token":
            raise TeaError("authentication_required", f"Set the local {self.token_env} environment variable.")
        if any(c in value for c in "\r\n\x00"):
            raise TeaError("authentication_required", "Invalid token format.")
        return value

    def authorize_write(self, repo: str) -> None:
        if repository(repo) not in self.write_repositories:
            raise TeaError("write_disabled", f"Write access is not configured for {repo}.")

    @field_validator("tool_profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        if value not in ("core", "review", "full"):
            raise ValueError("Unknown tool profile")
        return value

    @field_validator("allowed_write_operations")
    @classmethod
    def validate_operations(cls, values: list[str]) -> list[str]:
        if set(values) - WRITE_OPERATIONS:
            raise ValueError("Unknown write operation")
        return sorted(set(values))

    @staticmethod
    def normalize_branch(branch: str) -> str:
        if not isinstance(branch, str):
            raise TeaError("invalid_input", "Expected a literal Git branch name.")
        branch = branch.removeprefix("refs/heads/")
        if (
            not branch
            or branch in ("HEAD", "@")
            or branch.startswith(("-", "/", "refs/"))
            or branch.endswith(("/", ".", ".lock"))
            or ".." in branch
            or "@{" in branch
            or any(c in branch for c in " ~^:?*[\\\x00\r\n")
            or any(ord(c) < 33 or ord(c) == 127 for c in branch)
            or any(not p or p.startswith(".") or p.endswith(".lock") for p in branch.split("/"))
        ):
            raise TeaError("invalid_input", "Expected a literal Git branch name.")
        return branch

    @field_validator("protected_branches")
    @classmethod
    def validate_protected(cls, values: list[str]) -> list[str]:
        try:
            return sorted(set(cls.normalize_branch(v) for v in values))
        except TeaError:
            raise ValueError("Invalid protected branch") from None

    def operation_enabled(self, operation: str) -> bool:
        return operation in self.allowed_write_operations and not (
            operation in MERGES and (self.protected_branches or self.protect_default_branch)
        )

    def authorize_operation(self, repo: str, operation: str) -> None:
        self.authorize_write(repo)
        if not self.operation_enabled(operation):
            raise TeaError("write_disabled", "Operation is disabled by the configured write policy.")

    def authorize_branch(self, branch: str, default_branch: str | None = None) -> str:
        branch = self.normalize_branch(branch)
        if branch in self.protected_branches or (self.protect_default_branch and branch == default_branch):
            raise TeaError("protected_branch", "Local policy protects this branch.")
        return branch

    def network_environment(self) -> dict[str, str]:
        if not self.trust_env:
            return {}
        result = {}
        for name in NETWORK_ENV_VARS:
            # Match the usual lowercase proxy precedence without importing arbitrary environment data.
            value = os.environ.get(name.lower(), os.environ.get(name, ""))
            if value:
                result[name] = value
        return result

    def environment_proxy(self) -> str | None:
        network = self.network_environment()
        host = urlsplit(self.base_url)
        if proxy_bypass_environment(host.netloc, {"no": network.get("NO_PROXY", "")}):
            return None
        return network.get(host.scheme.upper() + "_PROXY") or network.get("ALL_PROXY")


def load_settings(path: str | None = None) -> Settings:
    directory = Path(path).resolve().parent if path else Path.cwd()
    if path:
        with Path(path).open("rb") as stream:
            data = tomllib.load(stream)
        if "cache_dir" in data:
            cache = Path(data["cache_dir"])
            data["cache_dir"] = cache if cache.is_absolute() else directory / cache
        if "tool_profile" not in data:
            warnings.warn(
                "Legacy configuration uses full profile; write operations now default to six safe operations. Add tool_profile explicitly.",
                UserWarning,
                stacklevel=2,
            )
        data.setdefault("tool_profile", "full")
        settings = Settings(**data)
    else:
        settings = Settings()
    # Only resolve this profile's credential. Empty forwarded variables may use a local file.
    if not os.environ.get(settings.token_env, "").strip():
        for source in dict.fromkeys([directory / ".env", Path.cwd() / ".env"]):
            if source.is_file():
                value = dotenv_values(source, interpolate=False).get(settings.token_env)
                if value:
                    os.environ[settings.token_env] = value
                    break
    return settings
