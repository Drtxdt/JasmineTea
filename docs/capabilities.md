# 89 项能力对照

基线：2026-10-09 当前连接器工具清单及 bdgit 的公开 Swagger 快照。

表中的支持状态只表示接口/实现存在，不能替代带 Token 的平台联调。源数据随 Python 包分发。

验证：API 对照由 `tests/test_service.py` 检查路径和请求 JSON Schema；
Git 行为由 `tests/test_git.py` 的真实 bare 仓库测试验证；MCP 由 `tests/test_mcp.py`、
`tests/test_stdio.py` 覆盖。真实平台和客户端运行情况单列在 `validation.md`。

| 原工具 | 实现方式 | JasmineTea 工具 | 实例支持 | 差异 |
|---|---|---|---|---|
| `add_comment_to_issue` | API | `add_comment_to_issue` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `add_issue_assignees` | 等价映射 | `add_issue_assignees` | available | Read/merge/PATCH; Gitea has no atomic assignee add API. |
| `add_issue_labels` | API | `add_issue_labels` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `add_reaction_to_issue_comment` | API | `add_reaction_to_issue_comment` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `add_reaction_to_pr` | API | `add_reaction_to_pr` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `add_reaction_to_pr_review_comment` | 等价映射 | `add_reaction_to_issue_comment` | available | Uses native comment IDs; subtype permissions are enforced by the platform. |
| `add_review_to_pr` | API | `add_review_to_pr` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `compare_commits` | Git | `compare_commits` | available | Resolved SHAs, bounded diff and per-file stats. |
| `convert_pull_request_to_draft` | 平台缺失 | — | unsupported | No native draft lifecycle API; editing WIP titles is not presented as an equivalent draft operation. |
| `create_blob` | Git | `create_blob` | available | Objects live in the isolated local bare cache until update_ref publishes a reachable commit. |
| `create_branch` | API | `create_branch` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `create_commit` | Git | `create_commit` | available | Explicit author identity; local until published. |
| `create_file` | API | `create_file` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `create_issue` | API | `create_issue` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `create_pull_request` | API | `create_pull_request` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `create_tree` | Git | `create_tree` | available | Builds local trees; supports blobs, symlinks, executable modes and submodule entries. |
| `delete_file` | API | `delete_file` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `dismiss_pull_request_review` | API | `dismiss_pull_request_review` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `download_user_content` | 平台缺失 | — | unsupported | GitHub private-user-images CDN authentication has no equivalent in this instance API. |
| `download_workflow_artifact` | 平台缺失 | — | unsupported | The recorded bdgit API exposes no Actions workflow/artifact endpoints. |
| `enable_auto_merge` | API | `enable_auto_merge` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `fetch` | API | `fetch` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `fetch_blob` | API | `fetch_blob` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `fetch_commit` | API | `fetch_commit` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `fetch_commit_workflow_runs` | 平台缺失 | — | unsupported | The recorded bdgit API exposes no Actions workflow endpoints. |
| `fetch_file` | 等价映射 | `read_file` | available | Bounded UTF-8 text and line ranges; binary files explicitly rejected. |
| `fetch_issue` | API | `fetch_issue` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `fetch_issue_comments` | API | `fetch_issue_comments` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `fetch_pr` | API | `fetch_pr` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `fetch_pr_comments` | API | `fetch_pr_comments` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `fetch_pr_file_patch` | Git | `fetch_pr_file_patch` | available | Resolved base/head comparison with a validated file path. |
| `fetch_pr_patch` | 等价映射 | `get_pr_diff` | available | Use format=patch for the native PR patch. |
| `fetch_workflow_job_logs` | 平台缺失 | — | unsupported | The recorded bdgit API exposes no Actions job/log endpoints. |
| `fetch_workflow_job_steps` | 平台缺失 | — | unsupported | The recorded bdgit API exposes no Actions job/step endpoints. |
| `fetch_workflow_run_artifacts` | 平台缺失 | — | unsupported | The recorded bdgit API exposes no Actions artifact endpoints. |
| `fetch_workflow_run_jobs` | 平台缺失 | — | unsupported | The recorded bdgit API exposes no Actions job endpoints. |
| `get_commit_combined_status` | API | `get_commit_combined_status` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `get_issue_comment_reactions` | API | `get_issue_comment_reactions` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `get_pr_diff` | API | `get_pr_diff` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `get_pr_info` | 等价映射 | `fetch_pr` | available | PR metadata is separate from diff retrieval. |
| `get_pr_reactions` | API | `get_pr_reactions` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `get_pr_review_comment_reactions` | 等价映射 | `get_issue_comment_reactions` | available | Uses native comment IDs; subtype permissions are enforced by the platform. |
| `get_profile` | API | `get_profile` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `get_repo` | API | `get_repo` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `get_repo_collaborator_permission` | API | `get_repo_collaborator_permission` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `get_user_login` | 等价映射 | `get_profile` | available | Read the login field of the authenticated profile. |
| `get_users_recent_prs_in_repo` | API | `get_users_recent_prs_in_repo` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `label_pr` | 等价映射 | `add_issue_labels` | available | Gitea labels are addressed by existing numeric IDs. |
| `list_installations` | 平台缺失 | — | unsupported | GitHub App installations do not exist in personal-token Gitea authentication. |
| `list_installed_accounts` | 平台缺失 | — | unsupported | GitHub App installed accounts do not exist in personal-token Gitea authentication. |
| `list_pr_changed_filenames` | API | `list_pr_changed_filenames` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `list_pull_request_review_threads` | 等价映射 | `list_review_comments` | available | Read inline review comments; resolved-thread state is not available. |
| `list_pull_request_reviews` | API | `list_pull_request_reviews` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `list_recent_issues` | API | `list_recent_issues` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `list_repositories` | API | `list_repositories` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `list_repositories_by_affiliation` | API | `list_repositories_by_affiliation` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `list_repositories_by_installation` | 等价映射 | `list_repositories` | available | Token-scoped accessible repositories; no installation selector. |
| `list_user_org_memberships` | 等价映射 | `list_user_teams` | available | Team memberships include organization/permission data; GitHub membership states are absent. |
| `list_user_orgs` | API | `list_user_orgs` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `lock_issue_conversation` | 等价映射 | `lock_issue_conversation` | unsupported | Available only if the live API advertises an is_locked mutation field; absent on the recorded bdgit schema. |
| `mark_pull_request_ready_for_review` | 平台缺失 | — | unsupported | No native draft lifecycle API. |
| `merge_pull_request` | API | `merge_pull_request` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `remove_issue_assignees` | 等价映射 | `remove_issue_assignees` | available | Read/filter/PATCH; concurrent assignee-set changes can race. |
| `remove_issue_label` | API | `remove_issue_label` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `remove_pull_request_reviewers` | API | `remove_pull_request_reviewers` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `remove_reaction_from_issue_comment` | API | `remove_reaction_from_issue_comment` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `remove_reaction_from_pr` | API | `remove_reaction_from_pr` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `remove_reaction_from_pr_review_comment` | 等价映射 | `remove_reaction_from_issue_comment` | available | Uses native comment IDs; subtype permissions are enforced by the platform. |
| `reply_to_review_comment` | 平台缺失 | — | unsupported | No native inline-review reply API is advertised. |
| `request_pull_request_reviewers` | API | `request_pull_request_reviewers` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `rerun_failed_workflow_run_jobs` | 平台缺失 | — | unsupported | The recorded bdgit API exposes no Actions retry endpoints. |
| `rerun_workflow_job` | 平台缺失 | — | unsupported | The recorded bdgit API exposes no Actions retry endpoints. |
| `resolve_review_thread` | 平台缺失 | — | unsupported | No native resolved-review-thread mutation API is advertised. |
| `search` | Git | `search` | available | Literal case-sensitive search within one repository/ref, not global indexed GitHub search. |
| `search_branches` | API | `search_branches` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `search_commits` | Git | `search_commits` | available | Literal commit-message search within one repository/ref. |
| `search_installed_repositories_streaming` | 等价映射 | `search_repositories` | available | Paginated Gitea search; no streaming installation search. |
| `search_installed_repositories_v2` | 等价映射 | `search_repositories` | available | Token-scoped native Gitea repository search. |
| `search_issues` | API | `search_issues` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `search_prs` | API | `search_prs` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `search_repositories` | API | `search_repositories` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `unlock_issue_conversation` | 等价映射 | `unlock_issue_conversation` | unsupported | Available only if the live API advertises an is_locked mutation field; absent on the recorded bdgit schema. |
| `unresolve_review_thread` | 平台缺失 | — | unsupported | No native resolved-review-thread mutation API is advertised. |
| `update_file` | API | `update_file` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `update_issue` | API | `update_issue` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `update_issue_comment` | API | `update_issue_comment` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `update_pull_request` | API | `update_pull_request` | available | Native Gitea REST operation; explicit pagination and platform permissions apply. |
| `update_ref` | Git | `update_ref` | available | Atomic expected-SHA lease; fast-forward only. |
| `update_review_comment` | 平台缺失 | — | unsupported | No native inline-review edit API is advertised. |
