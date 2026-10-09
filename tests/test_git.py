import subprocess

import pytest

from jasminetea_mcp.errors import TeaError
from jasminetea_mcp.git_backend import GitBackend


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True).stdout.decode().strip()


@pytest.fixture
def local_git(tmp_path, settings):
    work = tmp_path / "work"
    remote = tmp_path / "remote.git"
    work.mkdir()
    git("init", "--initial-branch=main", cwd=work)
    git("config", "user.name", "Tea Test", cwd=work)
    git("config", "user.email", "tea@example.test", cwd=work)
    (work / "中文 空格.txt").write_text("hello needle\n", encoding="utf-8")
    (work / "binary.bin").write_bytes(b"\x00\xff")
    git("add", ".", cwd=work)
    git("commit", "-m", "initial", cwd=work)
    initial = git("rev-parse", "HEAD", cwd=work)
    git("clone", "--bare", "--no-hardlinks", str(work), str(remote))
    backend = GitBackend(settings)
    backend._remote_url = lambda repo: str(remote)
    return backend, remote, initial


def test_multi_file_commit_and_stale_lease(local_git):
    backend, remote, initial = local_git
    result = backend.commit_files(
        "owner/repo",
        "main",
        initial,
        [{"path": "中文 空格.txt", "content": "updated\n"}, {"path": "dir/new.txt", "content": "new\n"}],
        "two files",
        "Tea Test",
        "tea@example.test",
    )
    assert git("--git-dir", str(remote), "rev-parse", "main") == result["sha"]
    assert git("--git-dir", str(remote), "show", "main:dir/new.txt") == "new"
    with pytest.raises(TeaError) as caught:
        backend.commit_files(
            "owner/repo", "main", initial, [{"path": "x", "content": "bad"}], "stale", "Tea", "t@example.test"
        )
    assert caught.value.code == "conflict"
    assert git("--git-dir", str(remote), "rev-parse", "main") == result["sha"]


def test_object_lifecycle_and_fast_forward_only(local_git):
    backend, remote, initial = local_git
    blob = backend.create_blob("owner/repo", "hello")
    tree = backend.create_tree("owner/repo", [{"path": "file.txt", "sha": blob["sha"]}], initial)
    commit = backend.create_commit("owner/repo", tree["sha"], [initial], "objects", "Tea", "t@example.test")
    assert backend.read_object("owner/repo", blob["sha"], "blob")["data"]["content"] == "aGVsbG8="
    assert "file.txt" in backend.commit_diff("owner/repo", commit["sha"])["text"]
    assert commit["published"] is False
    assert git("--git-dir", str(remote), "rev-parse", "main") == initial
    backend.update_ref("owner/repo", "main", commit["sha"], initial)
    assert git("--git-dir", str(remote), "show", "main:file.txt") == "hello"
    with pytest.raises(TeaError):
        backend.update_ref("owner/repo", "main", initial, commit["sha"])
    assert git("--git-dir", str(remote), "rev-parse", "main") == commit["sha"]


def test_git_search_and_binary_diff(local_git):
    backend, _, initial = local_git
    hits = backend.search_code("owner/repo", "main", "needle")
    assert hits["items"][0]["path"] == "中文 空格.txt"
    assert hits["items"][0]["line"] == 1
    updated = backend.commit_files(
        "owner/repo",
        "main",
        initial,
        [{"path": "binary.bin", "content": "AP4=", "encoding": "base64"}],
        "binary change",
        "Tea",
        "t@example.test",
    )
    diff = backend.diff("owner/repo", initial, updated["sha"])
    assert diff["files"][0]["additions"] is None
    assert backend.search_commits("owner/repo", "main", "binary")["items"][0]["sha"] == updated["sha"]


@pytest.mark.parametrize("path", ["../escape", "/absolute", "dir/../escape", "a\\b", "a//b"])
def test_git_tree_path_validation(local_git, path):
    backend, _, initial = local_git
    with pytest.raises(TeaError):
        backend.create_tree("owner/repo", [{"path": path, "content": "x"}], initial)


def test_git_env_and_remote_url_never_contain_token_in_config(local_git, settings):
    backend, _, _ = local_git
    backend.resolve("owner/repo", "main")
    configs = list(settings.cache_dir.glob("*.git/config"))
    assert len(configs) == 1
    assert "unit-test-token" not in configs[0].read_text()
    env = backend._environment()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_VALUE_1"] == ""
    assert env["GIT_CONFIG_KEY_3"] == "http.followRedirects" and env["GIT_CONFIG_VALUE_3"] == "false"


def test_create_branch_from_exact_commit_rejects_existing(local_git):
    backend, remote, initial = local_git
    result = backend.create_branch("owner/repo", "test/new", initial)
    assert result["sha"] == initial
    assert git("--git-dir", str(remote), "rev-parse", "test/new") == initial
    with pytest.raises(TeaError) as caught:
        backend.create_branch("owner/repo", "test/new", initial)
    assert caught.value.code == "conflict"


def test_cleanup_lease_preserves_unexpected_commits(local_git):
    backend, remote, initial = local_git
    backend.create_branch("owner/repo", "test/cleanup", initial)
    updated = backend.commit_files(
        "owner/repo",
        "test/cleanup",
        initial,
        [{"path": "another.txt", "content": "preserve"}],
        "another change",
        "Tea",
        "t@example.test",
    )
    with pytest.raises(TeaError):
        backend.delete_branch("owner/repo", "test/cleanup", initial)
    assert git("--git-dir", str(remote), "rev-parse", "test/cleanup") == updated["sha"]
    backend.delete_branch("owner/repo", "test/cleanup", updated["sha"])
    assert not git("--git-dir", str(remote), "show-ref", "--heads").endswith("refs/heads/test/cleanup")


def test_pr_merge_base_excludes_unrelated_base_changes(local_git):
    backend, _, initial = local_git
    backend.create_branch("owner/repo", "feature", initial)
    base = backend.commit_files(
        "owner/repo",
        "main",
        initial,
        [{"path": "main-only.txt", "content": "base only"}],
        "base advance",
        "Tea",
        "t@example.test",
    )
    head = backend.commit_files(
        "owner/repo",
        "feature",
        initial,
        [{"path": "feature.txt", "content": "feature"}],
        "feature change",
        "Tea",
        "t@example.test",
    )
    assert backend.diff("owner/repo", base["sha"], head["sha"], "main-only.txt")["files"]
    pr_diff = backend.diff("owner/repo", base["sha"], head["sha"], "main-only.txt", True)
    assert not pr_diff["files"] and pr_diff["comparison_base_sha"] == initial


def test_single_file_rename_keeps_previous_path_and_patch(local_git):
    backend, _, initial = local_git
    head = backend.commit_files(
        "owner/repo",
        "main",
        initial,
        [{"path": "中文 空格.txt", "delete": True}, {"path": "renamed.txt", "content": "hello needle\n"}],
        "rename",
        "Tea",
        "t@example.test",
    )
    diff = backend.diff("owner/repo", initial, head["sha"], "renamed.txt")
    assert diff["files"][0]["previous_path"] == "中文 空格.txt"
    assert "rename from" in diff["diff"]
