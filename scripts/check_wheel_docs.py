"""Check that built wheels contain the documentation site (ADR-0116).

Usage: python scripts/check_wheel_docs.py dist/*.whl
"""

from __future__ import annotations

import sys
import zipfile

REQUIRED = ("arcviz/docs_site/index.html", "arcviz/docs_site/architecture/arc42/index.html")


def problems(wheel: str) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    return [f"{wheel}: {name} is missing" for name in REQUIRED if name not in names]


def main(argv: list[str]) -> int:
    if not argv:
        print("check_wheel_docs: no wheel given", file=sys.stderr)
        return 2
    found = [problem for wheel in argv for problem in problems(wheel)]
    for problem in found:
        print(f"check_wheel_docs: {problem}", file=sys.stderr)
    if not found:
        print(f"check_wheel_docs: {len(argv)} wheel(s) contain the documentation site")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
