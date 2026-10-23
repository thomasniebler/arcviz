"""Check that a pull request title follows Conventional Commits (squash merges use it as the commit).

Usage::

    PR_TITLE="feat(agent): ..." python scripts/check_pr_title.py            # exit code only
    PR_TITLE=... GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo HEAD_SHA=... \\
        python scripts/check_pr_title.py --status                            # also post a commit status

With ``--status`` the result is posted as the commit status ``PR title`` on the PR's head commit and the
script exits 0 for a bad title (the status carries the failure). The CI job does this on every push
and ``.github/workflows/pr-title.yml`` on title edits; the newest status wins, so a title edit updates
the same required check without re-running CI.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

TYPES = ("feat", "fix", "perf", "refactor", "docs", "test", "build", "ci", "chore", "revert")
PATTERN = re.compile(rf"^({'|'.join(TYPES)})(\([\w./-]+\))?!?: \S.*$")
CONTEXT = "PR title"


def check(title: str) -> bool:
    return bool(PATTERN.match(title))


def post_status(ok: bool, *, token: str, repository: str, sha: str) -> None:
    """Set the ``PR title`` commit status on ``sha`` (needs ``statuses: write``)."""
    body = {
        "state": "success" if ok else "failure",
        "context": CONTEXT,
        "description": "Conventional Commits" if ok else "Expected '<type>(<optional scope>): <subject>'",
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/statuses/{sha}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30):
        pass


def main(argv: list[str]) -> int:
    title = os.environ.get("PR_TITLE", "")
    ok = check(title)
    if ok:
        print(f"PR title OK: {title}")
    else:
        print(
            f"::error::PR title {title!r} does not follow Conventional Commits: "
            f"'<type>(<optional scope>): <subject>' with type one of {', '.join(TYPES)}."
        )
    if "--status" in argv:
        post_status(
            ok, token=os.environ["GITHUB_TOKEN"], repository=os.environ["GITHUB_REPOSITORY"], sha=os.environ["HEAD_SHA"]
        )
        return 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
