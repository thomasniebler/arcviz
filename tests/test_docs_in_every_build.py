"""The documentation is built into every artifact; no GitHub Pages (#188; operations.md §6, ADR-0116)."""

from __future__ import annotations

import importlib.util
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load("hatch_build.py", "hatch_build")
check = load("scripts/check_wheel_docs.py", "check_wheel_docs")


def test_the_build_hook_refuses_a_build_without_the_site(tmp_path):
    assert "make docs-bundle" in hook.missing_docs(tmp_path)
    (tmp_path / "arcviz" / "docs_site").mkdir(parents=True)
    (tmp_path / "arcviz" / "docs_site" / "index.html").write_text("<html></html>")
    assert hook.missing_docs(tmp_path) is None


def test_the_hook_is_configured_and_editable_installs_are_exempt():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["hatch"]["build"]["hooks"]["custom"]["path"] == "hatch_build.py"
    assert "arcviz/docs_site/" in pyproject["tool"]["hatch"]["build"]["artifacts"]
    source = (ROOT / "hatch_build.py").read_text(encoding="utf-8")
    assert 'version == "editable"' in source
    assert "hatch_build.py" in (ROOT / "Dockerfile").read_text(encoding="utf-8")  # the image builds a wheel


def wheel(tmp_path, names: list[str]) -> str:
    path = tmp_path / "arcviz_agent-0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
    return str(path)


def test_the_wheel_check(tmp_path, capsys):
    good = wheel(tmp_path, ["arcviz/__init__.py", *check.REQUIRED])
    assert check.main([good]) == 0
    bad = wheel(tmp_path, ["arcviz/__init__.py"])
    assert check.main([bad]) == 1 and "docs_site/index.html is missing" in capsys.readouterr().err
    assert check.main([]) == 2


def test_ci_and_the_release_verify_the_wheel():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8")
    for workflow in (ci, release):
        assert "scripts/check_wheel_docs.py" in workflow and "build_docs_bundle.py" in workflow


@pytest.mark.parametrize("path", [".github/workflows/docs.yml"])
def test_no_pages_publishing(path):
    assert not (ROOT / path).exists()
    release = (ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8")
    assert "docs.yml" not in release and "pages" not in release.lower()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "docs-publish" not in pyproject["dependency-groups"] and "uv" not in pyproject.get("tool", {})
    zensical = (ROOT / "zensical.toml").read_text(encoding="utf-8")
    assert "site_url" not in zensical and "mike" not in zensical
    assert "github.io" not in (ROOT / "README.md").read_text(encoding="utf-8")


def test_make_run_builds_the_site_when_missing():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    run = makefile.split("\nrun:\n", 1)[1].split("\n\n", 1)[0]
    assert "docs_site/index.html" in run and "docs-bundle" in run
