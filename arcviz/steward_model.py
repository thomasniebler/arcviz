"""The project steward's configuration and per-project state (spec: docs/architecture/specs/project-steward.md,
ADR-0204, ADR-0205, ADR-0206).

Pure: the environment, the keyed document `steward` of one project (opt-in switch, what the last run saw, the day's run
count, the findings people dismissed, the lease of the process that checks it) and the bounds. Reading and writing the
document is `services/steward.py`; the check itself is `steward.py`.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from .envconfig import Env

DOCUMENT = "steward"  # kind of the keyed document, one per project (key = the project id)
CLIENT = "steward"  # `StoredProposal.client` and the author id of what the steward proposes
AUTHOR_NAME = "Steward"
MAX_DISMISSED = 500  # remembered discarded findings per project, the oldest are forgotten first
KEEP_DAYS = 7  # days of run counts kept
GROUPS = ("fix", "next_step")  # `ProposalItem.group`
MAX_TOKENS_PER_RUN = 200_000  # model tokens of one run (main and verifier calls together); the rest is not asked


@dataclass(frozen=True)
class StewardConfig:
    enabled: bool = False
    debounce_seconds: int = 300  # a project is checked this long after its last change
    sweep_seconds: int = 3600  # and at the latest this long after its last check (changes other replicas made)
    max_runs_per_day: int = 4  # per project and UTC day, manual runs included
    max_items: int = 15  # items of one run
    verifier_provider: str | None = None
    verifier_model: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> StewardConfig:
        env = Env(environ)
        config = cls(
            enabled=env.flag("ARCVIZ_STEWARD"),
            debounce_seconds=env.integer("ARCVIZ_STEWARD_DEBOUNCE_SECONDS", 300, minimum=1),
            sweep_seconds=env.integer("ARCVIZ_STEWARD_SWEEP_SECONDS", 3600, minimum=60),
            max_runs_per_day=env.integer("ARCVIZ_STEWARD_MAX_RUNS_PER_DAY", 4, minimum=1),
            max_items=env.integer("ARCVIZ_STEWARD_MAX_ITEMS", 15, minimum=1, maximum=60),
            verifier_provider=env.text("ARCVIZ_VERIFIER_PROVIDER") or None,
            verifier_model=env.text("ARCVIZ_VERIFIER_MODEL") or None,
        )
        if (config.verifier_provider is None) != (config.verifier_model is None):
            env.problem("ARCVIZ_VERIFIER_PROVIDER and ARCVIZ_VERIFIER_MODEL must be set together.")
        env.check()
        return config

    @property
    def verifier_configured(self) -> bool:
        return self.verifier_provider is not None and self.verifier_model is not None


def verifier_environ(config: StewardConfig, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment that makes `ProviderConfig.from_env` describe the verifier: the verifier's provider and model on
    top of the usual variables, so keys and endpoints of its vendor are found where they always are."""
    base = dict(os.environ if environ is None else environ)
    if config.verifier_provider and config.verifier_model:
        base["ARCVIZ_PROVIDER"] = config.verifier_provider
        base["ARCVIZ_MODEL"] = config.verifier_model
    return base


class LastRun(BaseModel):
    at: str
    outcome: str  # unchanged | clean | proposed | blocked | disabled | failed
    items: int = 0
    verified: int = 0  # LLM candidates the verifier confirmed
    dropped: int = 0  # LLM candidates dropped (invalid, not confirmed, dismissed)
    tokens: int = 0


class Lease(BaseModel):
    holder: str
    until: float  # wall-clock seconds (time.time()); another process may take an expired lease over


class StewardBox(BaseModel):
    """The document of one project."""

    incarnation: str = ""  # of the project it belongs to (`projects.incarnation_of`); another one: ignore the box
    enabled: bool = False  # opt-in per project, set by the owner
    last_fingerprint: str = ""  # of what the last run saw
    last_run: LastRun | None = None
    runs: dict[str, int] = Field(default_factory=dict)  # UTC day -> runs
    dismissed: list[str] = Field(default_factory=list)  # `finding` keys of items a person discarded
    lease: Lease | None = None
    dirty_at: str | None = None  # the first change not checked yet (persisted so a restart does not forget it)

    def runs_today(self, now: datetime) -> int:
        return self.runs.get(now.astimezone(UTC).date().isoformat(), 0)

    def count_run(self, now: datetime) -> None:
        today = now.astimezone(UTC).date()
        self.runs[today.isoformat()] = self.runs_today(now) + 1
        keep = {(today - timedelta(days=n)).isoformat() for n in range(KEEP_DAYS)}
        self.runs = {day: n for day, n in self.runs.items() if day in keep}

    def dismiss(self, findings: list[str]) -> None:
        """Remember discarded findings, bounded: the oldest are forgotten first."""
        fresh = [f for f in dict.fromkeys(findings) if f and f not in self.dismissed]
        self.dismissed = [*self.dismissed, *fresh][-MAX_DISMISSED:]


def finding_key(kind: str, *parts: str) -> str:
    """A stable, short key of a finding: its kind (`rule:<id>`, `llm:fix`, `llm:next`) and what it is about, hashed (the
    key is kept in stored proposals and the dismissal list, never a message or an id of somebody's content)."""
    body = "\x1f".join([kind, *parts])
    return f"{kind}:{hashlib.sha1(body.encode()).hexdigest()[:12]}"
