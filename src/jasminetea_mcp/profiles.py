"""Stable task profiles; authorization remains independent of discovery."""

CORE = frozenset(
    """get_capabilities get_profile list_repositories get_repo list_files read_file
list_commits fetch_commit get_diff compare_commits search_code search_branches""".split()
)
REVIEW = CORE | frozenset(
    """search_prs fetch_pr get_pr_diff fetch_pr_file_patch
list_pr_changed_filenames list_pull_request_reviews list_review_comments fetch_pr_comments
get_commit_combined_status search_issues fetch_issue fetch_issue_comments""".split()
)
SAFE_WRITES = (
    "create_branch",
    "commit_files",
    "create_pull_request",
    "create_issue",
    "add_comment_to_issue",
    "add_review_to_pr",
)
BRANCH_WRITES = frozenset(
    (
        "create_branch",
        "commit_files",
        "create_file",
        "update_file",
        "delete_file",
        "update_ref",
        "delete_branch",
    )
)
MERGES = frozenset(("merge_pull_request", "enable_auto_merge"))
WRITE_OPERATIONS = frozenset(
    [
        "add_comment_to_issue",
        "add_issue_assignees",
        "add_issue_labels",
        "add_reaction_to_issue_comment",
        "add_reaction_to_pr",
        "add_review_to_pr",
        "commit_files",
        "create_blob",
        "create_branch",
        "create_commit",
        "create_file",
        "create_issue",
        "create_pull_request",
        "create_tree",
        "delete_branch",
        "delete_file",
        "dismiss_pull_request_review",
        "enable_auto_merge",
        "lock_issue_conversation",
        "merge_pull_request",
        "remove_issue_assignees",
        "remove_issue_label",
        "remove_pull_request_reviewers",
        "remove_reaction_from_issue_comment",
        "remove_reaction_from_pr",
        "request_pull_request_reviewers",
        "unlock_issue_conversation",
        "update_file",
        "update_issue",
        "update_issue_comment",
        "update_pull_request",
        "update_ref",
    ]
)
