"""Download the pinned Mermaid build into arcviz/static/vendor so the UI works without a CDN.

Usage: python scripts/vendor_mermaid.py [version]

Without an argument the version in ``arcviz/static/vendor/VERSION`` is re-fetched (the committed pin);
with an argument that version is fetched and becomes the new pin.

Fetches ``mermaid.min.js`` (single-file IIFE build exposing ``window.mermaid``) and the licence from
jsdelivr, verifies that the file looks like the requested version, and writes ``VERSION``. Commit the
result; the Docker image and the wheel ship the static directory as is.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parents[1] / "arcviz" / "static" / "vendor"
VERSION_FILE = VENDOR_DIR / "VERSION"
FALLBACK_VERSION = "11.17.2"  # only used when no VERSION file has been committed yet


def pinned_version() -> str:
    """The committed pin: whatever VERSION says, so a plain re-run never downgrades a later upgrade."""
    if VERSION_FILE.exists():
        pinned = VERSION_FILE.read_text(encoding="utf-8").strip()
        if pinned:
            return pinned
    return FALLBACK_VERSION


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https host
        return response.read()


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else pinned_version()
    base = f"https://cdn.jsdelivr.net/npm/mermaid@{version}"
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)

    bundle = fetch(f"{base}/dist/mermaid.min.js")
    if f'version:"{version}"'.encode() not in bundle and version.encode() not in bundle[:200_000]:
        raise SystemExit(f"Downloaded bundle does not mention version {version}; refusing to write it.")
    (VENDOR_DIR / "mermaid.min.js").write_bytes(bundle)
    (VENDOR_DIR / "mermaid.LICENSE").write_bytes(fetch(f"{base}/LICENSE"))
    (VENDOR_DIR / "VERSION").write_text(version + "\n", encoding="utf-8")
    print(f"vendored mermaid {version} ({len(bundle) / 1_000_000:.1f} MB) into {VENDOR_DIR}")


if __name__ == "__main__":
    main()
