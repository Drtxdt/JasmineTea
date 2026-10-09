import json
import tomllib

import pytest
from mcp import Client

from jasminetea_mcp.cli import client_example
from jasminetea_mcp.server import create_server


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_protocol_discovery_read_write_and_error(api_service, mode):
    service, requests = api_service
    server = create_server(service)
    async with Client(server, mode=mode) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {"read_file", "commit_files", "get_capabilities", "create_issue"} <= names
        result = await client.call_tool(
            "read_file", {"repo": "owner/repo", "path": "path.txt", "ref": "main"}
        )
        assert not result.is_error
        result = await client.call_tool("create_issue", {"repo": "owner/repo", "title": "protocol test"})
        assert not result.is_error
        before = len(requests)
        result = await client.call_tool("create_issue", {"repo": "other/repo", "title": "denied"})
        assert result.is_error and "write_disabled" in result.content[0].text
        assert len(requests) == before
        result = await client.call_tool("lock_issue_conversation", {"repo": "owner/repo", "index": 1})
        assert result.is_error and "unsupported" in result.content[0].text
        read = next(t for t in tools.tools if t.name == "read_file")
        write = next(t for t in tools.tools if t.name == "create_issue")
        assert read.annotations.read_only_hint is True
        assert write.annotations.read_only_hint is False


def test_client_configs_reference_credentials_without_values(tmp_path):
    path = str(tmp_path / "config.toml")
    codex = tomllib.loads(client_example("codex", path))
    assert codex["mcp_servers"]["jasminetea"]["env_vars"] == ["BDGIT_TOKEN"]
    claude = json.loads(client_example("claude", path))
    assert claude["mcpServers"]["jasminetea"]["env"]["BDGIT_TOKEN"] == "${BDGIT_TOKEN:-}"
    dsh = client_example("deepseek", path)
    assert "@deepseek-ai/dsh-mcp-client" in dsh and "!!js process.env.BDGIT_TOKEN" in dsh
    assert "unit-test-token" not in dsh


async def test_large_write_result_reports_uncertainty_without_repeating_mutation(api_service):
    service, requests = api_service
    service.settings.max_tool_bytes = 16
    async with Client(create_server(service)) as client:
        result = await client.call_tool("create_issue", {"repo": "owner/repo", "title": "created once"})
        assert result.is_error
        error = json.loads(result.content[0].text)
        assert error["code"] == "response_too_large" and error["result_uncertain"] is True
        assert len([r for r in requests if r.method == "POST"]) == 1
