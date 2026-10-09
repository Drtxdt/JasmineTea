import json

import httpx
import pytest

from jasminetea_mcp.api import GiteaClient
from jasminetea_mcp.errors import TeaError


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://evil.test/api/v1/user",
        "/admin/users",
        "/user/settings",
        "/user/applications/oauth2",
        "/repos/owner/repo/contents/../secrets",
        "/repos/owner/repo/hooks",
        "/repos/owner/repo/contents/%2e%2e/secrets",
        "https://example.test/api/v1/user?token=secret",
    ],
)
def test_fetch_allowlist(settings, endpoint):
    client = GiteaClient(settings)
    with pytest.raises(TeaError):
        client.safe_fetch_path(endpoint)


async def test_mutation_repo_cannot_be_swapped(settings):
    client = GiteaClient(settings, transport=httpx.MockTransport(lambda _: pytest.fail("HTTP must not run")))
    with pytest.raises(TeaError, match="does not match"):
        await client.request("POST", "/repos/other/repo/issues", repo="owner/repo", body={})
    await client.close()


async def test_missing_auth(settings, monkeypatch):
    monkeypatch.delenv("BDGIT_TOKEN")
    client = GiteaClient(settings)
    with pytest.raises(TeaError) as caught:
        await client.request("GET", "/user")
    assert caught.value.code == "authentication_required"
    await client.close()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_required"),
        (403, "forbidden"),
        (404, "not_found_or_hidden"),
        (409, "conflict"),
        (422, "invalid_input"),
        (429, "rate_limited"),
    ],
)
async def test_http_errors_are_typed_and_redacted(settings, status, code, monkeypatch):
    async def no_wait(_):
        pass

    monkeypatch.setattr("jasminetea_mcp.api.asyncio.sleep", no_wait)
    client = GiteaClient(
        settings,
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"message": "unit-test-token"})),
    )
    with pytest.raises(TeaError) as caught:
        await client.request("GET", "/user")
    assert caught.value.code == code
    assert "unit-test-token" not in str(caught.value)
    await client.close()


async def test_write_timeout_is_uncertain_and_not_retried(settings):
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout("possibly secret data")

    client = GiteaClient(settings, transport=httpx.MockTransport(fail))
    with pytest.raises(TeaError) as caught:
        await client.request("POST", "/repos/owner/repo/issues", repo="owner/repo", body={"title": "test"})
    assert len(calls) == 1
    assert caught.value.uncertain
    assert "secret" not in str(caught.value)
    await client.close()


async def test_write_502_not_retried(settings):
    calls = []

    def fail(request):
        calls.append(request)
        return httpx.Response(502, text="gateway")

    client = GiteaClient(settings, transport=httpx.MockTransport(fail))
    with pytest.raises(TeaError) as caught:
        await client.request("POST", "/repos/owner/repo/issues", repo="owner/repo", body={})
    assert len(calls) == 1 and caught.value.uncertain
    await client.close()


async def test_redirect_never_forwards_token(settings):
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.test"})

    client = GiteaClient(settings, transport=httpx.MockTransport(redirect))
    with pytest.raises(TeaError) as caught:
        await client.request("GET", "/user")
    assert caught.value.code == "redirect_refused" and len(calls) == 1
    await client.close()


async def test_pagination_and_response_bound(settings):
    client = GiteaClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[1, 2])))
    result = await client.page("/user/repos", page=2, limit=2)
    assert result["next_page"] == 3
    with pytest.raises(TeaError):
        await client.page("/user/repos", limit=101)
    settings.max_response_bytes = 2
    with pytest.raises(TeaError) as caught:
        await client.request("GET", "/user")
    assert caught.value.code == "response_too_large"
    await client.close()


async def test_put_and_delete_use_blob_sha(api_service):
    service, requests = api_service
    await service.update_file("owner/repo", "path.txt", "main", "new", "message", "a" * 40)
    await service.delete_file("owner/repo", "path.txt", "main", "delete", "b" * 40)
    assert json.loads(requests[0].content)["sha"] == "a" * 40
    assert json.loads(requests[1].content)["sha"] == "b" * 40
    await service.close()


def test_nested_encoding_cannot_escape_fetch_allowlist(settings):
    client = GiteaClient(settings)
    with pytest.raises(TeaError):
        client.safe_fetch_path("/repos/owner/repo/contents/%252e%252e/secrets")
