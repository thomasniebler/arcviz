"""The project steward: a background check that proposes fixes and next steps (spec: docs/architecture/specs/project-steward.md,
ADR-0204, ADR-0205, ADR-0206).

After a project changed, `Steward.run` reads it, runs the deterministic review rules, asks the main model for fixes the rules
cannot see and for next steps that serve the vision, the drivers or a decided goal, lets a **different model** verify every
candidate the main model authored against the objects it cites, and stores what survives as one pending project proposal
in the proposal panel. It runs in the system context and may only read the project, replace its own pending proposal and
write its own bookkeeping document: nothing it makes touches the project until a person applies it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import project_review, projects, requirement_model, telemetry
from .decision_model import decision_lines
from .i18n import _
from .live import LiveHub
from .llm import LLMProvider, ToolSpec
from .model import fingerprint
from .operations import OperationError
from .project_model import active, vision_lines
from .project_review import ProjectFinding, ProjectSnapshot
from .proposals import KEY_RE, MAX_CITES, PROPOSABLE_OPERATIONS
from .services import ProposalError, ServiceRoot, SystemServices
from .steward_model import CLIENT, MAX_TOKENS_PER_RUN, StewardBox, StewardConfig, finding_key

logger = logging.getLogger(__name__)

# What a model may propose: everything a person may, except deleting from the shared model (ADR-0206).
LLM_OPERATIONS = tuple(op for op in PROPOSABLE_OPERATIONS if op != "remove_relation")
MAX_CANDIDATES = 10  # the main model's candidates that are verified in one run
MAX_ITEMS_PER_CANDIDATE = 6
MAX_TITLE = 160
MAX_REASON = 300

FINDINGS_SYSTEM_PROMPT = """You are the steward of an architecture project in Arcviz. You get the project: a digest of its diagrams, its shared model, \
the vision, the architecture drivers with their scenarios, the accepted decisions (binding) and the open rule findings. \
Answer with one call of submit_steward_findings.

Propose only what is worth a person's attention, and propose nothing rather than something weak. Two kinds:
- "fix": something wrong, missing or inconsistent that the rule findings do not already name (a driver with no scenario, a \
decision that contradicts another, a component that is missing from the model, a relation that is wrong).
- "next_step": the next useful piece of work towards the product vision or a driver (a scenario that would make a quality \
goal measurable, a decision that should be written down, an element or relation the model needs). A next step must name \
what it serves in `serves`: a driver id such as "d2" or a vision goal such as "vision:g1". Without that it is dropped.

Rules:
- Every candidate names the model objects it is about in `cites` (ids that appear in the project: elements, relations, \
drivers, scenarios, decisions, stakeholders, requirements, diagrams, or "vision"). A candidate whose ids do not exist is dropped.
- A candidate carries the operations that would carry it out in `items` (at most 6): the operations of the tool schema, \
with exactly their arguments. Later items may refer to an earlier item's new id as "@key". Never delete anything.
- A person decides: new decisions are always drafts, the vision is only ever proposed, never invented: propose a vision only \
when the project has none and the description or drivers state clearly what the product is for.
- Never contradict an accepted decision; never repeat a rule finding.
- Write titles and rationales in German, short and concrete (one or two sentences)."""

VERDICT_SYSTEM_PROMPT = """You verify a proposal that another model made for an architecture project. You get the proposal, the model \
objects it cites (as stored), the vision and a digest of the project. Your task is to find the reason it is wrong, not \
supported by the cited objects, or contradicts an accepted decision. Answer with one call of submit_verdict:
- "supported": the cited objects really show what the proposal claims, and the operations would do what it says.
- "unsupported": a claim is not backed by the cited objects, an id or fact is invented, or the operations do something else.
- "unclear": you cannot tell from what you were given.
Be strict: when in doubt, "unclear". The reason is one or two German sentences naming the facts you checked."""


def _items_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": MAX_ITEMS_PER_CANDIDATE,
        "items": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Local label, 1 to 16 characters a-z, 0-9 and _."},
                "op": {"type": "string", "enum": list(LLM_OPERATIONS)},
                "arguments": {"type": "object", "description": "Exactly the arguments of the operation."},
            },
            "required": ["key", "op", "arguments"],
        },
    }


FINDINGS_TOOL = ToolSpec(
    "submit_steward_findings",
    "Submit the fixes and next steps for the project (an empty list when there is nothing worth proposing).",
    {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "maxItems": MAX_CANDIDATES,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["fix", "next_step"]},
                        "title": {"type": "string"},
                        "rationale": {"type": "string"},
                        "cites": {"type": "array", "items": {"type": "string"}},
                        "serves": {"type": "array", "items": {"type": "string"}},
                        "items": _items_schema(),
                    },
                    "required": ["kind", "title", "rationale", "cites", "items"],
                },
            }
        },
        "required": ["candidates"],
    },
)

VERDICT_TOOL = ToolSpec(
    "submit_verdict",
    "Submit the verdict on the proposal.",
    {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["supported", "unsupported", "unclear"]},
            "reason": {"type": "string"},
        },
        "required": ["verdict", "reason"],
    },
)


@dataclass
class Candidate:
    kind: str  # "fix" | "next_step"
    title: str
    rationale: str
    cites: list[str]
    serves: list[str]
    items: list[dict[str, Any]]  # {key, op, arguments}, keys local to the candidate
    finding: str  # finding_key
    verdict: str = ""
    reason: str = ""


@dataclass
class Outcome:
    outcome: str
    items: int = 0
    verified: int = 0
    dropped: int = 0
    tokens: int = 0
    proposal: str | None = None
    problems: list[str] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "items": self.items,
            "verified": self.verified,
            "dropped": self.dropped,
            "tokens": self.tokens,
            "proposal": self.proposal,
        }


def verifier_problems(verifier: Any, main: LLMProvider | None) -> list[str]:
    """Start-up problems of the verifier configuration: it must be another model than the one the chats use (ADR-0205)."""
    from .llm import ProviderConfig
    from .llm.factory import OPENAI_ALIASES

    def canonical(provider: str) -> str:
        return "openai" if provider in OPENAI_ALIASES else provider

    if main is not None:
        same = (
            canonical(getattr(main, "name", "")) == canonical(verifier.provider)
            and getattr(main, "model", None) == verifier.model
        )
    else:
        mine = ProviderConfig.from_env()
        default = _default_model(canonical(verifier.provider))
        same = canonical(mine.provider) == canonical(verifier.provider) and (mine.model or default) == (
            verifier.model or default
        )
    if same:
        return [
            "ARCVIZ_VERIFIER_PROVIDER/ARCVIZ_VERIFIER_MODEL must name another model than ARCVIZ_PROVIDER/ARCVIZ_MODEL."
        ]
    return []


def _default_model(provider: str) -> str | None:
    try:
        if provider == "anthropic":
            from .llm.anthropic_provider import DEFAULT_MODEL
        else:
            from .llm.openai_provider import DEFAULT_MODEL
    except ModuleNotFoundError:
        return None
    return DEFAULT_MODEL


# ---- what a project is made of: the ids a candidate may cite ----------------------------------------------------------


def known_refs(snapshot: ProjectSnapshot) -> set[str]:
    """Every id a candidate may cite (and `vision`)."""
    project = snapshot.project
    refs = {"vision"}
    for group in (
        project.elements,
        project.relations,
        project.drivers,
        project.scenarios,
        project.decisions,
        project.stakeholders,
    ):
        refs |= {item.id for item in group}
    refs |= {d.id for _m, d in snapshot.members}
    if snapshot.requirements is not None:
        refs |= {r.id for r in snapshot.requirements.requirements}
    return refs


def serve_targets(snapshot: ProjectSnapshot) -> set[str]:
    """What a next step may serve: an active driver id or a vision goal (`vision:g<n>`)."""
    project = snapshot.project
    targets = {d.id for d in active(project.drivers)}
    if project.vision is not None:
        targets |= {f"vision:g{n}" for n in range(1, len(project.vision.goals) + 1)}
    return targets


def cited_objects(snapshot: ProjectSnapshot, cites: list[str]) -> list[str]:
    """The cited objects as stored, one JSON line each, for the verifier."""
    project = snapshot.project
    by_id: dict[str, Any] = {}
    for group in (
        project.elements,
        project.relations,
        project.drivers,
        project.scenarios,
        project.decisions,
        project.stakeholders,
    ):
        by_id.update({item.id: item for item in group})
    if snapshot.requirements is not None:
        by_id.update({r.id: r for r in snapshot.requirements.requirements})
    lines: list[str] = []
    for ref in cites:
        if ref == "vision":
            lines.append(
                f"vision: {json.dumps(project.vision.model_dump() if project.vision else None, ensure_ascii=False)}"
            )
        elif ref in by_id:
            body = by_id[ref].model_dump(mode="json")
            body.pop("origin", None)
            lines.append(f"{ref}: {json.dumps(body, ensure_ascii=False)}")
        else:
            diagram = next((d for _m, d in snapshot.members if d.id == ref), None)
            if diagram is not None:
                lines.append(f"{ref}: diagram «{diagram.type}» {diagram.title}\n{diagram.summary(budget=1500)}")
    return lines


# ---- the main model's candidates ----------------------------------------------------------------------------------


def parse_candidates(data: Any, snapshot: ProjectSnapshot) -> tuple[list[Candidate], int]:
    """The valid candidates of `submit_steward_findings` and how many were dropped (unknown cites, a next step that serves
    nothing, an operation a model may not propose, an item that does not apply to a copy of the project)."""
    raw = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return [], 0
    refs, targets = known_refs(snapshot), serve_targets(snapshot)
    out: list[Candidate] = []
    dropped = 0
    for entry in raw[:MAX_CANDIDATES]:
        candidate = _candidate(entry, snapshot, refs, targets)
        if candidate is None:
            dropped += 1
        else:
            out.append(candidate)
    dropped += max(0, len(raw) - MAX_CANDIDATES)
    return out, dropped


def _candidate(entry: Any, snapshot: ProjectSnapshot, refs: set[str], targets: set[str]) -> Candidate | None:
    if not isinstance(entry, dict):
        return None
    kind = entry.get("kind")
    title = " ".join(str(entry.get("title") or "").split())[:MAX_TITLE]
    rationale = " ".join(str(entry.get("rationale") or "").split())[:400]
    cites = [c for c in entry.get("cites") or [] if isinstance(c, str)]
    serves = [s for s in entry.get("serves") or [] if isinstance(s, str)]
    items = entry.get("items")
    if kind not in ("fix", "next_step") or not title or not rationale or not isinstance(items, list) or not items:
        return None
    if not cites or any(c not in refs for c in cites) or len(cites) > MAX_CITES:
        return None
    if kind == "next_step" and (not serves or any(s not in targets for s in serves)):
        return None
    if len(items) > MAX_ITEMS_PER_CANDIDATE:
        return None
    cleaned = []
    for item in items:
        if not isinstance(item, dict) or item.get("op") not in LLM_OPERATIONS:
            return None
        key, args = item.get("key"), item.get("arguments", {})
        if not isinstance(key, str) or not KEY_RE.match(key) or len(key) > 16 or not isinstance(args, dict):
            return None
        cleaned.append({"key": key, "op": item["op"], "arguments": args})
    if len({i["key"] for i in cleaned}) != len(cleaned):
        return None
    if not _applies(snapshot.project, cleaned):
        return None
    finding = finding_key(
        "llm:" + ("fix" if kind == "fix" else "next"),
        json.dumps(sorted(cites)),
        json.dumps([(i["op"], i["arguments"]) for i in cleaned], sort_keys=True, ensure_ascii=False),
    )
    return Candidate(kind, title, rationale, list(dict.fromkeys(cites)), serves, cleaned, finding)


def _applies(project: projects.Project, items: list[dict[str, Any]]) -> bool:
    """Whether the items apply, in order, to a copy of the project (later items may name earlier ones as `@key`)."""
    scratch = copy.deepcopy(project)
    ids: dict[str, str] = {}
    for item in items:
        try:
            args = _resolve(item["arguments"], ids)
            out = projects.apply(scratch, item["op"], args)
        except (OperationError, KeyError):
            return False
        if item["op"].startswith("add_"):
            ids[item["key"]] = out["result"]["id"]
    return True


def _resolve(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str) and value.startswith("@") and len(value) > 1:
        return ids[value[1:]]
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, ids) for k, v in value.items()}
    return value


def _rename(value: Any, mapping: dict[str, str]) -> Any:
    """`value` with every `"@key"` renamed through `mapping` (a candidate's local keys become proposal-wide)."""
    if isinstance(value, str) and value.startswith("@") and value[1:] in mapping:
        return "@" + mapping[value[1:]]
    if isinstance(value, list):
        return [_rename(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _rename(v, mapping) for k, v in value.items()}
    return value


# ---- the deterministic stage --------------------------------------------------------------------------------------


def rule_candidates(findings: list[ProjectFinding], project: projects.Project) -> list[Candidate]:
    """What the review rules settle on their own: a finding whose resolutions name exactly one way of the model (creating
    the relation of a connection, moving an element to its only other parent, setting a property to its only value). Never
    a removal, and never a choice between alternatives."""
    out: list[Candidate] = []
    for f in findings:
        picks = [r for r in f.resolutions if r.action in ("create_relation", "update_relation", "move_element")]
        if len(picks) != 1:
            continue
        r = picks[0]
        if r.action == "create_relation":
            item = {"key": "i1", "op": "add_relation", "arguments": dict(r.args)}
            cites = [str(r.args.get(k)) for k in ("source", "target") if r.args.get(k)]
        elif r.action == "update_relation" and r.relation:
            item = {"key": "i1", "op": "update_relation", "arguments": {"relation": r.relation, **r.args}}
            cites = [r.relation]
        elif r.action == "move_element" and r.model_element:
            item = {"key": "i1", "op": "update_element", "arguments": {"element": r.model_element, **r.args}}
            cites = [r.model_element]
        else:
            continue
        if not cites or not _applies(project, [item]):
            continue
        key = finding_key(f"rule:{f.rule}", f.id, json.dumps(item["arguments"], sort_keys=True, ensure_ascii=False))
        out.append(Candidate("fix", f.message[:MAX_TITLE], f"{f.rule}: {f.message}"[:400], cites, [], [item], key))
    return out


# ---- verification and selection -----------------------------------------------------------------------------------


def parse_verdict(data: Any) -> tuple[str, str]:
    """`(verdict, reason)`; anything unusable is `unclear`."""
    if isinstance(data, dict) and data.get("verdict") in ("supported", "unsupported", "unclear"):
        return str(data["verdict"]), " ".join(str(data.get("reason") or "").split())[:MAX_REASON]
    return "unclear", ""


def select(candidates: list[Candidate], dismissed: set[str], taken: set[str], limit: int) -> list[Candidate]:
    """Drop what people dismissed or what is already proposed, order fixes before next steps, cap the items."""
    kept: list[Candidate] = []
    seen: set[str] = set()
    for c in sorted(candidates, key=lambda c: (c.kind != "fix", c.finding.startswith("llm"))):
        if c.finding in dismissed or c.finding in taken or c.finding in seen:
            continue
        if sum(len(k.items) for k in kept) + len(c.items) > limit:
            continue
        seen.add(c.finding)
        kept.append(c)
    return kept


def proposal_items(selected: list[Candidate]) -> list[dict[str, Any]]:
    """The items of the project proposal: one per operation, keys unique across candidates (`c<n>_<key>`)."""
    out: list[dict[str, Any]] = []
    for number, c in enumerate(selected, start=1):
        mapping = {i["key"]: f"c{number}_{i['key']}" for i in c.items}
        why = c.rationale + (f" Prüfung: {c.reason}" if c.verdict == "supported" and c.reason else "")
        for item in c.items:
            out.append(
                {
                    "key": mapping[item["key"]],
                    "op": item["op"],
                    "arguments": _rename(item["arguments"], mapping),
                    "title": c.title if len(c.items) == 1 else "",
                    "group": c.kind,
                    "finding": c.finding,
                    "rationale": why,
                    "cites": c.cites,
                }
            )
    return out


def fingerprint_of(snapshot: ProjectSnapshot) -> str:
    """What a run saw: the project, its member diagrams and its requirements."""
    box = snapshot.requirements.model_dump(mode="json") if snapshot.requirements is not None else None
    body = json.dumps(
        {
            "project": snapshot.project.model_dump(mode="json", exclude={"updated_at"}),
            "diagrams": sorted((d.id, fingerprint(d)) for _m, d in snapshot.members),
            "requirements": box,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode()).hexdigest()[:24]


# ---- the run -------------------------------------------------------------------------------------------------------


def _tokens(usage: dict[str, int]) -> int:
    return int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))


def context_text(snapshot: ProjectSnapshot, findings: list[ProjectFinding]) -> str:
    """The user message of the main call: digest, vision, drivers, decisions and open findings."""
    from .agent_model import architecture_context

    lines = project_review.review_input(snapshot, findings).split("\n")
    arch = architecture_context(snapshot.project)
    extra: list[str] = []
    if arch.vision:
        extra += ["Vision:", arch.vision, ""]
    if arch.drivers:
        extra += ["Architecture drivers (most important first):", arch.drivers, ""]
    box = snapshot.requirements
    if box is not None and box.requirements:
        cover = requirement_model.coverage_map(box, snapshot.project)
        uncovered = [r.id for r in box.requirements if r.status == "refined" and not cover.get(r.id)]
        if uncovered:
            extra += ["Refined requirements nothing covers yet: " + ", ".join(uncovered[:30]), ""]
    return "\n".join([*extra, *lines])


class Steward:
    """Runs the checks of projects. `provider` and `verifier` are called lazily (they may fail to exist)."""

    def __init__(
        self,
        root: ServiceRoot,
        config: StewardConfig,
        provider: Callable[[], LLMProvider],
        verifier: Callable[[], LLMProvider | None],
    ):
        self.root = root
        self.config = config
        self._provider = provider
        self._verifier = verifier

    def run(self, project_id: str, manual: bool = False, now: datetime | None = None) -> dict[str, Any]:
        """Check one project (system context); the report of what happened. Never raises because of a model."""
        now = now or datetime.now(UTC)
        svc = self.root.system_services()
        store = svc.steward_store
        box, _rev = store.load(project_id)
        if not box.enabled and not manual:
            return Outcome("disabled").report()
        if box.runs_today(now) >= self.config.max_runs_per_day:
            return Outcome("capped").report()
        project, members = svc.projects.load_members(project_id)
        snapshot = ProjectSnapshot(project, members, svc.requirements.box(project_id))
        fp = fingerprint_of(snapshot)
        if fp == box.last_fingerprint and not manual:
            return Outcome("unchanged").report()
        with telemetry.span("arcviz.steward.run") as span:
            outcome = self._check(svc, snapshot, box, project_id)
        span.set("arcviz.steward.outcome", outcome.outcome)
        store.record(project_id, fp, outcome, now)
        telemetry.record_steward_run(outcome.outcome)
        return outcome.report()

    def _check(self, svc: SystemServices, snapshot: ProjectSnapshot, box: StewardBox, project_id: str) -> Outcome:
        outcome = Outcome("clean")
        findings = project_review.run_project_checks(snapshot)
        candidates = rule_candidates(findings, snapshot.project)
        verifier = self._verifier() if self.config.verifier_configured else None
        if verifier is None:
            outcome.problems.append("no verifier: only rule-based items")
        else:
            self._ask(snapshot, findings, verifier, candidates, outcome)
        pending = [p for p in svc.proposals.list(project_id, "pending") if p.client == CLIENT]
        taken = {i.finding for p in svc.proposals.list(project_id) if p.client != CLIENT for i in p.pending_items()}
        selected = select(candidates, set(box.dismissed), taken, self.config.max_items)
        for old in pending:  # the steward keeps one pending proposal: the new run replaces it
            svc.proposals.discard(project_id, old.id)
        if not selected:
            return outcome
        try:
            fixes, steps = sum(c.kind == "fix" for c in selected), sum(c.kind == "next_step" for c in selected)
            summary = (
                _(
                    "Steward: {fixes} Korrekturen, {steps} nächste Schritte, von einem zweiten Modell geprüft.",
                    fixes=fixes,
                    steps=steps,
                )
                if verifier is not None
                else _("Steward: {fixes} Korrekturen aus den Prüfregeln.", fixes=fixes)
            )
            stored = svc.proposals.create_project(project_id, proposal_items(selected), summary, client=CLIENT)
        except ProposalError as exc:
            logger.warning("The steward's proposal was refused (%s)", type(exc).__name__)
            outcome.outcome = "blocked"
            return outcome
        outcome.outcome, outcome.proposal = "proposed", stored.id
        outcome.items = len(stored.items)
        telemetry.record_steward_items("proposed", outcome.items)
        return outcome

    def _ask(
        self,
        snapshot: ProjectSnapshot,
        findings: list[ProjectFinding],
        verifier: LLMProvider,
        candidates: list[Candidate],
        outcome: Outcome,
    ) -> None:
        """The main model's candidates, each verified by the other model; the confirmed ones join `candidates`."""
        try:
            data, usage = self._provider().complete_tool(
                FINDINGS_SYSTEM_PROMPT, context_text(snapshot, findings), FINDINGS_TOOL
            )
            outcome.tokens += _tokens(usage)
            authored, dropped = parse_candidates(data, snapshot)
            outcome.dropped += dropped
        except Exception as exc:  # the rule items still go out
            logger.warning("The steward's model call failed (%s)", type(exc).__name__)
            outcome.problems.append("main call failed")
            return
        digest = "\n".join(project_review.digest_lines(snapshot))
        for candidate in authored:
            if outcome.tokens >= MAX_TOKENS_PER_RUN:
                outcome.dropped += 1
                continue
            try:
                data, usage = verifier.complete_tool(
                    VERDICT_SYSTEM_PROMPT, _verdict_text(snapshot, candidate, digest), VERDICT_TOOL
                )
                outcome.tokens += _tokens(usage)
                candidate.verdict, candidate.reason = parse_verdict(data)
            except Exception as exc:
                logger.warning("The steward's verification failed (%s)", type(exc).__name__)
                candidate.verdict = "unclear"
            telemetry.record_steward_items(candidate.verdict)
            if candidate.verdict == "supported":
                outcome.verified += 1
                candidates.append(candidate)
            else:
                outcome.dropped += 1


def _verdict_text(snapshot: ProjectSnapshot, candidate: Candidate, digest: str) -> str:
    proposal = {
        "kind": candidate.kind,
        "title": candidate.title,
        "rationale": candidate.rationale,
        "serves": candidate.serves,
        "cites": candidate.cites,
        "items": [{"op": i["op"], "arguments": i["arguments"]} for i in candidate.items],
    }
    decisions = decision_lines(snapshot.project.decisions)
    parts = [
        "Proposal:",
        json.dumps(proposal, ensure_ascii=False, indent=1),
        "",
        "Cited objects as stored:",
        *cited_objects(snapshot, candidate.cites),
        "",
    ]
    vision = vision_lines(snapshot.project.vision)
    if vision:
        parts += ["Vision:", vision, ""]
    if decisions:
        parts += ["Accepted decisions (binding):", decisions, ""]
    return "\n".join([*parts, "Project digest:", digest])


# ---- the worker ----------------------------------------------------------------------------------------------------


class StewardWorker:
    """One thread that checks the projects that changed: a change (a hint of the live hub) marks its project dirty, and a
    dirty project is checked once `debounce_seconds` passed without another change; every `sweep_seconds` the enabled
    projects are marked too, which also catches what other replicas changed. Per project one process holds the lease."""

    def __init__(self, steward: Steward, hub: LiveHub | None, tick: float = 5.0):
        self.steward = steward
        self.hub = hub
        self.tick = tick
        self.config = steward.config
        self._dirty: dict[str, float] = {}  # project -> when it last changed (monotonic)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._swept = float("-inf")

    def start(self) -> None:
        if self.hub is not None:
            self.hub.add_listener(self.notice)
        self._thread = threading.Thread(target=self._loop, name="arcviz-steward", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self.hub is not None:
            self.hub.remove_listener(self.notice)
        if self._thread is not None:
            self._thread.join(timeout=10)

    def notice(self, event: dict[str, Any]) -> None:
        """A live event of any write: remember its project (never blocks, never raises)."""
        try:
            if event.get("type") == "chat":
                return
            project = event.get("project")
            if project is None and event.get("diagram"):
                found = self.steward.root.system_services().projects.locate(str(event["diagram"]))
                project = found[0] if found else None
            if isinstance(project, str):
                with self._lock:
                    self._dirty[project] = time.monotonic()
        except Exception:
            logger.debug("The steward could not place an event", exc_info=True)

    def _loop(self) -> None:
        while not self._stop.wait(self.tick):
            try:
                self.step()
            except Exception:
                logger.exception("A steward step failed")

    def step(self, now: float | None = None) -> list[str]:
        """Check what is due; the ids of the projects that ran."""
        now = time.monotonic() if now is None else now
        svc = self.steward.root.system_services()
        store = svc.steward_store
        if now - self._swept >= self.config.sweep_seconds:
            self._swept = now
            for project in store.enabled_projects():
                with self._lock:
                    self._dirty.setdefault(project, now - self.config.debounce_seconds)
        with self._lock:
            due = [p for p, at in self._dirty.items() if now - at >= self.config.debounce_seconds]
        ran: list[str] = []
        for project in due:
            with self._lock:
                self._dirty.pop(project, None)
            if not store.enabled(project):
                continue
            holder = store.acquire(project, self.config.debounce_seconds * 2 + 600)
            if holder is None:
                continue
            try:
                self.steward.run(project)
                ran.append(project)
            except Exception as exc:
                logger.warning("The steward's run failed (%s)", type(exc).__name__)
            finally:
                store.release(project, holder)
        return ran
