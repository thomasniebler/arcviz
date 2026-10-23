"""Hatch build hook: every sdist and wheel contains the documentation site (ADR-0116).

The site is a build output (`arcviz/docs_site/`, git-ignored) that `make docs-bundle` writes; a release without it
would serve a 404 under /docs. Editable installs (development, CI) are exempt: they run from the checkout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ImportError:  # imported by the tests, where hatchling (the build backend) is not installed
    BuildHookInterface = object  # type: ignore[assignment,misc]

DOCS_INDEX = Path("arcviz") / "docs_site" / "index.html"
MISSING = (
    "The documentation site arcviz/docs_site/ is missing; every build contains it (ADR-0116). "
    "Build it first: `make docs-bundle` (or `uv sync --group docs && python scripts/build_docs_bundle.py`)."
)


def missing_docs(root: str | Path) -> str | None:
    """The error message when the site has not been built under `root`, else None."""
    return None if (Path(root) / DOCS_INDEX).is_file() else MISSING


class DocsSiteHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable":
            return
        problem = missing_docs(self.root)
        if problem is not None:
            raise RuntimeError(problem)
