"""Opt-in live acceptance. Operates only on the dedicated disposable repository."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from jasminetea_mcp.config import load_settings
from jasminetea_mcp.errors import TeaError
from jasminetea_mcp.service import TeaService

TEST_REPO = "hndx202426010302/JasmineTea-test"


async def run(config: str):
    settings = load_settings(config)
    if settings.base_url != "https://bdgit.educoder.net":
        raise TeaError("invalid_input", "Live acceptance is restricted to the supplied bdgit test instance.")
    settings.authorize_write(TEST_REPO)
    settings.token()
    service = TeaService(settings)
    identity = uuid4().hex[:12]
    baseline = f"jasminetea-test/base-{identity}"
    feature = f"jasminetea-test/feature-{identity}"
    title = f"[jasminetea-test:{identity}] acceptance"
    ledger = {
        "repo": TEST_REPO,
        "identity": identity,
        "branches": [],
        "branch_shas": {},
        "issue": None,
        "pr": None,
        "passed": False,
        "cleanup": [],
        "checks": [],
    }
    artifact = Path("artifacts") / f"live-{identity}.json"
    artifact.parent.mkdir(exist_ok=True)

    def save():
        artifact.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        user = await service.get_profile()
        metadata = (await service.get_repo(TEST_REPO))["data"]
        branch = metadata.get("default_branch")
        if metadata.get("empty"):
            tree = await service.create_tree(TEST_REPO, [{"path": "README.md", "content": title + "\n"}])
            seed = await service.create_commit(
                TEST_REPO, tree["sha"], [], title + " seed",
                user.get("full_name") or user["login"],
                user.get("email") or f"{user['login']}@users.noreply.local",
            )
            initial_sha = seed["sha"]
            ledger["checks"].append("empty_repository_seed")
        elif branch:
            initial_sha = await service._git(TEST_REPO, "resolve", branch)
        else:
            raise TeaError("empty_repository", "Repository default branch is unknown.")
        await service.create_branch(TEST_REPO, baseline, initial_sha)
        ledger["branches"].append(baseline)
        ledger["branch_shas"][baseline] = initial_sha
        save()
        await service.create_branch(TEST_REPO, feature, initial_sha)
        ledger["branches"].append(feature)
        ledger["branch_shas"][feature] = initial_sha
        save()
        base_sha = await service._git(TEST_REPO, "resolve", feature)
        commit = await service.commit_files(
            TEST_REPO,
            feature,
            base_sha,
            [
                {"path": f"jasminetea-fixture/{identity}/中文 空格.txt", "content": f"{identity}\n"},
                {"path": f"jasminetea-fixture/{identity}/second.txt", "content": "multi-file transaction\n"},
            ],
            title,
            user.get("full_name") or user["login"],
            user.get("email") or f"{user['login']}@users.noreply.local",
        )
        ledger["checks"].append("multi_file_commit")
        ledger["branch_shas"][feature] = commit["sha"]
        save()
        read = await service.read_file(TEST_REPO, f"jasminetea-fixture/{identity}/中文 空格.txt", feature)
        if identity not in read["content"]:
            raise TeaError("acceptance_failed", "Published file did not match the test content.")
        ledger["checks"].append("unicode_file_read")
        issue = (await service.create_issue(TEST_REPO, title, "Disposable JasmineTea API acceptance."))[
            "data"
        ]
        ledger["issue"] = issue["number"]
        save()
        await service.add_comment_to_issue(TEST_REPO, issue["number"], title)
        pr = (await service.create_pull_request(TEST_REPO, title, feature, baseline, title))["data"]
        ledger["pr"] = pr["number"]
        save()
        await service.add_comment_to_issue(TEST_REPO, pr["number"], title)
        await service.add_review_to_pr(TEST_REPO, pr["number"], "COMMENT", title, commit["sha"])
        await service.get_pr_diff(TEST_REPO, pr["number"])
        await service.merge_pull_request(TEST_REPO, pr["number"], commit["sha"])
        merged = (await service.fetch_pr(TEST_REPO, pr["number"]))["data"]
        if not merged.get("merged"):
            raise TeaError("acceptance_failed", "PR merge was not confirmed by the platform.")
        ledger["branch_shas"][baseline] = merged.get("merge_commit_sha")
        ledger["checks"].append("issue_pr_review_merge")
        ledger["passed"] = True
    except TeaError as error:
        ledger["error"] = error.as_dict()
    finally:
        # Cleanup is restricted to IDs returned during this invocation and the exact generated prefix.
        for kind in ("pr", "issue"):
            index = ledger[kind]
            if index:
                try:
                    data = (
                        await (
                            service.fetch_pr(TEST_REPO, index)
                            if kind == "pr"
                            else service.fetch_issue(TEST_REPO, index)
                        )
                    )["data"]
                    if data.get("title") != title:
                        raise TeaError("cleanup_refused", "Object marker changed; preserved the object.")
                    if data.get("state") == "open":
                        if kind == "pr":
                            await service.update_pull_request(TEST_REPO, index, {"state": "closed"})
                        else:
                            await service.update_issue(TEST_REPO, index, {"state": "closed"})
                    ledger["cleanup"].append({"kind": kind, "id": index, "closed": True})
                except TeaError as error:
                    ledger["cleanup"].append({"kind": kind, "id": index, "error": error.as_dict()})
        for name in reversed(ledger["branches"]):
            try:
                current_default = (await service.get_repo(TEST_REPO))["data"].get("default_branch")
                if name == current_default:
                    ledger["cleanup"].append({"branch": name, "retained": "platform_default_branch"})
                    continue
                expected = ledger["branch_shas"][name]
                if not expected:
                    raise TeaError("cleanup_refused", "Unknown post-merge branch SHA; preserved branch.")
                await service._git(TEST_REPO, "delete_branch", name, expected)
                ledger["cleanup"].append({"branch": name, "deleted": True})
            except TeaError as error:
                ledger["cleanup"].append({"branch": name, "error": error.as_dict()})
        ledger["workflow_passed"] = ledger["passed"]
        ledger["passed"] = ledger["passed"] and all("error" not in item for item in ledger["cleanup"])
        save()
        await service.close()
    print(
        json.dumps(
            {"passed": ledger["passed"], "record": str(artifact), "cleanup": ledger["cleanup"]},
            ensure_ascii=False,
        )
    )
    return ledger["passed"] and all("error" not in item for item in ledger["cleanup"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="examples/test-repository.toml")
    args = parser.parse_args()
    try:
        raise SystemExit(0 if asyncio.run(run(args.config)) else 1)
    except TeaError as error:
        print(json.dumps(error.as_dict()))
        raise SystemExit(1) from None
