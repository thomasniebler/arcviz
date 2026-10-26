"""Start the app with a scripted LLM provider so the UI can be exercised without an API key.

Usage: python tests/e2e/server.py <data-dir> [port]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from arcviz import ports as arcviz_ports  # noqa: E402
from arcviz.llm.base import ModelReply  # noqa: E402
from arcviz.server import create_app  # noqa: E402
from arcviz.store import FileStore  # noqa: E402
from arcviz.templates import demo_diagram  # noqa: E402
from tests.conftest import ScriptedProvider, call, readable_ids  # noqa: E402

REPLY_TEXT = (
    "VNet prod-vnet (10.0.0.0/16) im Deployment angelegt und service-a, service-b, service-c dorthin verschoben."
)

PROJECT_REPLY_TEXT = "Sequenzdiagramm Bezahlablauf angelegt und payment im Deployment ergänzt."


# Readable ids for what the smoke test creates (it names them), like the unit tests; production draws
# `<slug>-<random suffix>` (ADR-0131), which the UI never derives: it always reads the id from the response.
# ARCVIZ_E2E_REAL_IDS=1 keeps the production generator; the smoke test reads every id from the UI or a response, so it
# passes either way (CI runs both, #196).
if os.environ.get("ARCVIZ_E2E_REAL_IDS", "") not in ("1", "true"):
    arcviz_ports.new_ids = readable_ids


class SlowScriptedProvider(ScriptedProvider):
    """Every model call takes ARCVIZ_E2E_TURN_DELAY seconds, so another browser can watch a turn in progress
    (presence: "is chatting", spec: docs/architecture/specs/collaboration.md)."""

    def _complete(self, system, messages, tools):
        time.sleep(float(os.environ.get("ARCVIZ_E2E_TURN_DELAY", "0") or 0))
        if [t.name for t in tools] == ["submit_scenario_analysis"]:  # answered by tool, outside the turn order
            self.requests.append({"system": system, "messages": list(messages), "tools": ["submit_scenario_analysis"]})
            return ANALYSIS_REPLY
        # the ISO 25010 suggestion (iso25010-qualities.md): one valid name, one gibberish
        if [t.name for t in tools] == ["submit_qualities"]:
            self.requests.append({"system": system, "messages": list(messages), "tools": ["submit_qualities"]})
            return ModelReply(
                text="",
                tool_calls=[
                    call("submit_qualities", "ql1", qualities=["reliability", "blau"], reason="Ausfallsicherheit.")
                ],
                stop_reason="tool_use",
            )
        for tool_name, answer in (("submit_refinements", REFINE_REPLY), ("submit_derivation", DERIVE_REPLY)):
            if [t.name for t in tools] == [tool_name]:  # the workshop's chunks (requirements-workshop.md): by tool, too
                self.requests.append({"system": system, "messages": list(messages), "tools": [tool_name]})
                return answer
        return super()._complete(system, messages, tools)


# The what-if analysis of the smoke test's scenario (spec: docs/architecture/specs/decision-support.md).
ANALYSIS_REPLY = ModelReply(
    text="",
    tool_calls=[
        call(
            "submit_scenario_analysis",
            "an1",
            summary="Heute läuft alles in einer Region; fällt sie aus, steht das System.",
            drivers=[{"driver": "d2", "effect": "violates", "reason": "Keine zweite Region."}],
            elements=[],
            sensitivity_points=["Replikation der Datenbank"],
            tradeoffs=["Verfügbarkeit gegen Kosten"],
            options=[
                {
                    "title": "Aktiv/Passiv",
                    "description": "Kalte zweite Region.",
                    "pros": ["günstig"],
                    "cons": ["RTO 30 min"],
                },
                {
                    "title": "Aktiv/Aktiv",
                    "description": "Zwei heiße Regionen.",
                    "pros": ["RTO < 1 min"],
                    "cons": ["doppelte Kosten"],
                    "instruction": "Füge eine zweite Region hinzu.",
                },
            ],
            recommended=1,
        )
    ],
    stop_reason="tool_use",
)


# The requirements workshop of the smoke test (spec: docs/architecture/specs/requirements-workshop.md): three imported
# requirements are refined in one chunk, then drivers and a scenario are derived from them.
REFINE_REPLY = ModelReply(
    text="",
    tool_calls=[
        call(
            "submit_refinements",
            "rf1",
            refinements=[
                {
                    "requirement": "req1",
                    "statement": "Der Shop ist rund um die Uhr erreichbar.",
                    "kind": "quality",
                    "priority": "must",
                    "measure": "Verfügbarkeit 99,9 %",
                    "theme": "Verfügbarkeit",
                },
                {
                    "requirement": "req2",
                    "statement": "Alle Daten werden verschlüsselt gespeichert.",
                    "kind": "quality",
                    "theme": "Sicherheit",
                    "issues": [{"kind": "conflict", "text": "Widerspricht dem Export im Klartext", "other": "req3"}],
                },
                {
                    "requirement": "req3",
                    "statement": "Der Export liefert eine CSV-Datei.",
                    "kind": "functional",
                    "theme": "Export",
                },
            ],
        )
    ],
    stop_reason="tool_use",
    usage={"input_tokens": 900, "output_tokens": 150},
)
DERIVE_REPLY = ModelReply(
    text="",
    tool_calls=[
        call(
            "submit_derivation",
            "dv1",
            items=[
                {
                    "key": "d_avail",
                    "op": "add_driver",
                    "requirements": ["req1"],
                    "title": "Verfügbarkeit",
                    "kind": "quality_goal",
                    "importance": "high",
                },
                {
                    "key": "q_zone",
                    "op": "add_scenario",
                    "requirements": ["req1"],
                    "title": "Ausfall einer Zone",
                    "kind": "failure",
                    "driver": "@d_avail",
                    "stimulus": "Eine Zone fällt aus",
                    "response_measure": "Verfügbarkeit 99,9 %",
                    "core": True,
                },
                {
                    "key": "d_sec",
                    "op": "add_driver",
                    "requirements": ["req2"],
                    "title": "Datensicherheit",
                    "kind": "quality_goal",
                },
            ],
            unmapped=[{"requirement": "req3", "reason": "reine Funktion"}],
        )
    ],
    stop_reason="tool_use",
    usage={"input_tokens": 1100, "output_tokens": 300},
)


provider = SlowScriptedProvider(
    [
        ModelReply(
            text="",
            tool_calls=[
                call(
                    "add_node",
                    "c1",
                    name="prod-vnet",
                    kind="vnet",
                    parent="prod-deployment",
                    properties={"cidr": "10.0.0.0/16"},
                )
            ],
            stop_reason="tool_use",
        ),
        ModelReply(
            text="",
            tool_calls=[
                call("move_node", "c2", id="service-a", parent="prod-vnet"),
                call("move_node", "c3", id="service-b", parent="prod-vnet"),
                call("move_node", "c4", id="service-c", parent="prod-vnet"),
            ],
            stop_reason="tool_use",
        ),
        ModelReply(
            text=REPLY_TEXT, tool_calls=[], stop_reason="end_turn", usage={"input_tokens": 1200, "output_tokens": 34}
        ),
        # propose-mode turns at the end of the smoke test (discarded once, then applied)
        *[
            reply
            for _ in range(2)
            for reply in (
                ModelReply(
                    text="",
                    tool_calls=[call("add_node", "p1", name="cache", kind="storage", parent="prod-deployment")],
                    stop_reason="tool_use",
                ),
                ModelReply(text="Vorschlag: Cache im Deployment.", tool_calls=[], stop_reason="end_turn"),
            )
        ],
        # architecture review (KI-Review button): one structured answer
        ModelReply(
            text="",
            tool_calls=[
                call(
                    "submit_review",
                    "r1",
                    summary="Klare Struktur hinter einem Gateway; die Datenanbindung ist der Schwachpunkt.",
                    findings=[
                        {
                            "category": "risk",
                            "severity": "medium",
                            "message": "service-a spricht ohne TLS mit orders-db.",
                            "elements": ["service-a", "orders-db"],
                            "edges": ["e3"],
                            "suggestion": "Setze an der Kante e3 die Eigenschaft protocol=postgres+tls.",
                        },
                        {
                            "category": "strength",
                            "severity": "info",
                            "message": "Ein Gateway bündelt den Zugriff.",
                            "elements": ["api-gateway"],
                        },
                        {
                            "category": "praise",  # not a category: dropped and counted
                            "severity": "high",
                            "message": "Ungültige Kategorie.",
                            "elements": ["ghost"],
                        },
                    ],
                )
            ],
            stop_reason="tool_use",
            usage={"input_tokens": 1500, "output_tokens": 200},
        ),
        # "Als Vorschlag umsetzen" on the database finding: a propose-mode turn adding a replica
        ModelReply(
            text="",
            tool_calls=[
                call(
                    "apply_batch",
                    "s1",
                    operations=[
                        {
                            "op": "add_node",
                            "args": {"name": "orders-db-replica", "kind": "database", "parent": "prod-deployment"},
                        },
                        {
                            "op": "add_edge",
                            "args": {"source": "orders-db", "target": "orders-db-replica", "kind": "replication"},
                        },
                    ],
                )
            ],
            stop_reason="tool_use",
        ),
        ModelReply(text="Vorschlag: Replik für orders-db.", tool_calls=[], stop_reason="end_turn"),
        # project chat (spec: docs/architecture/specs/project-chat.md): a new sequence diagram, then the deployment
        ModelReply(
            text="",
            tool_calls=[call("create_diagram", "pc1", title="Bezahlablauf", type="sequence")],
            stop_reason="tool_use",
        ),
        ModelReply(text="", tool_calls=[call("add_lifeline", "pc2", name="Kasse")], stop_reason="tool_use"),
        ModelReply(text="", tool_calls=[call("open_diagram", "pc3", diagram="demo")], stop_reason="tool_use"),
        ModelReply(
            text="",
            tool_calls=[call("add_node", "pc4", name="payment", kind="service", parent="prod-deployment")],
            stop_reason="tool_use",
        ),
        ModelReply(text=PROJECT_REPLY_TEXT, tool_calls=[], stop_reason="end_turn"),
        # project review (spec: docs/architecture/specs/project-review.md): one structured answer
        ModelReply(
            text="",
            tool_calls=[
                call(
                    "submit_project_review",
                    "pr1",
                    summary="Das Projekt beschreibt Struktur und Abläufe; die Zahlung ist nur teilweise modelliert.",
                    findings=[
                        {
                            "category": "risk",
                            "severity": "high",
                            "message": "Der Payment-Service fehlt im Ablauf Bezahlablauf.",
                            "diagram": "demo",
                            "elements": ["payment"],
                        },
                        {"category": "suggestion", "severity": "low", "message": "Eine Projektübersicht fehlt."},
                    ],
                )
            ],
            stop_reason="tool_use",
        ),
        # the requirements chat (spec: requirements-workshop.md): adds one requirement, then answers
        ModelReply(
            text="",
            tool_calls=[call("add_requirements", "rc1", items=[{"raw": "Das Backup läuft jede Nacht"}])],
            stop_reason="tool_use",
        ),
        ModelReply(
            text="Eine Anforderung aufgenommen: Das Backup läuft jede Nacht.", tool_calls=[], stop_reason="end_turn"
        ),
    ],
    stream=True,  # the reply text arrives as text_delta events, like with a real provider
)

if __name__ == "__main__":
    data_dir = sys.argv[1] if len(sys.argv) > 1 else ".e2e-data"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    store = FileStore(data_dir)
    if not store.exists("demo"):
        # Same diagram the UI would create on first load, plus a compacted transcript so the smoke test
        # can assert on the collapsed summary block (spec: docs/architecture/specs/chat-compaction.md).
        store.create("Demo deployment", "demo", demo_diagram("demo", "Demo deployment"))
        store.import_chat(
            "demo",
            [
                {
                    "role": "summary",
                    "seq": 6,
                    "content": "Earlier the user set up the gateway and three services.",
                    "covers": {"from_seq": 1, "to_seq": 6, "turns": 3},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
        )
    app = create_app(store=store, provider=provider)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
