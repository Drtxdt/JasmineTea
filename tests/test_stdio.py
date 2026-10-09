import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import pytest
from mcp import Client, StdioServerParameters


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_real_stdio_process_read_write_and_denial(tmp_path, schema, mode):
    posted = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_json(self, data):
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/swagger.v1.json":
                self.send_json(schema)
            elif path.endswith("/contents/readme.txt"):
                self.send_json(
                    {
                        "type": "file",
                        "encoding": "base64",
                        "sha": "a" * 40,
                        "content": base64.b64encode("stdio 中文\n".encode()).decode(),
                    }
                )
            else:
                self.send_json({"login": "owner", "default_branch": "main"})

        def do_POST(self):
            if self.headers.get("Authorization") != "token stdio-test-token":
                self.send_error(403)
                return
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            posted.append(data)
            self.send_json({"id": 1, "number": 1, **data})

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "stdio.toml"
    config.write_text(
        f'base_url = "http://127.0.0.1:{http.server_port}"\nwrite_repositories = ["owner/repo"]\n',
        encoding="utf-8",
    )
    params = StdioServerParameters(
        command=os.environ.get("JASMINETEA_TEST_PYTHON", sys.executable),
        args=["-m", "jasminetea_mcp", "serve", "--config", str(config)],
        env={"BDGIT_TOKEN": "stdio-test-token", "PYTHONUTF8": "1"},
    )
    try:
        async with Client(params, mode=mode, read_timeout_seconds=15) as client:
            listed = await client.list_tools()
            assert len(listed.tools) >= 50
            capabilities = await client.call_tool("get_capabilities", {})
            assert not capabilities.is_error and len(capabilities.structured_content["capabilities"]) == 89
            read = await client.call_tool(
                "read_file", {"repo": "owner/repo", "path": "readme.txt", "ref": "main"}
            )
            assert not read.is_error and "stdio" in str(read.structured_content)
            write = await client.call_tool("create_issue", {"repo": "owner/repo", "title": "stdio fixture"})
            assert not write.is_error and len(posted) == 1
            denied = await client.call_tool(
                "create_issue", {"repo": "other/repo", "title": "must not reach HTTP"}
            )
            assert denied.is_error and len(posted) == 1
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)
