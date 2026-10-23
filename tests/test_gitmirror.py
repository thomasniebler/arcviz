import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from arcviz import gitmirror
from arcviz import operations as ops
from arcviz.gitmirror import GitMirror, GitMirrorConfig
from arcviz.server import create_app
from arcviz.store import DiagramStore
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider


def git(repo, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def bare_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)], check=True)
    return remote


@pytest.fixture
def mirrored_store(tmp_path, bare_remote):
    repo = tmp_path / "mirror"
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    git(repo, "remote", "add", "origin", str(bare_remote))
    mirror = GitMirror(GitMirrorConfig(repo_dir=repo, push=True, author_name="Tester", author_email="t@example.org"))
    return DiagramStore(tmp_path / "data", mirror=mirror), mirror, repo


def test_snapshots_become_commits_with_instruction_as_subject(mirrored_store, bare_remote):
    store, mirror, repo = mirrored_store
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    assert git(repo, "log", "--format=%s") == "Initial state"
    assert (repo / "diagrams" / "demo" / "diagram.json").exists()
    assert (repo / "diagrams" / "demo" / "diagram.mmd").read_text().startswith("flowchart TB")

    d = store.load("demo")
    ops.add_node(d, "prod-vnet", kind="vnet", id="prod-vnet", parent="prod-deployment")
    store.save_latest(d)
    store.snapshot(d, label="Füge ein VNet hinzu", source="auto", details='- add_node {"name": "prod-vnet"}')

    subjects = git(repo, "log", "--format=%s").splitlines()
    assert subjects == ["Füge ein VNet hinzu", "Initial state"]
    body = git(repo, "log", "-1", "--format=%b")
    assert '- add_node {"name": "prod-vnet"}' in body and "Arcviz version 2 (auto)" in body
    assert "prod-vnet" in (repo / "diagrams" / "demo" / "diagram.mmd").read_text()
    assert mirror.drain(30)  # pushes run in the background (#113)
    assert mirror.commits == 2 and mirror.last_error is None
    # pushed to the bare remote
    assert git(bare_remote, "log", "--format=%s", "main").splitlines() == subjects

    # a manual snapshot of an unchanged state creates a version but no commit
    store.snapshot(d, label="Manual", source="manual")
    assert mirror.commits == 2 and len(store.list_versions("demo")) == 3

    # restore is recorded too; deleting the diagram removes its files
    store.restore("demo", 1)
    assert git(repo, "log", "-1", "--format=%s").startswith("Restored version 1")
    store.delete("demo")
    assert git(repo, "log", "-1", "--format=%s") == "Delete diagram demo"
    assert not (repo / "diagrams" / "demo").exists()


def test_mirror_initialises_repo_and_reports_push_problems(tmp_path):
    repo = tmp_path / "fresh"
    mirror = GitMirror(GitMirrorConfig(repo_dir=repo, push=True, remote="origin"))
    assert (repo / ".git").exists()
    assert mirror.last_error and "remote 'origin' is not configured" in mirror.last_error

    store = DiagramStore(tmp_path / "data", mirror=mirror)
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))  # commit works, push fails, nothing raises
    assert mirror.drain(30)
    assert mirror.commits == 1 and mirror.last_commit
    assert mirror.last_error and "git push" in mirror.last_error
    status = mirror.status()
    assert status["enabled"] is True and status["push"] is True and status["commits"] == 1


def test_missing_git_binary_degrades_gracefully(tmp_path):
    repo = tmp_path / "mirror"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    mirror = GitMirror(GitMirrorConfig(repo_dir=repo), git_bin="/nonexistent/git")
    store = DiagramStore(tmp_path / "data", mirror=mirror)
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))  # store still works
    assert store.load("demo").title == "Demo"
    assert mirror.commits == 0 and mirror.last_error and "git binary not found" in mirror.last_error


def test_config_from_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ARCVIZ_GIT_DIR", raising=False)
    assert GitMirrorConfig.from_env() is None
    monkeypatch.setenv("ARCVIZ_GIT_DIR", str(tmp_path / "g"))
    monkeypatch.setenv("ARCVIZ_GIT_PUSH", "true")
    monkeypatch.setenv("ARCVIZ_GIT_REMOTE", "upstream")
    cfg = GitMirrorConfig.from_env()
    assert cfg is not None and cfg.push is True and cfg.remote == "upstream" and cfg.repo_dir == tmp_path / "g"


# ---- pushes off the request path (#113) ----------------------------------------------------------------------------


class SlowRemote:
    """`git push` that blocks until released (a slow or unreachable remote); every other git command runs for real."""

    def __init__(self, mirror: GitMirror):
        self.real = mirror._git
        self.release = threading.Event()
        self.started = threading.Event()
        self.pushes: list[dict] = []
        mirror._git = self  # type: ignore[method-assign]

    def __call__(self, *args: str, check: bool = True, timeout: float = gitmirror.GIT_TIMEOUT_SECONDS):
        if args[0] == "push":
            self.pushes.append({"thread": threading.current_thread().name, "timeout": timeout})
            self.started.set()
            assert self.release.wait(30)
            return subprocess.CompletedProcess(args, 0, "", "")
        return self.real(*args, check=check, timeout=timeout)


def test_a_slow_push_delays_neither_commits_nor_a_concurrent_ops_request(mirrored_store):
    store, mirror, repo = mirrored_store
    remote = SlowRemote(mirror)
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    assert remote.started.wait(10)  # the first push hangs ...
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    for title in ("Eins", "Zwei", "Drei"):  # ... and requests that commit go on meanwhile
        response = client.post("/api/diagrams/demo/ops", json={"op": "rename_diagram", "args": {"title": title}})
        assert response.status_code == 200
    assert git(repo, "log", "-1", "--format=%s").startswith("rename_diagram")  # committed in order, at once
    assert len(remote.pushes) == 1 and not remote.release.is_set()
    remote.release.set()
    assert mirror.drain(30)
    # the pushes the three commits asked for while one was running are coalesced into one more
    assert len(remote.pushes) == 2
    assert {p["thread"] for p in remote.pushes} == {"arcviz-git-push"}
    assert {p["timeout"] for p in remote.pushes} == {gitmirror.PUSH_TIMEOUT_SECONDS}
    assert mirror.status()["push_pending"] is False


def test_shutdown_drains_pending_pushes_within_a_bound(mirrored_store):
    store, mirror, _ = mirrored_store
    remote = SlowRemote(mirror)
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    assert remote.started.wait(10)
    started = time.monotonic()
    assert mirror.close(0.2) is False  # a hanging push never holds the shutdown longer than the bound
    assert time.monotonic() - started < 5
    remote.release.set()
    assert mirror.drain(30)
    assert mirror.close(5) is True  # nothing pending: at once
    d = store.load("demo")
    store.snapshot(d.model_copy(update={"title": "Neu"}), "nach dem Schließen")  # a new lifespan pushes again
    assert mirror.drain(30) and len(remote.pushes) == 2


def test_the_app_lifespan_starts_and_drains_the_mirror(mirrored_store, monkeypatch):
    store, mirror, _ = mirrored_store
    closed: list[float] = []
    started: list[bool] = []
    monkeypatch.setattr(mirror, "close", lambda timeout=gitmirror.DRAIN_SECONDS: closed.append(timeout) or True)
    monkeypatch.setattr(mirror, "start", lambda: started.append(True))
    app = create_app(store=store, provider=ScriptedProvider([]))
    assert started == []  # building the app (or the store, as the CLI does) starts nothing
    with TestClient(app):
        assert started == [True] and closed == []
    assert closed == [gitmirror.DRAIN_SECONDS]


def test_commits_left_unpushed_by_a_previous_process_are_pushed_at_start(tmp_path, bare_remote):
    """Coordinator review #4: a shutdown that could not finish its push leaves commits behind; the next process pushes
    them once at start, without waiting for a new commit."""
    repo = tmp_path / "mirror"
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    git(repo, "remote", "add", "origin", str(bare_remote))
    (repo / "x.txt").write_text("x")
    git(repo, "add", "x.txt")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", "left behind")
    mirror = GitMirror(GitMirrorConfig(repo_dir=repo, push=True))
    assert mirror.drain(30) and mirror.pushes == 0 and mirror._worker is None  # never from the constructor (the CLI)
    mirror.start()  # the server's lifespan
    assert mirror.drain(30)
    assert git(bare_remote, "log", "--format=%s", "main") == "left behind"
    empty = tmp_path / "empty"  # nothing committed yet: nothing to push
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    git(empty, "remote", "add", "origin", str(bare_remote))
    fresh = GitMirror(GitMirrorConfig(repo_dir=empty, push=True))
    fresh.start()
    assert fresh.drain(30) and fresh.pushes == 0 and fresh.last_error is None


def test_last_error_reports_the_last_push_and_a_commit_does_not_clear_it(mirrored_store):
    """Coordinator review #5: with push on, `last_error` belongs to the push worker: set by a failed push, cleared only
    by a successful one; a commit in between leaves it alone."""
    store, mirror, _ = mirrored_store
    remote = SlowRemote(mirror)
    outcomes = iter([1, 0])
    real_call = remote.__call__

    def push_once_failing(*args, check=True, timeout=gitmirror.GIT_TIMEOUT_SECONDS):
        if args[0] == "push":
            return subprocess.CompletedProcess(args, next(outcomes), "", "rejected")
        return real_call(*args, check=check, timeout=timeout)

    mirror._git = push_once_failing  # type: ignore[method-assign]
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    assert mirror.drain(30) and mirror.last_error and "git push" in mirror.last_error
    d = store.load("demo")
    gate = threading.Event()
    real_push = mirror._push

    def held_push():
        gate.wait(30)
        real_push()

    mirror._push = held_push  # type: ignore[method-assign]
    store.snapshot(d.model_copy(update={"title": "Neu"}), "zwei", "auto")  # committed; its push is held
    assert mirror.last_error and "git push" in mirror.last_error  # the commit did not clear it
    gate.set()
    assert mirror.drain(30) and mirror.last_error is None  # the successful push did


def test_a_push_never_clears_a_commit_error(mirrored_store, monkeypatch):
    """Round 2 #4: commit and push errors are kept apart; a successful push clears only its own, so a commit that
    failed while it ran stays reported."""
    store, mirror, _ = mirrored_store
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    assert mirror.drain(30)
    gate = threading.Event()
    real_push = mirror._push

    def held_push():
        gate.wait(30)
        real_push()

    monkeypatch.setattr(mirror, "_push", held_push)
    d = store.load("demo")
    store.snapshot(d.model_copy(update={"title": "Zwei"}), "zwei", "auto")  # its push is held
    monkeypatch.setattr(mirror, "_write_files", lambda diagram: (_ for _ in ()).throw(gitmirror.GitMirrorError("disk")))
    store.snapshot(d.model_copy(update={"title": "Drei"}), "drei", "auto")  # this commit fails meanwhile
    gate.set()
    assert mirror.drain(30)
    status = mirror.status()
    assert status["push_error"] is None and status["commit_error"] == "disk"
    assert mirror.last_error == "disk"
