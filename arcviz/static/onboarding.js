// First steps for new users (spec: docs/architecture/specs/onboarding.md): the starter cards of the empty project and
// the steps of the guided tour. Pure module: no DOM access. The texts are N_ markers: `t()` them where they are shown.
import { N_, t } from "./i18n.js";

/** Starters of the empty-project landing: a diagram type, a finished example (a template of that type) and a first
 * chat message that fits the type. */
export const STARTERS = [
  {
    type: "deployment", title: N_("Verteilungsdiagramm"), blurb: N_("Wo läuft was? Server, Netze, Dienste und ihre Verbindungen."),
    template: "aks-database", newTitle: N_("Mein Deployment"),
    prompt: N_("Lege ein VNet mit zwei Subnetzen an, in das ein Webserver und eine PostgreSQL-Datenbank kommen. Der Webserver greift auf die Datenbank zu."),
  },
  {
    type: "activity", title: N_("Aktivitätsdiagramm"), blurb: N_("Wie läuft ein Prozess ab? Schritte, Entscheidungen und Parallelität."),
    template: "order-process", newTitle: N_("Mein Prozess"),
    prompt: N_("Modelliere einen Bestellprozess: Bestellung prüfen, bei verfügbarer Ware parallel packen und Rechnung erstellen, sonst ablehnen."),
  },
  {
    type: "sequence", title: N_("Sequenzdiagramm"), blurb: N_("Wer sagt wann was zu wem? Nachrichten zwischen Beteiligten."),
    template: "checkout", newTitle: N_("Mein Ablauf"),
    prompt: N_("Zeige, wie ein Browser beim Login einen Auth-Dienst aufruft, der gegen eine Datenbank prüft und ein Token zurückgibt."),
  },
];

/** Request body of `POST /api/diagrams` for a starter: its finished example (`example`) or an empty diagram of its type. */
export function starterBody(starter, project, example) {
  return example
    ? { title: t("{title} (Beispiel)", { title: t(starter.title) }), type: starter.type, template: starter.template, project }
    : { title: t(starter.newTitle), type: starter.type, template: "empty", project };
}

/** The starters whose example template the server offers (an older or customised server may not know one). */
export function availableStarters(config, starters = STARTERS) {
  return starters.filter((s) => config?.diagram_types?.[s.type]);
}

/** Steps of the guided tour: the element to highlight, a heading and a short text. */
export const TOUR_STEPS = [
  { target: "#navigator", title: N_("Projekte und Diagramme"), text: N_("Links liegen Ihre Projekte. Ein Projekt gliedert sich in Abschnitte und enthält Diagramme verschiedener Typen. Teilen können Sie ein Projekt über ⋯ → Freigeben.") },
  { target: ".chat", title: N_("Der Chat ist Ihr Werkzeug"), text: N_("Beschreiben Sie in eigenen Worten, was entstehen oder sich ändern soll. Der Assistent wendet geprüfte Operationen auf das Modell an. Mit „Vorschau“ sehen Sie Änderungen erst als Vorschlag und bestätigen sie.") },
  { target: ".canvas", title: N_("Das Diagramm"), text: N_("Rechts erscheint das Ergebnis sofort. Ein Klick auf ein Element öffnet den Inspektor zum Nachbearbeiten von Hand; Zoom und „Einpassen“ finden Sie in der Leiste darunter.") },
  { target: "#btn-review", title: N_("Review"), text: N_("Prüft das Diagramm auf Stärken, Risiken und Verbesserungen und markiert Befunde direkt im Diagramm. Jeder Befund lässt sich als Vorschlag umsetzen.") },
  { target: "#version-field", title: N_("Nichts geht verloren"), text: N_("Jede Änderung wird als Version gespeichert. Mit Undo, Redo, Versionen und „Vergleichen“ gehen Sie jederzeit zurück oder sehen, was sich geändert hat.") },
  { target: "#help-menu", title: N_("Mehr erfahren"), text: N_("Unter „Hilfe“ liegen das Benutzerhandbuch und diese Einführung. Sie können den Rundgang dort jederzeit wiederholen.") },
];

/** Index of the step after moving `by` (±1) from `index`, kept inside the tour; null when `by` moves past the last step. */
export function moveStep(index, by, count = TOUR_STEPS.length) {
  const next = index + by;
  if (next >= count) return null;
  return Math.max(0, next);
}

/** The first step from `index` in direction `by` (±1, `index` itself included) that `isVisible` accepts, or null when
 * the search leaves the tour (a hidden control is skipped; going back past the first shown step ends the search). */
export function nextVisible(index, by, isVisible, count = TOUR_STEPS.length) {
  for (let i = index; i >= 0 && i < count; i += by) if (isVisible(i)) return i;
  return null;
}
