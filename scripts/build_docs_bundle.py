"""Build the documentation site that the app serves under /docs (arcviz/docs_site/).

Usage: python scripts/build_docs_bundle.py [--out DIR]

Reads ``zensical.toml``, derives an offline configuration from it and runs ``zensical build --strict``
with that configuration. The derived configuration differs from the published one only where serving
from the app needs it (design: docs/architecture/specs/docs-in-app.md, ADR-0042):

- ``site_dir`` is ``arcviz/docs_site`` (or ``--out``), so the Docker image and the wheel ship it;
- no ``site_url`` and no mike version selector (the bundle is one version, served under any prefix);
- no ``repo_url``/``repo_name``/``edit_uri``: the theme would fetch repository facts from api.github.com
  on every page, and edit links are useless in a deployed app;
- no Google Fonts (``theme.font = false``), so pages render without network access;
- Mermaid comes from the app's vendored build (``/static/vendor/mermaid.min.js``, referenced relative
  to the site root as ``../static/vendor/mermaid.min.js``), not from unpkg.

The derived file is written as JSON with a ``.yml`` suffix (JSON is YAML, and Zensical reads
mkdocs.yml-style files). Zensical requires ``site_dir`` inside the directory of the configuration
file, so the file is written to the repository root as ``.zensical-bundle.yml`` and removed afterwards.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "arcviz" / "docs_site"
# Relative to the site root (/docs/): the app serves its static files next to it under /static/.
MERMAID_SCRIPT = "../static/vendor/mermaid.min.js"


def bundle_config(project: dict[str, Any], out: Path) -> dict[str, Any]:
    """The published Zensical configuration, adapted for serving from the app (see module docstring)."""
    config = json.loads(json.dumps(project))  # deep copy
    config["site_dir"] = out.relative_to(ROOT).as_posix()
    for key in ("site_url", "repo_url", "repo_name", "edit_uri"):
        config.pop(key, None)
    extra = config.setdefault("extra", {})
    extra.pop("version", None)
    config.setdefault("theme", {})["font"] = False
    scripts = list(config.get("extra_javascript", []))
    if MERMAID_SCRIPT not in scripts:
        scripts.insert(0, MERMAID_SCRIPT)
    config["extra_javascript"] = scripts
    return config


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory (default: arcviz/docs_site)")
    args = parser.parse_args(argv)
    with open(ROOT / "zensical.toml", "rb") as f:
        project = tomllib.load(f)["project"]
    out = args.out.resolve()
    if not out.is_relative_to(ROOT):
        print(f"build_docs_bundle: --out must be inside {ROOT}", file=sys.stderr)
        return 2
    config_file = ROOT / ".zensical-bundle.yml"
    config_file.write_text(json.dumps(bundle_config(project, out), indent=2), encoding="utf-8")
    zensical = Path(sys.executable).with_name("zensical")
    command = [str(zensical) if zensical.exists() else "zensical", "build", "--clean", "--strict"]
    try:
        completed = subprocess.run([*command, "-f", str(config_file)], cwd=ROOT, check=False)
    finally:
        config_file.unlink(missing_ok=True)
    if completed.returncode != 0:
        return completed.returncode
    if not (out / "index.html").is_file():
        print(f"build_docs_bundle: no index.html in {out}", file=sys.stderr)
        return 1
    print(f"build_docs_bundle: documentation written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
