import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jasminetea_mcp.api import GiteaClient
from jasminetea_mcp.cli import client_example
from jasminetea_mcp.config import NETWORK_ENV_VARS
from jasminetea_mcp.errors import TeaError
from jasminetea_mcp.git_backend import GitBackend


@pytest.fixture
def network_servers(monkeypatch):
    for name in NETWORK_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    servers = []
    records = [[], []]
    for index in range(2):
        class Handler(BaseHTTPRequestHandler):
            bucket = records[index]

            def do_GET(self):
                self.bucket.append(self.path)
                body = b'{"login":"owner"}'
                self.send_response(403 if "info/refs" in self.path else 200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
    try:
        yield [f"http://127.0.0.1:{s.server_port}" for s, _ in servers], records
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(2)


@pytest.mark.parametrize("enabled,bypass,selected", [(False, False, 0), (True, False, 1), (True, True, 0)])
async def test_http_and_git_share_explicit_proxy_policy(settings, monkeypatch, network_servers, enabled, bypass, selected):
    urls, records = network_servers
    settings.base_url = urls[0]
    settings.trust_env = enabled
    monkeypatch.setenv("HTTP_PROXY", urls[1])
    if bypass:
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    api = GiteaClient(settings)
    try:
        assert await api.request("GET", "/user") == {"login": "owner"}
    finally:
        await api.close()
    backend = GitBackend(settings)
    with pytest.raises(TeaError):
        backend._run(None, ["ls-remote", backend._remote_url("owner/repo")])
    assert len(records[selected]) == 2 and not records[1 - selected]
    assert any("info/refs" in path for path in records[selected])


def test_child_environment_excludes_ambient_credentials_and_git_settings(settings, monkeypatch):
    for key in ("AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "GIT_SSH_COMMAND", "GIT_TRACE", "RANDOM_SECRET"):
        monkeypatch.setenv(key, "private")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:password@proxy.invalid")
    env = GitBackend(settings)._environment()
    assert "BDGIT_TOKEN" not in env
    assert not any(key in env for key in ("AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "GIT_SSH_COMMAND", "GIT_TRACE", "RANDOM_SECRET", "HTTPS_PROXY"))
    config = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert config["http.proxy"] == ""
    settings.trust_env = True
    assert settings.environment_proxy() == "http://user:password@proxy.invalid"


def test_write_timeout_is_uncertain_and_never_retried(settings, monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args[0], 1, stderr=b"unit-test-token")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(TeaError) as caught:
        GitBackend(settings)._run(None, ["push", "--porcelain"], writing=True)
    assert caught.value.code == "git_timeout" and caught.value.uncertain
    assert len(calls) == 1 and "unit-test-token" not in str(caught.value)


@pytest.mark.parametrize("client", ["codex", "claude", "deepseek"])
def test_proxy_client_configuration_forwards_references_only(tmp_path, monkeypatch, client):
    config = tmp_path / "settings.toml"
    config.write_text("trust_env = true\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://private:password@proxy.invalid")
    example = client_example(client, str(config))
    assert "HTTPS_PROXY" in example and "NO_PROXY" in example and "SSL_CERT_FILE" in example
    assert "private:password" not in example
    config.write_text("trust_env = false\n", encoding="utf-8")
    assert "HTTPS_PROXY" not in client_example(client, str(config))
