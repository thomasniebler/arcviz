"""scripts/ci_scope.py: which CI steps a pull request needs."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("ci_scope", Path(__file__).parent.parent / "scripts" / "ci_scope.py")
assert _spec and _spec.loader
ci_scope = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_scope)


def needed(files: list[str]) -> set[str]:
    return {name for name, on in ci_scope.scope(files).items() if on}


def test_docs_only_change_skips_python_steps():
    assert needed(["docs/guide/elements.md", "CONTRIBUTING.md"]) == {"docs"}


def test_code_change_skips_docs_build():
    assert needed(["arcviz/rules.py", "tests/test_rules.py"]) == {"python", "docker"}


def test_frontend_change_skips_docker():
    assert needed(["arcviz/static/app.js", "tests/e2e/smoke.mjs"]) == {"python", "e2e"}


def test_the_real_ids_smoke_run_is_gated_on_frontend_and_e2e_paths():
    """#196: the second Playwright run (ARCVIZ_E2E_REAL_IDS=1) only when the UI, the e2e test or the id scheme
    changes; it is a step of the one CI job, never a job of its own."""
    for path in ("arcviz/static/navigator.js", "tests/e2e/smoke.mjs", "tests/e2e/server.py", "arcviz/server.py"):
        assert "e2e" in needed([path]), path
    for path in ("arcviz/ports.py", "arcviz/services/projects.py", "arcviz/services/diagrams.py"):
        assert "e2e" in needed([path]), path
    # where ids are created and placed, and the agents that create diagrams (coordinator review of #199)
    for path in (
        "arcviz/store/file.py",
        "arcviz/store/cosmos.py",
        "arcviz/projects.py",
        "arcviz/agent.py",
        "arcviz/project_agent.py",
        "arcviz/agent_model.py",
        "arcviz/agent_types.py",
    ):
        assert "e2e" in needed([path]), path
    for path in ("arcviz/rules.py", "arcviz/mermaid.py", "tests/test_agent.py", "docs/guide/index.md"):
        assert "e2e" not in needed([path]), path
    workflow = (Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "steps.scope.outputs.e2e == 'true'" in workflow and 'ARCVIZ_E2E_REAL_IDS: "1"' in workflow
    assert workflow.count("runs-on:") == 1


def test_image_inputs_run_docker():
    for path in ("Dockerfile", ".dockerignore", "pyproject.toml", "arcviz/server.py"):
        assert "docker" in needed([path]), path


def test_store_change_runs_cosmos_emulator():
    assert "cosmos" in needed(["arcviz/store/cosmos.py"])
    assert "cosmos" in needed(["tests/store_contract.py"])
    assert "cosmos" not in needed(["arcviz/agent.py"])


def test_ci_change_runs_everything_but_the_matrix():
    assert needed([".github/workflows/ci.yml"]) == set(ci_scope.SCOPES)


def test_full_run_enables_everything():
    assert all(ci_scope.scope([], full=True).values())


def test_unrelated_change_needs_nothing():
    assert needed(["README.md", "LICENSE"]) == set()


def test_main_prints_github_output_lines(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", ["docs/index.md\n", "\n"])
    assert ci_scope.main([]) == 0
    assert "docs=true" in capsys.readouterr().out.splitlines()


def test_docs_bundle_build_inputs_run_docker_but_pages_do_not():
    """The image builds the documentation site (ADR-0042): its build setup is an image input, its pages are not."""
    for path in ("zensical.toml", "uv.lock", "scripts/build_docs_bundle.py"):
        assert "docker" in needed([path]), path
    assert "docker" not in needed(["docs/architecture/arc42/01-introduction-and-goals.md"])


def test_architecture_diagrams_and_arc42_pages_run_pytest():
    """tests/test_architecture_docs.py checks the renders, the snippets in the pages and the code references."""
    for path in (
        "docs/architecture/diagrams/c4-context.arcviz.json",
        "docs/architecture/diagrams/c4-kinds.json",
        "docs/architecture/arc42/05-building-block-view.md",
    ):
        assert {"python", "docs"} <= needed([path]), path
    assert "python" not in needed(["docs/guide/elements.md"])


def test_the_lock_runs_everything_that_installs_from_it():
    """uv.lock pins the test environment, the image, the Cosmos SDK and the docs toolchain (ADR-0109)."""
    assert needed(["uv.lock"]) == {"python", "docs", "docker", "cosmos"}
    assert "docs" in needed(["pyproject.toml"])  # the docs toolchain is a dependency group there


def test_the_wheel_build_inputs_run_the_docker_scope():
    """The CI step that checks the wheel for the documentation site runs with the docker scope (ADR-0116)."""
    for path in ("hatch_build.py", "scripts/check_wheel_docs.py"):
        assert "docker" in needed([path]), path
