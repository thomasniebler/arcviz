"""scripts/check_pr_title.py: Conventional Commits PR titles."""

import importlib.util
import json
from pathlib import Path

import pytest

_path = Path(__file__).parent.parent / "scripts" / "check_pr_title.py"
_spec = importlib.util.spec_from_file_location("check_pr_title", _path)
assert _spec and _spec.loader
check_pr_title = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_pr_title)


@pytest.mark.parametrize(
    "title",
    [
        "feat: add a thing",
        "fix(agent): handle disconnects",
        "ci!: drop the push trigger",
        "chore(main): release 0.1.4",
        "docs(spec/build): describe CI scopes",
    ],
)
def test_valid_titles(title):
    assert check_pr_title.check(title)


@pytest.mark.parametrize(
    "title", ["", "Add a thing", "feature: add", "feat:missing space", "feat: ", "Feat: capitalised type"]
)
def test_invalid_titles(title):
    assert not check_pr_title.check(title)


def test_main_reports_error(monkeypatch, capsys):
    monkeypatch.setenv("PR_TITLE", "nope")
    assert check_pr_title.main([]) == 1
    assert "::error::" in capsys.readouterr().out


@pytest.mark.parametrize(("title", "state"), [("fix: ok", "success"), ("nope", "failure")])
def test_status_mode_posts_commit_status_and_exits_zero(monkeypatch, title, state):
    sent = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        sent.append(request)
        return _Response()

    monkeypatch.setattr(check_pr_title.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("PR_TITLE", title)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("HEAD_SHA", "abc")
    assert check_pr_title.main(["--status"]) == 0
    (request,) = sent
    assert request.full_url == "https://api.github.com/repos/o/r/statuses/abc"
    body = json.loads(request.data)
    assert body["state"] == state and body["context"] == "PR title"
