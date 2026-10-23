// Pure helpers of the project chat (spec: docs/architecture/specs/project-chat.md): German descriptions of
// the project tools, the label of the diagrams a turn changed, and whether an event belongs to another diagram.
// Pure module: no DOM access, unit-tested in tests/js/projectchat.test.mjs.
import { N_, t } from "./i18n.js";

// The same German type names as types.js TYPE_LABELS (pure modules import nothing).
const TYPE_NOUNS = {
  deployment: N_("Verteilungsdiagramm"), activity: N_("Aktivitätsdiagramm"), sequence: N_("Sequenzdiagramm"), state_machine: N_("Zustandsdiagramm"),
  class: N_("Klassendiagramm"), use_case: N_("Anwendungsfalldiagramm"), component: N_("Komponentendiagramm"),
};

/** Tool line of a project tool in German; null for every other tool (shown with format.describeCall). */
export function describeProjectCall(name, args = {}) {
  switch (name) {
    case "list_diagrams": return t("Diagramme des Projekts auflisten");
    case "read_diagram": return t("Diagramm „{diagram}“ lesen", { diagram: args.diagram });
    case "open_diagram": return t("Diagramm „{diagram}“ öffnen", { diagram: args.diagram });
    case "create_diagram": {
      const noun = TYPE_NOUNS[args.type] ? t(TYPE_NOUNS[args.type]) : t("Diagramm ({type})", { type: args.type });
      return args.section
        ? t("Neues {noun} „{title}“ anlegen (Abschnitt {section})", { noun, title: args.title, section: args.section })
        : t("Neues {noun} „{title}“ anlegen", { noun, title: args.title });
    }
    default: return null;
  }
}

/** `[{id, label}]` of the `diagrams` of a final event or a transcript entry: "A (neu, v2)", "B (v7)", "C". */
export function changedDiagramItems(diagrams) {
  return (diagrams || []).map((d) => {
    const notes = [d.created ? t("neu") : "", d.version ? `v${d.version}` : ""].filter(Boolean);
    return { id: d.id, label: notes.length ? `${d.title} (${notes.join(", ")})` : d.title };
  });
}

/** "Geändert: A (neu, v2) · B (v7)" for the `diagrams` of a final event or a transcript entry; "" for none. */
export function changedDiagramsLabel(diagrams) {
  const items = changedDiagramItems(diagrams);
  return items.length ? t("Geändert: {items}", { items: items.map((i) => i.label).join(" · ") }) : "";
}

/** The diagram an event of a project turn renders, when it is not the shown one; else null. */
export function diagramSwitch(event, shownId) {
  const id = event?.diagram_id;
  return id && id !== shownId ? id : null;
}

/** Text of the chat pane's scope bar in the project scope. */
export function scopeLabel(project) {
  return project?.title ? t("Projekt-Chat · {title}", { title: project.title }) : t("Projekt-Chat");
}
