import base64
import inspect
import json
import re

import httpx
import pytest
from jsonschema import Draft4Validator

from jasminetea_mcp.api import GiteaClient
from jasminetea_mcp.errors import TeaError
from jasminetea_mcp.parity import capability_report, reference_matrix
from jasminetea_mcp.service import TeaService

VALUES = {
    "repo": "owner/repo",
    "endpoint": "/user",
    "query": "main",
    "username": "owner",
    "affiliation": "owned",
    "index": 1,
    "comment_id": 1,
    "label_id": 1,
    "review_id": 1,
    "sha": "a" * 40,
    "expected_sha": "a" * 40,
    "expected_head_sha": "a" * 40,
    "path": "path.txt",
    "branch": "new",
    "from_ref": "main",
    "head": "new",
    "base": "main",
    "content": "hello",
    "message": "test",
    "body": "comment",
    "title": "test",
    "fields": {"title": "updated"},
    "labels": [1],
    "usernames": ["owner"],
    "reviewers": ["owner"],
    "event": "COMMENT",
}


def arguments(method, repo="owner/repo"):
    result = {}
    for name, parameter in inspect.signature(method).parameters.items():
        if parameter.default is inspect.Parameter.empty:
            result[name] = repo if name == "repo" else VALUES.get(name, "test")
    return result


async def test_all_mutating_tools_enforce_whitelist_before_io(settings):
    api = GiteaClient(
        settings, transport=httpx.MockTransport(lambda _: pytest.fail("Denied write must perform no I/O"))
    )
    service = TeaService(settings, api=api)
    for name, method in service.methods().items():
        if method.tea_meta["write"]:
            with pytest.raises(TeaError) as caught:
                await method(**arguments(method, repo="other/repo"))
            assert caught.value.code == "write_disabled", name
    await service.close()


def test_89_capabilities_have_real_implementations_or_explanations(settings, schema):
    service = TeaService(settings)
    rows = reference_matrix()
    assert len(rows) == 89
    assert len({r["reference"] for r in rows}) == 89
    report = capability_report(schema, service.methods(), True)
    assert not any(r["status"] == "implementation_missing" for r in report)
    assert all(r["notes"] for r in rows)
    for row in rows:
        if row["backend"] != "unsupported":
            assert row["tool"] in service.methods(), row
    lock = next(r for r in report if r["reference"] == "lock_issue_conversation")
    assert lock["status"] == "unsupported"


async def test_all_native_tools_call_real_instance_contracts(api_service, schema):
    """Exercise each advertised API method against the instance's recorded path/body contract."""
    service, requests = api_service
    for _name, method in service.methods().items():
        meta = method.tea_meta
        if not meta["path"]:
            continue
        operation = schema["paths"].get(meta["path"], {}).get(meta["method"])
        if not operation or (
            meta["fields"]
            and not all(f in service.api.body_properties(schema, operation) for f in meta["fields"])
        ):
            continue
        await method(**arguments(method))
    assert len(requests) >= 40
    for request in requests:
        matching = [
            item
            for path, item in schema["paths"].items()
            if re.fullmatch(
                re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)), request.url.path.removeprefix("/api/v1")
            )
        ]
        assert matching, request.url.path
        operation = next(item[request.method.lower()] for item in matching if request.method.lower() in item)
        queries = {p["name"]: p for p in operation.get("parameters", []) if p.get("in") == "query"}
        assert set(request.url.params) <= set(queries), request.url
        for key in request.url.params:
            value = request.url.params[key]
            parameter = queries[key]
            if "enum" in parameter:
                assert value in parameter["enum"], (key, value)
            if parameter["type"] == "integer":
                assert str(int(value)) == value, (key, value)
            elif parameter["type"] == "boolean":
                assert value in ("true", "false"), (key, value)
        if request.method == "GET" or not request.content:
            continue
        for parameter in operation.get("parameters", []):
            if parameter.get("in") == "body":
                contract = {**parameter["schema"], "definitions": schema["definitions"]}
                Draft4Validator(contract).validate(json.loads(request.content))
    await service.close()


async def test_unknown_edit_fields_fail_before_mutation(api_service):
    service, requests = api_service
    with pytest.raises(TeaError) as caught:
        await service.update_pull_request("owner/repo", 1, {"force_merge": True})
    assert caught.value.code == "unsupported"
    assert not requests
    await service.close()


async def test_merge_head_and_force_policy(api_service):
    service, requests = api_service
    await service.merge_pull_request("owner/repo", 1, "a" * 40)
    await service.enable_auto_merge("owner/repo", 1, "b" * 40)
    assert json.loads(requests[0].content)["head_commit_id"] == "a" * 40
    assert json.loads(requests[1].content)["merge_when_checks_succeed"] is True
    assert all(json.loads(request.content)["force_merge"] is False for request in requests)
    await service.close()


async def test_text_range_truncation_and_binary(api_service, settings):
    service, _ = api_service
    result = await service.read_file("owner/repo", "path.txt", "main", start_line=2)
    assert result["content"] == "second\n"
    settings.max_text_bytes = 4
    assert (await service.read_file("owner/repo", "path.txt", "main"))["truncated"]
    service.api.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "type": "file",
                    "encoding": "base64",
                    "content": base64.b64encode(b"\x00\xff").decode(),
                },
            )
        )
    )
    with pytest.raises(TeaError) as caught:
        await service.read_file("owner/repo", "path.txt", "main")
    assert caught.value.code == "binary_content"
    await service.close()


async def test_directory_listing_has_bounded_pagination(settings):
    api = GiteaClient(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=[{"path": str(i)} for i in range(120)])
        ),
    )
    service = TeaService(settings, api=api)
    first = await service.list_files("owner/repo", ref="main", limit=50)
    last = await service.list_files("owner/repo", ref="main", page=3, limit=50)
    assert len(first["items"]) == 50 and first["next_page"] == 2
    assert len(last["items"]) == 20 and last["next_page"] is None
    await service.close()
