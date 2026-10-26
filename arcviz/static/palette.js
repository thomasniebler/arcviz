// The command palette's registry and ranking (spec: docs/architecture/specs/command-palette.md, ADR-0197). Pure: no DOM.
// A command is plain data; the view (paletteview.js) executes it by clicking the existing control (`click`: its id),
// setting the location hash (`route`) or opening a link (`href`). Availability is read from the control itself.
// Labels, groups and hints are N_ markers (German source texts); the view `t()`s them when it lists them.
import { N_ } from "./i18n.js";

/** The display order of the groups; "Zuletzt" (recent) and "Diagramme" (the open project's diagrams) are not registry groups. */
export const GROUPS = [N_("Zuletzt"), N_("Gehe zu"), N_("Diagramm"), N_("Version"), N_("Ansicht"), N_("Export"), N_("Projekt"), N_("Allgemein"), N_("Diagramme")];

const nav = (id, label, route, keywords = "", needs = "project") => ({ id, label, group: "Gehe zu", keywords, route, needs });
const press = (id, label, group, click, keywords = "", extra = {}) => ({ id, label, group, keywords, click, ...extra });

/** Every action reachable from the palette; `needs: "project"` = a project must be open. */
export const COMMANDS = [
  nav("go-diagrams", N_("Diagramm-Ansicht"), "", "diagramme leinwand canvas zurück", null),
  nav("go-requirements", N_("Anforderungen"), "#/requirements", "requirements workshop einkippen verfeinern ableiten"),
  nav("go-drivers", N_("Architektur: Treiber"), "#/architecture/drivers", "architektur qualitätsziel randbedingung risiko atam"),
  nav("go-scenarios", N_("Architektur: Szenarien"), "#/architecture/scenarios", "architektur qualitätsszenario analyse"),
  nav("go-decisions", N_("Architektur: Entscheidungen"), "#/architecture/decisions", "architektur adr decision"),
  nav("go-stakeholders", N_("Architektur: Stakeholder"), "#/architecture/stakeholders", "architektur beteiligte"),
  nav("go-vision", N_("Architektur: Vision"), "#/architecture/vision", "architektur produktvision ziele"),
  nav("go-documentation", N_("Architektur: Dokumentation"), "#/architecture/documentation", "architektur arc42 doku export notiz"),
  press("go-model", N_("Modell"), "Gehe zu", "btn-model", "projektmodell elemente beziehungen matrix", { needs: "project" }),
  press("go-proposals", N_("Vorschläge"), "Gehe zu", "btn-proposals", "agenten änderungsvorschläge mcp prüfen", { needs: "project" }),
  press("go-review", N_("Projekt-Review"), "Gehe zu", "btn-project-review", "prüfen regeln befunde", { needs: "project" }),
  press("go-chat", N_("Projekt-Chat"), "Gehe zu", "btn-project-chat", "assistent chat projekt", { needs: "project" }),

  press("diagram-new", N_("Neues Diagramm …"), "Diagramm", "btn-new", "anlegen erstellen vorlage", { needs: "project" }),
  press("diagram-import", N_("Diagramm importieren …"), "Diagramm", "btn-import", "json datei laden", { needs: "project" }),
  press("diagram-clear-chat", N_("Chat dieses Diagramms leeren"), "Diagramm", "btn-clear-chat", "verlauf löschen"),
  press("diagram-delete", N_("Diagramm löschen"), "Diagramm", "btn-delete", "entfernen"),

  press("version-snapshot", N_("Version sichern"), "Version", "btn-snapshot", "snapshot speichern benannt"),
  press("version-restore", N_("Version wiederherstellen"), "Version", "btn-restore", "zurücksetzen ausgewählte"),
  press("version-compare", N_("Versionen vergleichen …"), "Version", "btn-compare", "diff unterschied"),
  press("version-undo", N_("Rückgängig machen"), "Version", "btn-undo", "undo zurück", { hint: N_("Strg+Z") }),
  press("version-redo", N_("Wiederholen"), "Version", "btn-redo", "redo erneut", { hint: N_("Strg+Umschalt+Z") }),

  press("view-fit", N_("Diagramm einpassen"), "Ansicht", "btn-fit", "zoom anpassen fit"),
  press("view-zoom-reset", N_("Zoom auf 100 % zurücksetzen"), "Ansicht", "btn-zoom-reset", "zoom"),
  press("view-source", N_("Quelltext ein-/ausblenden"), "Ansicht", "btn-source", "mermaid code"),
  press("view-review", N_("Diagramm prüfen (Review)"), "Ansicht", "btn-review", "regeln architektur befunde ki"),

  press("export-svg", N_("Als SVG exportieren"), "Export", "exp-svg", "herunterladen bild vektor"),
  press("export-png", N_("Als PNG exportieren"), "Export", "exp-png", "herunterladen bild"),
  press("export-json", N_("Als JSON exportieren"), "Export", "exp-json", "herunterladen modell sichern"),
  press("export-puml", N_("Als PlantUML exportieren"), "Export", "exp-puml", "herunterladen"),
  press("export-mermaid", N_("Mermaid-Quelltext kopieren"), "Export", "exp-mmd", "kopieren zwischenablage"),

  press("project-new", N_("Neues Projekt …"), "Projekt", "btn-project-new", "anlegen erstellen"),
  press("project-share", N_("Projekt freigeben …"), "Projekt", "btn-project-share", "teilen zugriff viewer editor", { needs: "project" }),
  press("project-rename", N_("Projekt umbenennen …"), "Projekt", "btn-project-rename", "name", { needs: "project" }),
  press("project-describe", N_("Projektbeschreibung ändern …"), "Projekt", "btn-project-describe", "text", { needs: "project" }),
  press("project-export", N_("Projekt exportieren"), "Projekt", "btn-project-export", "herunterladen bundle sichern", { needs: "project" }),
  press("project-import", N_("Projekt importieren …"), "Projekt", "btn-project-import", "bundle datei laden"),
  press("section-new", N_("Neuer Abschnitt"), "Projekt", "btn-section-new", "ordner gliederung", { needs: "project" }),

  press("app-settings", N_("Persönliche Einstellungen …"), "Allgemein", "btn-settings", "design skin theme agenten zugang token"),
  press("app-tour", N_("Rundgang starten"), "Allgemein", "btn-tour", "hilfe einführung tour"),
  press("app-whats-new", N_("Neu in dieser Version"), "Allgemein", "btn-whats-new", "neuerungen changelog"),
  { id: "app-guide", label: N_("Benutzerhandbuch öffnen"), group: "Allgemein", keywords: "hilfe dokumentation docs", href: "/docs/guide/" },
  { id: "app-arc42", label: N_("Architekturdokumentation (arc42) öffnen"), group: "Allgemein", keywords: "hilfe dokumentation docs", href: "/docs/architecture/arc42/" },
];

/** Lower-case and strip diacritics one UTF-16 unit at a time, so the folded text has the length of the original
 * (the ranges of a match can then be applied to the original). `ß` folds to `s`. */
export function fold(text) {
  let out = "";
  for (let i = 0; i < text.length; i++) {
    const lower = text[i].toLowerCase();
    out += lower === "ß" ? "s" : lower.normalize("NFD")[0];
  }
  return out;
}

const words = (query) => fold(String(query ?? "")).split(/\s+/).filter(Boolean);
const isBoundary = (ch) => ch === undefined || /[\s:.,;()/\-–·]/.test(ch);

/** The best occurrence of `word` in the folded text `t`: the first one that starts the text or a word, else the first. */
function occurrence(t, word) {
  let first = -1;
  for (let at = t.indexOf(word); at >= 0; at = t.indexOf(word, at + 1)) {
    if (first < 0) first = at;
    if (at === 0 || isBoundary(t[at - 1])) return at;
  }
  return first;
}

/** `null` unless every word of `query` occurs in `text`; else a score, higher is better (0 for a blank query). */
export function scoreMatch(query, text) {
  const ws = words(query);
  if (!ws.length) return 0;
  const t = fold(text);
  let score = 0;
  for (const w of ws) {
    const at = occurrence(t, w);
    if (at < 0) return null;
    score += 2 * w.length + (at === 0 ? 8 : isBoundary(t[at - 1]) ? 5 : 0) - 0.01 * at;
  }
  return score - 0.001 * t.length;
}

/** The merged, sorted `[start, end)` ranges of the words of `query` in `text` (empty for a blank query or no match). */
export function matchRanges(query, text) {
  const t = fold(text);
  const found = [];
  for (const w of words(query)) {
    const at = occurrence(t, w);
    if (at >= 0) found.push([at, at + w.length]);
  }
  found.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const r of found) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]); else merged.push([...r]);
  }
  return merged;
}

/** The rows to show: `{command, ranges, recent?}`. A blank query lists the `recent` ids first, then everything in
 * registry order; otherwise the commands whose label and keywords contain every word, best match first. */
export function rankCommands(commands, query, recent = []) {
  if (!words(query).length) {
    const byId = new Map(commands.map((c) => [c.id, c]));
    const first = (recent || []).filter((id) => byId.has(id)).map((id) => ({ command: byId.get(id), ranges: [], recent: true }));
    const seen = new Set(first.map((r) => r.command.id));
    return [...first, ...commands.filter((c) => !seen.has(c.id)).map((command) => ({ command, ranges: [] }))];
  }
  return commands
    .map((command, index) => ({ command, index, score: scoreMatch(query, `${command.label} ${command.keywords || ""}`) }))
    .filter((r) => r.score !== null)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map(({ command }) => ({ command, ranges: matchRanges(query, command.label) }));
}

/** The recent list after running `id`: newest first, no duplicates, at most `limit` entries. */
export function rememberCommand(recent, id, limit = 5) {
  return [id, ...(recent || []).filter((x) => x !== id)].slice(0, limit);
}
