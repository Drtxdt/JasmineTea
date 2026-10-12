import base64
import json
from pathlib import Path

import httpx
import pytest

from jasminetea_mcp.api import GiteaClient
from jasminetea_mcp.config import Settings
from jasminetea_mcp.profiles import WRITE_OPERATIONS
from jasminetea_mcp.service import TeaService


@pytest.fixture
def schema():
    return json.loads((Path(__file__).parent / "fixtures" / "bdgit-swagger.json").read_text(encoding="utf-8"))


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BDGIT_TOKEN", "unit-test-token")
    return Settings(
        base_url="https://example.test", write_repositories=["owner/repo"], cache_dir=tmp_path / "cache",
        tool_profile="full", allowed_write_operations=sorted(WRITE_OPERATIONS),
        protected_branches=[], protect_default_branch=False
    )


@pytest.fixture
def api_service(settings, schema):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers.get("Authorization") == "token unit-test-token"
        path = request.url.path.removeprefix("/api/v1")
        if request.method != "GET":
            return httpx.Response(200, json={"id": 1, "number": 1})
        if path == "/user":
            data = {"login": "owner", "email": "test@example.test"}
        elif path == "/repos/owner/repo":
            data = {"default_branch": "main", "permissions": {"push": True}, "full_name": "owner/repo"}
        elif path == "/repos/owner/repo/contents/path.txt":
            data = {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode("你好\nsecond\n".encode()).decode(),
                "sha": "a" * 40,
            }
        elif path.endswith("/issues/1"):
            data = {"id": 1, "assignees": [{"login": "owner"}]}
        elif path.endswith((".diff", ".patch")):
            return httpx.Response(200, text="diff --git a/path.txt b/path.txt\n+hello\n")
        elif path.endswith("/pulls/1"):
            data = {"id": 1, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
        elif path.endswith(("/git/commits/" + "a" * 40, "/git/blobs/" + "a" * 40, "/permission", "/status")):
            data = {"sha": "a" * 40, "permission": "write"}
        else:
            data = [{"id": 1, "name": "main", "login": "owner", "owner": {"login": "owner", "type": "User"}}]
        return httpx.Response(200, json=data)

    api = GiteaClient(settings, transport=httpx.MockTransport(respond))
    api.schema = schema
    return TeaService(settings, api=api), requests
