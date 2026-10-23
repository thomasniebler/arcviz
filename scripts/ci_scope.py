"""Decide which CI steps a pull request needs, from the files it changes.

Usage in CI (pull requests)::

    git diff --name-only HEAD^1 HEAD | python scripts/ci_scope.py >> "$GITHUB_OUTPUT"

and ``python scripts/ci_scope.py --all`` for scheduled and manual runs. Prints one ``name=true|false``
line per scope. Lint, format and type check are cheap and always run; the scopes gate the rest.
Filtering happens on steps inside the single CI job rather than with workflow-level ``paths:``, so the
required status check never stays pending on a skipped workflow.
"""

from __future__ import annotations

import re
import sys

# A scope is needed when any changed file matches its pattern.
SCOPES: dict[str, re.Pattern[str]] = {
    # pytest and the browser smoke test (the frontend lives in arcviz/static/).
    # Also the architecture docs: tests/test_architecture_docs.py checks the diagram renders, the pages that
    # include them and the code references.
    "python": re.compile(r"^(arcviz/|tests/|scripts/|pyproject\.toml$|uv\.lock$|docs/architecture/(diagrams|arc42)/)"),
    # Documentation build: the site, its config, the files it includes as snippets, and its toolchain (the
    # dependency groups in pyproject.toml, pinned by uv.lock).
    "docs": re.compile(r"^(docs/|zensical\.toml$|pyproject\.toml$|uv\.lock$|CHANGELOG\.md$|CONTRIBUTING\.md$)"),
    # Docker build + smoke test, and the wheel check (ADR-0116): the inputs of the image and the wheel. Static
    # frontend files only ride along (the smoke test checks /api/config and /docs/), so frontend-only changes skip
    # it, and so do changes to the documentation pages (the strict docs build checks them); the docs build setup
    # does not.
    "docker": re.compile(
        r"^(Dockerfile$|\.dockerignore$|pyproject\.toml$|uv\.lock$|arcviz/(?!static/)"
        r"|zensical\.toml$|scripts/build_docs_bundle\.py$|scripts/check_wheel_docs\.py$|hatch_build\.py$)"
    ),
    # The second browser smoke run with the production id generator (ARCVIZ_E2E_REAL_IDS=1, #196): the UI, the e2e
    # test, the routes, and everything that creates or places ids: services, stores, projects, the agents.
    "e2e": re.compile(
        r"^(arcviz/static/|tests/e2e/|arcviz/server\.py$|arcviz/ports\.py$|arcviz/services/|arcviz/store/"
        r"|arcviz/projects\.py$|arcviz/agent\.py$|arcviz/project_agent\.py$|arcviz/agent_[a-z_]+\.py$)"
    ),
    # Store contract against the Cosmos DB emulator (non-blocking): the store, its tests and the SDK
    # pin. The same contract runs against the in-process fake with pytest on every Python change.
    "cosmos": re.compile(
        r"^(arcviz/store/|tests/(test_store|store_contract|cosmos_fake)|scripts/wait_for_cosmos\.py$|pyproject\.toml$"
        r"|uv\.lock$)"
    ),
}

# Changes to CI itself run everything.
_CI = re.compile(r"^(\.github/|scripts/ci_scope\.py$)")


def scope(files: list[str], *, full: bool = False) -> dict[str, bool]:
    """Map scope name to whether the run needs it. ``full`` is the matrix flag (non-PR runs)."""
    run_all = full or any(_CI.match(f) for f in files)
    result = {name: run_all or any(pattern.match(f) for f in files) for name, pattern in SCOPES.items()}
    result["full"] = full
    return result


def main(argv: list[str]) -> int:
    full = "--all" in argv
    files = [] if full else [line.strip() for line in sys.stdin if line.strip()]
    for name, needed in scope(files, full=full).items():
        print(f"{name}={'true' if needed else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
