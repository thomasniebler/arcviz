"""Every Python install uses the committed lock; base images are pinned by digest (#116; operations.md §3, ADR-0109/0110)."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
WORKFLOWS = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / ".github").rglob("*.yml")}
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def test_the_lock_is_committed_and_covers_the_project():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    names = {package["name"] for package in lock["package"]}
    assert "arcviz-agent" in names
    for required in ("fastapi", "anthropic", "openai", "azure-identity", "azure-cosmos", "zensical"):
        assert required in names, required
    assert "mike" not in names  # no versioned Pages publishing any more (ADR-0116)


def test_the_docs_toolchain_lives_in_dependency_groups():
    groups = PYPROJECT["dependency-groups"]
    assert any(d.startswith("zensical") for d in groups["docs"]) and set(groups) == {"docs"}
    assert not (ROOT / "requirements-docs.txt").exists()


def test_the_build_backend_is_pinned_exactly():
    assert all("==" in requirement for requirement in PYPROJECT["build-system"]["requires"])


@pytest.mark.parametrize(
    "line", [line for line in DOCKERFILE.splitlines() if re.match(r"(FROM|COPY --from=)\S*", line)]
)
def test_every_base_image_is_pinned_by_version_and_digest(line):
    image = line.split()[1].removeprefix("--from=") if line.startswith("COPY") else line.split()[1]
    if "/" not in image and ":" not in image:
        return  # a stage of this Dockerfile (COPY --from=docs)
    assert re.search(r":\d+\.\d+\.\d+[^@]*@sha256:[0-9a-f]{64}$", image), image


def test_every_python_install_uses_the_lock():
    sources = {"Dockerfile": DOCKERFILE, "Makefile": MAKEFILE, **WORKFLOWS}
    for name, text in sources.items():
        assert "requirements-docs.txt" not in text, name
        assert "uv pip install" not in text and "pip install -r" not in text, name
        for match in re.finditer(r"uv sync[^\n]*", text):
            assert "--locked" in match.group(0) or "--frozen" in match.group(0), (name, match.group(0))
    assert "uv sync --locked" in DOCKERFILE and "uv sync --locked" in MAKEFILE
    assert "uv sync --locked" in WORKFLOWS["action.yml"]


def test_the_image_and_ci_use_the_same_uv():
    image = re.search(r"ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)@", DOCKERFILE)
    action = re.search(r'version: "(\d+\.\d+\.\d+)"', WORKFLOWS["action.yml"])
    assert image and action and image.group(1) == action.group(1)
    # the release builds the wheel with the same uv
    assert re.findall(r'version: "(\d+\.\d+\.\d+)"', WORKFLOWS["release-please.yml"]) == [image.group(1)]


def test_pull_requests_test_the_python_of_the_image():
    """A Dockerfile-only bump (Dependabot) skips pytest in CI, so this check runs on the next code change at the latest."""
    images = set(re.findall(r"FROM python:(\d+\.\d+)\.\d+", DOCKERFILE))
    action = re.search(r'python-version:\n(?:.*\n)*?\s+default: "(\d+\.\d+)"', WORKFLOWS["action.yml"])
    assert len(images) == 1 and action and action.group(1) in images
    assert f"Unit tests (py{action.group(1)})" in WORKFLOWS["ci.yml"]


def test_the_release_attaches_hashed_requirements():
    release = WORKFLOWS["release-please.yml"]
    assert "uv export --frozen" in release and "--no-emit-project" in release and "requirements.txt" in release


def test_every_action_is_pinned_by_commit_sha_with_its_version():
    """Dependabot bumps SHA pins and their version comments (owner's decision in M9)."""
    for name, text in WORKFLOWS.items():
        for match in re.finditer(r"^\s*(?:- )?uses: (\S+)(.*)$", text, re.MULTILINE):
            action, rest = match.groups()
            if action.startswith("./"):
                continue  # the repository's own composite action
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), (name, action)
            assert re.match(r"\s+# v\d+\.\d+\.\d+$", rest), (name, action, rest)


def test_dependabot_groups_every_ecosystem_monthly():
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    for ecosystem in ("uv", "docker", "github-actions"):
        assert f'package-ecosystem: "{ecosystem}"' in config, ecosystem
    assert config.count('interval: "monthly"') == 3 and config.count("groups:") == 3
    assert "/.github/actions/setup" in config  # the composite action is scanned too
    assert 'prefix: "build"' in config and 'prefix: "ci"' in config  # Conventional PR titles


def test_the_release_bumps_the_project_version_in_the_lock():
    """release-please must update the project's entry in uv.lock too, or `uv sync --locked` fails on the release."""
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    extra = config["packages"]["."]["extra-files"]
    locks = [f for f in extra if isinstance(f, dict) and f.get("path") == "uv.lock"]
    assert locks and locks[0]["type"] == "toml"
    assert "arcviz-agent" in locks[0]["jsonpath"] and locks[0]["jsonpath"].endswith(".version")


def test_a_feature_bumps_the_minor_version_before_1_0():
    """Owner decision (after 0.4.1): `feat` bumps the middle number, not the patch number."""
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    assert config.get("bump-minor-pre-major") is True
    assert not config.get("bump-patch-for-minor-pre-major")
