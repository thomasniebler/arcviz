"""Optional Git mirror of the diagram history.

The JSON file store stays the primary record. When ``ARCVIZ_GIT_DIR`` is set, every snapshot is
additionally committed to a Git repository as ``diagrams/<id>/diagram.json`` (the model) and
``diagrams/<id>/diagram.mmd`` (readable Mermaid source, for diffs), with the snapshot label (the
user's instruction) as commit subject and the applied operations as body. Commits run on the request path, in
order, under one lock. Pushing is optional and runs on a background worker (#113, ADR-0151): a commit only asks for a
push, pushes asked for while one runs are coalesced into the next one, each push is bounded by
``PUSH_TIMEOUT_SECONDS``, and :meth:`GitMirror.close` (the app's shutdown) waits at most a bound for a pending one.
Failures are logged and exposed through :meth:`GitMirror.status`.

Git is driven through ``subprocess`` so no extra dependency is needed; the binary must be on PATH.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import render
from .model import Diagram

log = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 120  # any git command but push (they run on the request path; local and fast)
PUSH_TIMEOUT_SECONDS = 60  # one push on the background worker; a hanging remote never holds it longer (#113)
DRAIN_SECONDS = 10  # what the app's shutdown waits for a pending push (#113)
WORKER_NAME = "arcviz-git-push"

# credentials in a URL: `scheme://user:token@host` and `?private_token=…`; `git@host:path` has no secret
_USERINFO = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@'\"]+@")
_QUERY = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s?'\"]*)\?[^\s'\"]*")


def redacted(text: str) -> str:
    """`text` with the user info and query of every URL in it replaced by `***`: `ARCVIZ_GIT_REMOTE` and git's output
    may carry a token (`https://user:token@host/repo.git`). Everything that logs or reports a remote or git's output
    goes through this."""
    return _QUERY.sub(r"\1?***", _USERINFO.sub(r"\1***@", text))


class GitMirrorError(Exception):
    pass


@dataclass
class GitMirrorConfig:
    repo_dir: Path
    push: bool = False
    remote: str = "origin"
    author_name: str = "Arcviz"
    author_email: str = "arcviz@localhost"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GitMirrorConfig | None:
        env = os.environ if env is None else env
        repo_dir = env.get("ARCVIZ_GIT_DIR")
        if not repo_dir:
            return None
        return cls(
            repo_dir=Path(repo_dir),
            push=env.get("ARCVIZ_GIT_PUSH", "0").strip().lower() in ("1", "true", "yes"),
            remote=env.get("ARCVIZ_GIT_REMOTE", "origin").strip() or "origin",
            author_name=env.get("ARCVIZ_GIT_AUTHOR_NAME", "Arcviz").strip() or "Arcviz",
            author_email=env.get("ARCVIZ_GIT_AUTHOR_EMAIL", "arcviz@localhost").strip() or "arcviz@localhost",
        )


class GitMirror:
    """Commits diagram snapshots to a Git working tree; thread-safe, never raises from record/remove."""

    def __init__(self, config: GitMirrorConfig, git_bin: str = "git"):
        self.config = config
        self.git_bin = shutil.which(git_bin) or git_bin
        self._lock = threading.RLock()  # serialises commits (and removals)
        self._push_state = threading.Condition()  # guards the push worker's fields below
        self._push_pending = False  # a commit asked for a push that has not started yet
        self._pushing = False
        self._closing = False
        self._worker: threading.Thread | None = None
        self.pushes = 0
        self.last_commit: str | None = None
        self._error_lock = threading.Lock()  # commits and the push worker both report (#113 review)
        self.commit_error: str | None = None  # the last commit's (or removal's) failure; a successful one clears it
        self.push_error: str | None = None  # the last push's failure; only a successful push clears it
        self.commits = 0
        self._ensure_repo()

    # ---- git plumbing -----------------------------------------------------

    def _git(
        self, *args: str, check: bool = True, timeout: float = GIT_TIMEOUT_SECONDS
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": self.config.author_name,
            "GIT_AUTHOR_EMAIL": self.config.author_email,
            "GIT_COMMITTER_NAME": self.config.author_name,
            "GIT_COMMITTER_EMAIL": self.config.author_email,
            "GIT_TERMINAL_PROMPT": "0",
        }
        try:
            result = subprocess.run(
                [self.git_bin, *args],
                cwd=self.config.repo_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise GitMirrorError(f"git binary not found ({self.git_bin}).") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitMirrorError(f"git {args[0]} timed out.") from exc
        if check and result.returncode != 0:
            raise GitMirrorError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
        return result

    def _ensure_repo(self) -> None:
        self.config.repo_dir.mkdir(parents=True, exist_ok=True)
        if not (self.config.repo_dir / ".git").exists():
            self._git("init", "-q")
            log.info("Initialised Git mirror in %s", self.config.repo_dir)
        if self.config.push:
            remotes = self._git("remote").stdout.split()
            if self.config.remote not in remotes:
                self._report(
                    "push",
                    redacted(
                        f"Push enabled but remote '{self.config.remote}' is not configured in {self.config.repo_dir}."
                    ),
                )
                log.warning(self.push_error)

    def start(self) -> None:
        """Push once what a previous process left unpushed (its shutdown waits only DRAIN_SECONDS), without waiting for
        a new commit. Called by the server's lifespan, never by the constructor: a CLI that builds the store pushes
        nothing and starts no thread (#113 review)."""
        if not self.config.push:
            return
        try:
            if self.config.remote not in self._git("remote").stdout.split():
                return
            if self._git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0:
                return  # nothing committed yet
        except GitMirrorError as exc:
            self._report("push", redacted(str(exc)))
            return
        self._request_push()

    # ---- files ------------------------------------------------------------

    def _diagram_dir(self, diagram_id: str) -> Path:
        return self.config.repo_dir / "diagrams" / diagram_id

    def _write_files(self, diagram: Diagram) -> None:
        directory = self._diagram_dir(diagram.id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = diagram.model_dump(mode="json", exclude={"updated_at"})
        (directory / "diagram.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
        )
        rendering = render.payload(diagram)
        (directory / render.file_name(diagram)).write_text(rendering[rendering["renderer"]], encoding="utf-8")

    # ---- public API -------------------------------------------------------

    def record(self, diagram: Diagram, subject: str, body: str = "") -> str | None:
        """Write the diagram files and commit them. Returns the commit sha, or None if nothing changed."""
        with self._lock:
            try:
                self._write_files(diagram)
                self._git("add", "-A", "--", f"diagrams/{diagram.id}")
                if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
                    return None  # nothing new to commit (e.g. a manual snapshot of an unchanged state)
                return self._commit(subject, body)
            except GitMirrorError as exc:
                # git's output (redacted) for GET /api/config, never in a log record (ADR-0112)
                self._report("commit", redacted(str(exc)))
                log.warning(
                    "Git mirror: committing failed (%s); see git.last_error in GET /api/config", type(exc).__name__
                )
                return None

    def remove(self, diagram_id: str) -> str | None:
        with self._lock:
            try:
                if not self._diagram_dir(diagram_id).exists():
                    return None
                self._git("rm", "-r", "-q", "--ignore-unmatch", "--", f"diagrams/{diagram_id}")
                if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
                    return None
                return self._commit(f"Delete diagram {diagram_id}")
            except GitMirrorError as exc:
                self._report("commit", redacted(str(exc)))
                log.warning(
                    "Git mirror: removing failed (%s); see git.last_error in GET /api/config", type(exc).__name__
                )
                return None

    def _commit(self, subject: str, body: str = "") -> str:
        subject = (subject.strip().splitlines() or ["Arcviz change"])[0][:200]
        args = ["commit", "-q", "-m", subject]
        if body.strip():
            args += ["-m", body.strip()]
        self._git(*args)
        sha = self._git("rev-parse", "HEAD").stdout.strip()
        self.last_commit = sha
        self.commits += 1
        self._report("commit", None)  # a push error stays until a push succeeds
        if self.config.push:
            self._request_push()
        return sha

    # ---- the push worker (#113) -------------------------------------------------------------------------------

    def _request_push(self) -> None:
        """Ask for a push of HEAD; never waits for it. Several requests before the worker gets to them are one push
        (each push sends everything committed so far). The worker starts on the first request (again after `close`)."""
        with self._push_state:
            self._push_pending = True
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._push_loop, name=WORKER_NAME, daemon=True)
                self._worker.start()
            self._push_state.notify_all()

    def _push_loop(self) -> None:
        while True:
            with self._push_state:
                while not self._push_pending and not self._closing:
                    self._push_state.wait()
                if not self._push_pending:  # closing, and nothing left to push
                    self._worker = None
                    self._push_state.notify_all()
                    return
                self._push_pending, self._pushing = False, True
            try:
                self._push()
            except GitMirrorError as exc:  # the push timed out, or git is gone
                self._report("push", redacted(str(exc)))
                log.warning("Git mirror: push failed (%s); see git.last_error in GET /api/config", type(exc).__name__)
            except Exception as exc:  # never let the worker die: the next request starts a push again
                self._report("push", f"git push failed: {type(exc).__name__}")
                log.warning("Git mirror: push failed (%s); see git.last_error in GET /api/config", type(exc).__name__)
            finally:
                with self._push_state:
                    self._pushing = False
                    self.pushes += 1
                    self._push_state.notify_all()

    def drain(self, timeout: float) -> bool:
        """Wait until no push is pending or running, at most `timeout` seconds; whether that was reached."""
        deadline = time.monotonic() + timeout
        with self._push_state:
            while self._push_pending or self._pushing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._push_state.wait(remaining)
        return True

    def close(self, timeout: float = DRAIN_SECONDS) -> bool:
        """Shutdown: wait at most `timeout` for pending pushes, then stop the worker. False when a push was still
        pending or running (it is logged; the next commit pushes everything, the worker thread is a daemon). A later
        commit starts a new worker (a new app lifespan in the same process)."""
        deadline = time.monotonic() + timeout
        drained = self.drain(timeout)
        with self._push_state:
            self._closing = True
            self._push_state.notify_all()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(max(0.0, deadline - time.monotonic()))
        with self._push_state:
            self._closing = False
        if not drained:
            log.warning("Git mirror: a push was still pending at shutdown; the next commit pushes it")
        return drained

    def _push(self) -> None:
        result = self._git("push", "-q", "-u", self.config.remote, "HEAD", check=False, timeout=PUSH_TIMEOUT_SECONDS)
        if result.returncode == 0:
            self._report("push", None)
            return
        output = (result.stderr or result.stdout).strip()
        self._report("push", redacted(f"git push to '{self.config.remote}' failed: {output}"))
        log.warning(
            "Git mirror: push to '%s' failed (exit %d); see git.last_error in GET /api/config",
            redacted(self.config.remote),
            result.returncode,
        )

    @property
    def last_error(self) -> str | None:
        """The commit error and the push error, whichever are set (each cleared only by its own kind of success)."""
        with self._error_lock:
            return "; ".join(e for e in (self.commit_error, self.push_error) if e) or None

    def _report(self, kind: str, error: str | None) -> None:
        """Set (or clear) the commit or the push error, under the lock: the request threads commit, the worker pushes,
        and a push never clears a commit's failure (#113 review)."""
        with self._error_lock:
            if kind == "commit":
                self.commit_error = error
            else:
                self.push_error = error

    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "dir": str(self.config.repo_dir),
            "push": self.config.push,
            "remote": redacted(self.config.remote) if self.config.push else None,
            "commits": self.commits,
            "push_pending": self._push_pending or self._pushing,
            "last_commit": self.last_commit,
            "last_error": self.last_error,
            "commit_error": self.commit_error,
            "push_error": self.push_error,
        }
