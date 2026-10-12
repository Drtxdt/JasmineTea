import json
import logging
from types import MethodType

import httpx
import pytest
from mcp import Client

from jasminetea_mcp.api import GiteaClient
from jasminetea_mcp.errors import TeaError
from jasminetea_mcp.server import create_server
from jasminetea_mcp.service import TeaService, exposed


@pytest.mark.parametrize("status", [200, 204])
@pytest.mark.parametrize("text", [True, False])
async def test_empty_response_preserves_response_kind(settings, status, text):
    api = GiteaClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    try:
        result = await api.request("GET", "/user", text=text)
        assert result == "" if text else result is None
    finally:
        await api.close()


async def test_unpaginated_platform_endpoint_is_sliced_locally(settings, schema):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[{"id": i} for i in range(5)])

    api = GiteaClient(settings, transport=httpx.MockTransport(respond))
    api.schema = schema
    try:
        result = await api.page("/repos/owner/repo/issues/1/comments", page=2, limit=2)
        assert [item["id"] for item in result["items"]] == [2, 3]
        assert result["next_page"] == 3 and not requests[0].url.params
    finally:
        await api.close()


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_empty_diff_and_raw_through_protocol(settings, schema, mode):
    api = GiteaClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    api.schema = schema
    service = TeaService(settings, api=api)
    async with Client(create_server(service), mode=mode) as client:
        for name, arguments, field in [
            ("get_pr_diff", {"repo": "owner/repo", "index": 1}, "text"),
            ("get_pr_diff", {"repo": "owner/repo", "index": 1, "format": "patch"}, "text"),
            ("get_diff", {"repo": "owner/repo", "sha": "a" * 40}, "text"),
            ("fetch", {"endpoint": "/repos/owner/repo/raw/empty.txt"}, "data"),
        ]:
            result = await client.call_tool(name, arguments)
            assert not result.is_error
            assert result.structured_content[field] == ""
            assert result.structured_content["truncated"] is False


async def test_pr_creator_filter_has_real_semantics(settings, schema):
    rows = [{"number": 1, "user": {"login": "owner"}}, {"number": 2, "user": {"login": "other"}}]
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"login": "owner"})
        return httpx.Response(200, json=[row for row in rows
                                        if row["user"]["login"] == request.url.params.get("created_by")])

    api = GiteaClient(settings, transport=httpx.MockTransport(respond))
    api.schema = schema
    service = TeaService(settings, api=api)
    try:
        result = await service.get_users_recent_prs_in_repo("owner/repo")
        assert [row["number"] for row in result["items"]] == [1]
        assert requests[-1].url.params["created_by"] == "owner"
        assert "sort" not in requests[-1].url.params and "created" not in requests[-1].url.params
        await service.list_recent_issues()
        assert "sort" not in requests[-1].url.params
    finally:
        await service.close()


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_readonly_tools_are_absent_and_capabilities_report_registration(api_service, mode):
    service, requests = api_service
    service.settings.write_repositories = []
    async with Client(create_server(service), mode=mode) as client:
        tools = (await client.list_tools()).tools
        names = {tool.name for tool in tools}
        assert len(names) == 38 and all(tool.annotations.read_only_hint for tool in tools)
        assert not {"create_commit", "commit_files", "create_issue", "update_ref"} & names
        capabilities = (await client.call_tool("get_capabilities", {})).structured_content
        assert set(capabilities["registered_tools"]) == names
        assert len(capabilities["capabilities"]) == 89
        before = len(requests)
        result = await client.call_tool("create_issue", {"repo": "owner/repo", "title": "denied"})
        assert result.is_error and len(requests) == before


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_commit_schema_no_longer_accepts_model_identity(api_service, mode):
    service, requests = api_service
    async with Client(create_server(service), mode=mode) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 69
        for tool in tools:
            if tool.name in ("create_commit", "commit_files"):
                properties = tool.input_schema["properties"]
                assert "author_name" not in properties and "author_email" not in properties
        service._git_identity_ready = True
        service.git.create_commit = lambda *args: {"identity": list(args[-2:])}
        result = await client.call_tool("create_commit", {
            "repo": "owner/repo", "tree": "a" * 40, "parents": [], "message": "forge",
            "author_name": "Teammate", "author_email": "teammate@example.test",
        })
        assert not result.is_error
        assert result.structured_content["identity"] == ["owner", "test@example.test"]


@pytest.mark.parametrize("writing", [True, False])
async def test_unexpected_failure_is_sanitized_and_write_uncertainty_is_conservative(api_service, caplog, writing):
    service, _ = api_service
    secret = "unit-test-token and sensitive-repository-data"

    @exposed(write=writing)
    async def failure(self, repo: str):
        raise AttributeError(secret)

    service.failure = MethodType(failure, service)
    if writing:
        service.settings.allowed_write_operations.append("failure")
    caplog.set_level(logging.ERROR)
    async with Client(create_server(service)) as client:
        result = await client.call_tool("failure", {"repo": "owner/repo"})
        assert result.is_error
        error = json.loads(result.content[0].text)
        assert error["code"] == "internal_error" and error["result_uncertain"] is writing
        assert secret not in str(result) and secret not in caplog.text


@pytest.mark.parametrize("user", [None, {}, {"login": "bad\nname"}, {"login": "owner", "email": "bad\nemail"}])
async def test_invalid_identity_stops_before_git(settings, user, schema):
    api = GiteaClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=user)))
    api.schema = schema
    service = TeaService(settings, api=api)
    service.git.create_commit = lambda *_: pytest.fail("Git must not run")
    try:
        with pytest.raises(TeaError) as caught:
            await service.create_commit("owner/repo", "a" * 40, [], "test")
        assert caught.value.code == "invalid_response"
    finally:
        await service.close()
