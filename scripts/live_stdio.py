"""Verify real platform calls through a separate MCP stdio server process."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from mcp import Client, StdioServerParameters

from jasminetea_mcp.config import load_settings

REPO = "hndx202426010302/JasmineTea-test"


async def run():
    config = Path("examples/test-repository.toml").resolve()
    settings = load_settings(str(config))
    if settings.base_url != "https://bdgit.educoder.net":
        raise ValueError("Live acceptance is restricted to the supplied bdgit test instance.")
    settings.authorize_write(REPO)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jasminetea_mcp", "serve", "--config", str(config)],
        env={"PYTHONUTF8": "1"},
    )
    report = {"checks": [], "passed": False}
    async with Client(params, mode="legacy", read_timeout_seconds=60) as client:
        async def call(name, arguments):
            result = await client.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError(f"{name}: {result.content}")
            return result.structured_content

        report["tool_count"] = len((await client.list_tools()).tools)
        report["checks"].append("tool_discovery")
        capabilities = await call("get_capabilities", {})
        assert len(capabilities["capabilities"]) == 89
        repo = (await call("get_repo", {"repo": REPO}))["data"]
        read = await call("read_file", {"repo": REPO, "path": "README.md", "ref": repo["default_branch"]})
        assert "jasminetea-test" in read["content"]
        report["checks"].append("authenticated_file_read")
        title = "[jasminetea-test:stdio-" + uuid4().hex[:12] + "] MCP acceptance"
        issue = (await call("create_issue", {"repo": REPO, "title": title}))["data"]
        report["issue"] = issue["number"]
        Path("artifacts/live-stdio.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        try:
            await call("add_comment_to_issue", {"repo": REPO, "index": issue["number"], "body": title})
            report["checks"].append("controlled_issue_write")
            denied = await client.call_tool("create_issue", {"repo": "other/repository", "title": title})
            assert denied.is_error and "write_disabled" in str(denied.content)
            report["checks"].append("write_allowlist_denial")
        finally:
            await call("update_issue", {"repo": REPO, "index": issue["number"], "fields": {"state": "closed"}})
            report["checks"].append("issue_cleanup")
        report["passed"] = True
    Path("artifacts/live-stdio.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(run())
