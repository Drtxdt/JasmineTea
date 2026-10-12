import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client
from test_git import git
from test_git import local_git as local_git

from jasminetea_mcp.api import GiteaClient
from jasminetea_mcp.cache_security import private_path
from jasminetea_mcp.cli import client_example
from jasminetea_mcp.config import Settings, load_settings
from jasminetea_mcp.errors import TeaError
from jasminetea_mcp.git_backend import GitBackend
from jasminetea_mcp.policy import WritePermit, cancel_event, permitted
from jasminetea_mcp.profiles import CORE, REVIEW, SAFE_WRITES
from jasminetea_mcp.server import create_server
from jasminetea_mcp.service import TeaService


@pytest.mark.parametrize(
    "link,expected,source",
    [
        ('<https://example.test/api/v1/user/repos?page=2&limit=50>; rel="next"', 2, "link"),
        ('<https://evil.test/api/v1/user/repos?page=2&limit=50>; rel="next"', 2, "conservative"),
        ('<https://example.test/api/v1/user/repos?page=1&limit=50>; rel="prev"', None, "link"),
        ('<https://example.test/api/v1/user/repos?page=wat>; rel="next"', 2, "conservative"),
        ("", 2, "conservative"),
    ],
)
async def test_clamped_pagination_never_follows_link(settings, schema, link, expected, source):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=list(range(50)), headers={"Link": link, "X-Total-Count": "120"})

    api = GiteaClient(settings, transport=httpx.MockTransport(respond))
    api.schema = schema
    result = await api.page("/user/repos", page=2 if 'rel="prev"' in link else 1, limit=100)
    assert result["next_page"] == expected and result["total_count"] == 120
    assert result["pagination_source"] == source and len(calls) == 1
    if source == "link" and expected:
        assert result["next_limit"] == 50
    await api.close()


async def test_swagger_disabled_uses_only_basic_reads(settings):
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path == "/swagger.v1.json":
            return httpx.Response(404)
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "1.24.5"})
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"login": "owner"})
        return httpx.Response(200, json={"default_branch": "main"})

    api = GiteaClient(settings, transport=httpx.MockTransport(respond))
    service = TeaService(settings, api=api)
    report = await service.get_capabilities()
    assert report["schema_source"] == "compatibility" and not report["schema_verified"]
    advanced = [r for r in report["capabilities"] if r["tool"] == "search_issues"]
    assert advanced and all(r["status"] == "unverified" and not r["enabled"] for r in advanced)
    assert (await service.get_profile())["login"] == "owner"
    assert await api.request("GET", "/repos/owner/repo/contents/nested/path.txt")
    before = len(calls)
    with pytest.raises(TeaError, match="live API"):
        await service.create_issue("owner/repo", "blocked")
    with pytest.raises(TeaError, match="compatibility set"):
        await api.request("GET", "/repos/owner/repo/issues")
    assert len(calls) == before
    await service.close()


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("profile,expected", [("core", CORE), ("review", REVIEW), ("full", None)])
async def test_profile_discovery_and_hidden_writes(api_service, mode, profile, expected):
    service, requests = api_service
    service.settings.tool_profile = profile
    service.settings.allowed_write_operations = list(SAFE_WRITES)
    service.settings.write_repositories = []
    async with Client(create_server(service), mode=mode) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == (expected or {n for n, m in service.methods().items() if not m.tea_meta["write"]})
        before = len(requests)
        result = await client.call_tool("commit_files", {"repo": "owner/repo"})
        assert result.is_error and len(requests) == before
        report = await client.call_tool("get_capabilities", {})
        assert set(report.structured_content["registered_tools"]) == names


async def test_safe_writes_and_protected_default_before_mutation(api_service):
    service, requests = api_service
    service.settings.allowed_write_operations = list(SAFE_WRITES)
    service.settings.protected_branches = ["main", "master"]
    service.settings.protect_default_branch = True
    for branch in ("main", "refs/heads/main", "HEAD", "refs/tags/feature"):
        before = len(requests)
        with pytest.raises(TeaError):
            await service.commit_files("owner/repo", branch, "a" * 40, [], "denied")
        assert len(requests) == before
    with pytest.raises(TeaError, match="protects"):
        service.settings.authorize_branch("custom-default", "custom-default")
    for name in ("merge_pull_request", "enable_auto_merge"):
        service.settings.allowed_write_operations.append(name)
        assert name not in service.registered_methods()
        before = len(requests)
        with pytest.raises(TeaError):
            await getattr(service, name)("owner/repo", 1, "a" * 40)
        assert len(requests) == before


async def test_backend_permits_cannot_change_scope(settings, schema):
    api = GiteaClient(settings, transport=httpx.MockTransport(lambda _: pytest.fail("no HTTP")))
    api.schema = schema
    backend = GitBackend(settings)
    with pytest.raises(TeaError, match="permit"):
        backend.create_blob("owner/repo", "denied")
    permit = WritePermit(
        id(settings), "owner/repo", "create_issue", path="/repos/{owner}/{repo}/issues", method="post"
    )
    with permitted(permit):
        with pytest.raises(TeaError, match="operation"):
            await api.request("PATCH", "/repos/owner/repo/issues/1", repo="owner/repo", body={})
        with pytest.raises(TeaError, match="operation"):
            backend.create_blob("owner/repo", "denied")
    await api.close()


def test_deleted_refs_are_pruned_without_deleting_local_tags(local_git):
    backend, remote, initial = local_git
    git("--git-dir", str(remote), "update-ref", "refs/heads/deleted", initial)
    git("--git-dir", str(remote), "update-ref", "refs/tags/deleted-tag", initial)
    assert backend.resolve("owner/repo", "deleted", True) == initial
    assert backend.resolve("owner/repo", "refs/tags/deleted-tag") == initial
    directory = next(backend.settings.cache_dir.glob("*.git"))
    git("--git-dir", str(directory), "update-ref", "refs/tags/local-only", initial)
    git("--git-dir", str(remote), "update-ref", "-d", "refs/heads/deleted")
    git("--git-dir", str(remote), "update-ref", "-d", "refs/tags/deleted-tag")
    backend.resolve("owner/repo", "main", True)
    for ref in ("deleted", "refs/tags/deleted-tag"):
        with pytest.raises(TeaError):
            backend.resolve("owner/repo", ref)
    assert git("--git-dir", str(directory), "rev-parse", "refs/tags/local-only") == initial


def test_read_ttl_and_forced_refresh(local_git, monkeypatch):
    backend, _, initial = local_git
    original, calls = backend._fetch, []

    def fetch(directory):
        calls.append(directory)
        original(directory)

    monkeypatch.setattr(backend, "_fetch", fetch)
    assert backend.resolve("owner/repo", "main") == initial
    backend.resolve("owner/repo", "main")
    assert len(calls) == 1
    backend.resolve("owner/repo", "main", True)
    assert len(calls) == 2
    backend.settings.git_refresh_ttl_seconds = 0
    backend.resolve("owner/repo", "main")
    assert len(calls) == 3


def test_diff_stream_truncation_keeps_structured_output(local_git):
    backend, _, initial = local_git
    commit = backend.commit_files(
        "owner/repo",
        "main",
        initial,
        [{"path": "large.txt", "content": "a line\n" * 20000}],
        "large",
        "Tea",
        "tea@example.test",
    )
    backend.settings.max_text_bytes = 1024
    result = backend.diff("owner/repo", initial, commit["sha"])
    assert result["truncated"] and len(result["diff"].encode()) <= 1024
    assert result["comparison_base_sha"] == initial and result["cache_fetched_at"]
    assert len(json.dumps(result).encode()) < backend.settings.max_tool_bytes


def test_private_cache_and_existing_unsafe_permissions(tmp_path):
    path = private_path(tmp_path / "private")
    assert path.is_dir()
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o700
        path.chmod(0o755)
        with pytest.raises(TeaError, match="Unsafe cache"):
            private_path(path)
        assert path.stat().st_mode & 0o777 == 0o755
    else:
        from jasminetea_mcp.cache_security import _windows

        _windows(path, False)


def test_kimi_configuration_never_serializes_credentials(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("BDGIT_TOKEN", "do-not-output-this")
    path = tmp_path / "tea.toml"
    path.write_text('tool_profile = "core"\n', encoding="utf-8")
    raw = client_example("kimi", str(path))
    config = json.loads(raw)["mcpServers"]["jasminetea"]
    assert Path(config["command"]).is_absolute() and config["cwd"] == str(tmp_path)
    assert config["toolTimeoutMs"] == 300000 and "env" not in config
    assert "do-not-output-this" not in raw and "${" not in raw
    assert load_settings(str(path)).tool_profile == "core"
    path.write_text("", encoding="utf-8")
    assert load_settings(str(path)).tool_profile == "full"
    assert Settings().tool_profile == "core"


def test_diff_stream_timeout_reaps_process(local_git, monkeypatch):
    backend, _, _ = local_git
    directory = next(iter(backend.settings.cache_dir.glob("*.git")), None)
    if directory is None:
        backend.resolve("owner/repo", "main")
        directory = next(backend.settings.cache_dir.glob("*.git"))
    original, children = subprocess.Popen, []

    def start(*args, **kwargs):
        child = original([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", start)
    backend.settings.timeout_seconds = 0.03
    with pytest.raises(TeaError, match="timed out"):
        backend._stream(directory, ["diff"], 100)
    assert children and all(child.poll() is not None for child in children)


async def test_cancelled_service_diff_reaps_worker(local_git, monkeypatch, schema):
    backend, _, _ = local_git
    backend.resolve("owner/repo", "main")
    directory = next(backend.settings.cache_dir.glob("*.git"))
    original, children = subprocess.Popen, []

    def start(*args, **kwargs):
        child = original([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", start)
    api = GiteaClient(
        backend.settings,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"login": "owner"})),
    )
    api.schema = schema
    service = TeaService(backend.settings, api=api, git=backend)
    service._git_identity_ready = True
    backend.diff = lambda *_: backend._stream(directory, ["diff"], 100)
    task = asyncio.create_task(service.compare_commits("owner/repo", "main", "main"))
    for _ in range(100):
        if children:
            break
        await asyncio.sleep(0.01)
    assert children
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        if all(child.poll() is not None for child in children):
            break
        await asyncio.sleep(0.01)
    assert all(child.poll() is not None for child in children)
    assert cancel_event.get() is None
    await service.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL regression")
def test_windows_cache_refuses_broad_acl_without_repair(tmp_path):
    path = private_path(tmp_path / "private-cache")
    subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True)
    with pytest.raises(TeaError, match="Unsafe cache"):
        private_path(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership regression")
def test_cache_refuses_other_owner(tmp_path, monkeypatch):
    path = private_path(tmp_path / "private-cache")
    monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(TeaError, match="Unsafe cache"):
        private_path(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO regression")
def test_ready_marker_cannot_be_a_blocking_fifo(local_git):
    backend, _, initial = local_git
    backend.resolve("owner/repo", "main")
    marker = next(backend.settings.cache_dir.glob("*.git/.ready"))
    marker.unlink()
    os.mkfifo(marker, 0o600)
    with pytest.raises(TeaError, match="Unsafe cache"):
        backend.read_object("owner/repo", initial, "commit")


def test_symlink_cache_is_rejected(tmp_path):
    target, link = tmp_path / "target", tmp_path / "link"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires local OS privilege")
    with pytest.raises(TeaError, match="Unsafe cache"):
        private_path(link)


async def test_default_branch_protection_and_api_branch_binding(api_service):
    service, requests = api_service
    service.settings.protected_branches = []
    service.settings.protect_default_branch = True
    before = len(requests)
    with pytest.raises(TeaError, match="protects"):
        await service.create_branch("owner/repo", "main", "a" * 40)
    assert len(requests) == before + 1 and requests[-1].method == "GET"
    permit = WritePermit(
        id(service.settings),
        "owner/repo",
        "create_branch",
        "feature",
        "/repos/{owner}/{repo}/branches",
        "post",
    )
    with permitted(permit):
        with pytest.raises(TeaError, match="branch"):
            await service.api.request(
                "POST", "/repos/owner/repo/branches", repo="owner/repo", body={"new_branch_name": "main"}
            )
    assert not any(request.method == "POST" for request in requests)


async def test_nested_api_file_path_and_traversal_permit(api_service):
    service, requests = api_service
    await service.create_file("owner/repo", "dir/中文 空格.txt", "feature", "hello", "nested path")
    assert requests[-1].method == "POST" and requests[-1].url.path.endswith("/dir/中文 空格.txt")
    before = len(requests)
    with permitted(
        WritePermit(
            id(service.settings),
            "owner/repo",
            "create_file",
            "feature",
            "/repos/{owner}/{repo}/contents/{filepath}",
            "post",
        )
    ):
        for endpoint in (
            "/repos/owner/repo/contents/../../../other/repo/issues",
            "/repos/owner/repo/contents/%2e%2e/file.txt",
        ):
            with pytest.raises(TeaError, match="endpoint path"):
                await service.api.request("POST", endpoint, repo="owner/repo", body={"branch": "feature"})
    assert len(requests) == before
