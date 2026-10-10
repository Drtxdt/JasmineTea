import base64
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from test_git import git
from test_git import local_git as local_git

from jasminetea_mcp.api import GiteaClient
from jasminetea_mcp.errors import TeaError
from jasminetea_mcp.git_backend import GitBackend
from jasminetea_mcp.service import TeaService


def test_failed_initial_fetch_is_recovered_by_nonrefresh_operation(local_git, monkeypatch):
    backend, _, initial = local_git
    original = backend._run
    attempts = 0

    def run(directory, args, **kwargs):
        nonlocal attempts
        if args[0] == "fetch":
            attempts += 1
            if attempts == 1:
                raise TeaError("git_timeout", "test timeout")
        return original(directory, args, **kwargs)

    monkeypatch.setattr(backend, "_run", run)
    with pytest.raises(TeaError):
        backend.resolve("owner/repo", "main")
    directory = next(backend.settings.cache_dir.glob("*.git"))
    assert (directory / "HEAD").is_file() and not (directory / ".ready").exists()
    assert backend.read_object("owner/repo", initial, "commit")["data"]["sha"] == initial
    assert attempts == 2 and (directory / ".ready").is_file()


def test_legacy_cache_migration_and_failed_refresh_preserve_local_objects(local_git, monkeypatch):
    backend, _, _ = local_git
    blob = backend.create_blob("owner/repo", "preserve me")
    directory = next(backend.settings.cache_dir.glob("*.git"))
    (directory / ".ready").unlink()
    assert backend.read_object("owner/repo", blob["sha"], "blob")
    original = backend._run

    def fail_fetch(directory, args, **kwargs):
        if args[0] == "fetch":
            raise TeaError("git_error", "offline")
        return original(directory, args, **kwargs)

    monkeypatch.setattr(backend, "_run", fail_fetch)
    with pytest.raises(TeaError):
        backend.resolve("owner/repo", "main")
    assert (directory / ".ready").is_file()
    assert backend.read_object("owner/repo", blob["sha"], "blob")["data"]["content"] == base64.b64encode(b"preserve me").decode()


def test_empty_remote_is_a_successful_cache_initialization(tmp_path, settings):
    remote = tmp_path / "empty.git"
    git("init", "--bare", str(remote))
    backend = GitBackend(settings)
    backend._remote_url = lambda _: str(remote)
    assert backend.create_blob("owner/repo", "seed")["sha"]
    assert next(settings.cache_dir.glob("*.git/.ready")).is_file()


def test_search_limit_is_total_across_files_and_does_not_use_new_git_flag(local_git, monkeypatch):
    backend, _, initial = local_git
    backend.commit_files("owner/repo", "main", initial,
                         [{"path": f"file-{i}.txt", "content": "needle\n" * 100} for i in range(4)],
                         "many matches", "Tea", "tea@example.test")
    original = subprocess.Popen

    def popen(args, **kwargs):
        assert not any(a.startswith("--max-count") for a in args)
        return original(args, **kwargs)

    # Popen is also used inside subprocess.run during refresh.
    monkeypatch.setattr(subprocess, "Popen", popen)
    result = backend.search_code("owner/repo", "main", "needle", 2)
    assert len(result["items"]) == 2 and result["truncated"]
    exact = backend.search_code("owner/repo", "main", "hello", 1)
    assert len(exact["items"]) == 1 and not exact["truncated"]
    assert backend.search_code("owner/repo", "main", "absent")["items"] == []


def fake_process(monkeypatch, source):
    original = subprocess.Popen
    processes = []

    def popen(_args, **kwargs):
        process = original([sys.executable, "-u", "-c", source], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    return processes


def test_stream_budget_and_large_stderr_reap_child(settings, monkeypatch):
    backend = GitBackend(settings)
    settings.max_response_bytes = 2000
    records = b"a" * 40 + b":unicode space.txt\0" + b"1\0needle\n"
    source = ("import sys,time; sys.stderr.buffer.write(b'x'*100000); sys.stderr.flush(); "
              f"sys.stdout.buffer.write({records!r}+b'a'*100000); sys.stdout.flush(); time.sleep(30)")
    processes = fake_process(monkeypatch, source)
    result = backend._search_stream(Path("unused"), "a" * 40, "needle", 20, "owner/repo")
    assert result["truncated"] and len(result["items"]) == 1
    assert processes[0].poll() is not None


def test_stream_timeout_reaps_child(settings, monkeypatch):
    backend = GitBackend(settings)
    settings.timeout_seconds = 0.05
    processes = fake_process(monkeypatch, "import time; time.sleep(30)")
    start = time.monotonic()
    with pytest.raises(TeaError) as caught:
        backend._search_stream(Path("unused"), "a" * 40, "needle", 20, "owner/repo")
    assert caught.value.code == "git_timeout" and time.monotonic() - start < 5
    assert processes[0].poll() is not None


@pytest.mark.parametrize("email", ["account@example.test", ""])
async def test_service_commits_author_and_committer_are_authenticated_identity(local_git, email):
    backend, remote, initial = local_git
    settings = backend.settings
    api = GiteaClient(settings, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"login": "owner", "full_name": "Ignored display name", "email": email})
    ))
    service = TeaService(settings, api=api, git=backend)
    try:
        result = await service.commit_files("owner/repo", "main", initial,
                                            [{"path": "author.txt", "content": "test"}], "authenticated")
        expected = email or "owner@users.noreply.local"
        assert git("--git-dir", str(remote), "show", "-s", "--format=%an|%ae|%cn|%ce", result["sha"]) == f"owner|{expected}|owner|{expected}"
        blob = await service.create_blob("owner/repo", "local")
        tree = await service.create_tree("owner/repo", [{"path": "local.txt", "sha": blob["sha"]}])
        local = await service.create_commit("owner/repo", tree["sha"], [], "local objects")
        directory = next(settings.cache_dir.glob("*.git"))
        assert git("--git-dir", str(directory), "show", "-s", "--format=%an|%ae|%cn|%ce", local["sha"]) == f"owner|{expected}|owner|{expected}"
        base = backend.commit_files("owner/repo", "main", result["sha"],
                                    [{"path": "base-only.txt", "content": "base"}], "base", "Tea", "t@example.test")
        backend.create_branch("owner/repo", "feature", result["sha"])
        head = backend.commit_files("owner/repo", "feature", result["sha"],
                                    [{"path": "feature.txt", "content": "feature"}], "feature", "Tea", "t@example.test")
        snapshots = await service.compare_commits("owner/repo", base["sha"], head["sha"])
        feature = await service.compare_commits("owner/repo", base["sha"], head["sha"], merge_base=True)
        assert {row["path"] for row in snapshots["files"]} == {"base-only.txt", "feature.txt"}
        assert [row["path"] for row in feature["files"]] == ["feature.txt"]
        assert feature["comparison_base_sha"] == result["sha"]
    finally:
        await service.close()


@pytest.mark.parametrize("detail,porcelain,code,uncertain", [
    (b"protected branch", b"!\tsha:refs/heads/main\t[remote rejected]\n", "protected_branch", False),
    (b"permission denied", b"!\tsha:refs/heads/main\t[remote rejected]\n", "forbidden", False),
    (b"Authentication failed", b"", "authentication_required", False),
    (b"", b"!\tsha:refs/heads/main\t[rejected] (stale info)\n", "conflict", False),
    (b"hook declined", b"!\tsha:refs/heads/main\t[remote rejected]\n", "remote_rejected", False),
    (b"network lost", b"", "git_error", True),
])
def test_push_errors_are_classified_without_returning_sensitive_output(settings, monkeypatch, detail, porcelain, code, uncertain):
    backend = GitBackend(settings)
    secret = b"unit-test-token " + base64.b64encode(b"token:unit-test-token") + b" https://proxy-user:proxy-password@proxy.test"
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args[0], 1, porcelain, detail + b" " + secret)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(TeaError) as caught:
        backend._run(None, ["push", "--porcelain"], writing=True)
    error = caught.value.as_dict()
    assert error["code"] == code and error["result_uncertain"] is uncertain
    assert "unit-test-token" not in str(error) and "proxy-password" not in str(error)
    assert base64.b64encode(b"token:unit-test-token").decode() not in str(error)
    assert len(calls) == 1


def test_invalid_branch_has_specific_error(local_git):
    backend, _, _ = local_git
    with pytest.raises(TeaError) as caught:
        backend.create_branch("owner/repo", "invalid..branch", "main")
    assert caught.value.code == "invalid_input" and not caught.value.uncertain


def test_real_remote_protected_branch_rejection_is_definite(local_git):
    backend, remote, initial = local_git
    hook = remote / "hooks/pre-receive"
    hook.write_bytes(b"#!/bin/sh\necho 'protected branch' >&2\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(TeaError) as caught:
        backend.commit_files("owner/repo", "main", initial,
                             [{"path": "blocked.txt", "content": "blocked"}],
                             "must reject", "Tea", "t@example.test")
    assert caught.value.code == "protected_branch" and not caught.value.uncertain
    assert git("--git-dir", str(remote), "rev-parse", "main") == initial
