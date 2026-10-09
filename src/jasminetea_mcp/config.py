import os
import re
import tomllib
from pathlib import Path
from urllib.parse import quote, urlsplit

from dotenv import dotenv_values
from platformdirs import user_cache_path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import TeaError


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
    cache_dir: Path = Field(default_factory=lambda: user_cache_path("jasminetea-mcp"))
    timeout_seconds: float = Field(default=20, gt=0, le=300)
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


def load_settings(path: str | None = None) -> Settings:
    directory = Path(path).resolve().parent if path else Path.cwd()
    if path:
        with Path(path).open("rb") as stream:
            data = tomllib.load(stream)
        if "cache_dir" in data:
            cache = Path(data["cache_dir"])
            data["cache_dir"] = cache if cache.is_absolute() else directory / cache
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
