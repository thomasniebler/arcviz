// Local voice commands: short phrases that the UI executes itself, without a round-trip to the LLM.
// Pure module (no DOM) so it can be unit-tested with `node --test`.
import { getLanguage, t } from "./i18n.js";

/** Normalise speech or typed text for matching: lower-case, no punctuation, folded umlauts. */
export function normalize(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
    .replace(/[.,!?;:"'`´„“”]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const FILLERS = ["bitte", "please", "jetzt", "now", "mal"];

function stripFillers(text) {
  const words = text.split(" ").filter((w) => !FILLERS.includes(w));
  return words.join(" ").trim();
}

/** Command catalogue. `phrases` are matched whole (after normalisation); `pattern` commands capture a target. */
export const COMMANDS = [
  { id: "zoom_in", de: "Zoom rein", en: "Zoom in", phrases: ["zoom rein", "zoom in", "hineinzoomen", "reinzoomen", "vergroessern", "naeher", "closer"] },
  { id: "zoom_out", de: "Zoom raus", en: "Zoom out", phrases: ["zoom raus", "zoom out", "herauszoomen", "rauszoomen", "verkleinern"] },
  { id: "zoom_reset", de: "Zoom zurücksetzen", en: "Reset zoom", phrases: ["zoom zuruecksetzen", "zoom reset", "reset zoom", "hundert prozent", "100 prozent", "100 percent", "hundred percent"] },
  { id: "fit", de: "Einpassen", en: "Fit to view", phrases: ["einpassen", "fit", "fit to view", "alles anzeigen", "show everything", "diagramm einpassen"] },
  { id: "focus_all", de: "Ganzes Diagramm", en: "Whole diagram", phrases: ["ganzes diagramm", "gesamtes diagramm", "fokus aufheben", "whole diagram", "clear focus", "zeig alles", "show all"] },
  { id: "focus_up", de: "Ebene höher", en: "Up one level", phrases: ["ebene hoeher", "eine ebene hoeher", "eine ebene hoch", "nach oben", "up one level", "go up", "level up"] },
  { id: "undo", de: "Rückgängig", en: "Undo", phrases: ["rueckgaengig", "rueckgaengig machen", "undo", "zurueck"] },
  { id: "redo", de: "Wiederholen", en: "Redo", phrases: ["wiederholen", "redo", "vorwaerts", "nochmal anwenden"] },
  { id: "snapshot", de: "Version sichern", en: "Save version", phrases: ["version sichern", "stand sichern", "snapshot", "save version", "save snapshot"] },
  { id: "next_diagram", de: "Nächstes Diagramm", en: "Next diagram", phrases: ["naechstes diagramm", "next diagram"] },
  { id: "prev_diagram", de: "Vorheriges Diagramm", en: "Previous diagram", phrases: ["vorheriges diagramm", "voriges diagramm", "letztes diagramm", "previous diagram"] },
  { id: "toggle_source", de: "Quelltext anzeigen/ausblenden", en: "Toggle source", phrases: ["quelltext", "quelltext anzeigen", "quelltext ausblenden", "zeig quelltext", "show source", "hide source", "source"] },
  { id: "send", de: "Absenden", en: "Send", phrases: ["absenden", "abschicken", "senden", "abschicken bitte", "send", "submit", "los"] },
  { id: "clear_input", de: "Eingabe löschen", en: "Clear input", phrases: ["eingabe loeschen", "text loeschen", "eingabe leeren", "clear input", "clear text"] },
  { id: "help", de: "Hilfe (Befehle zeigen)", en: "Help", phrases: ["hilfe", "welche befehle", "befehle", "help", "commands", "what can i say"] },
  {
    id: "focus", de: "Fokus auf <Container>", en: "Focus on <container>", needsTarget: true,
    pattern: /^(?:fokus(?:siere|sieren)?(?: auf)?|zeig(?:e)?(?: mir)?(?: den| die| das)?|oeffne|geh(?:e)? (?:in|zu)|focus(?: on)?|show(?: me)?(?: the)?|open|go (?:to|into))\s+(.+?)(?:\s+an)?$/,
  },
];

/** Pick the node whose name or id matches `target` best. Returns the node or null. */
export function resolveTarget(target, nodes, { containersOnly = false, containerKinds = [] } = {}) {
  const wanted = normalize(target);
  if (!wanted) return null;
  const candidates = Object.values(nodes || {}).filter((n) => !containersOnly || containerKinds.includes(n.kind));
  const score = (n) => {
    const name = normalize(n.name), id = normalize(n.id);
    if (name === wanted || id === wanted) return 100;
    const dashed = wanted.replace(/ /g, "-");
    if (id === dashed) return 95;
    if (name.startsWith(wanted) || id.startsWith(dashed)) return 80;
    if (name.includes(wanted) || id.includes(dashed)) return 60;
    const words = wanted.split(" ");
    const hits = words.filter((w) => name.includes(w) || id.includes(w)).length;
    return hits ? 20 + (hits / words.length) * 30 : 0;
  };
  let best = null, bestScore = 0;
  for (const n of candidates) {
    const s = score(n);
    if (s > bestScore) { best = n; bestScore = s; }
  }
  return bestScore >= 40 ? best : null;
}

/**
 * Match a phrase against the command catalogue.
 * @returns {null | {id: string, args: object, label: string, error?: string}}
 */
export function matchCommand(text, { nodes = {}, containerKinds = [] } = {}) {
  const norm = stripFillers(normalize(text));
  if (!norm) return null;
  for (const cmd of COMMANDS) {
    if (cmd.phrases && cmd.phrases.includes(norm)) return { id: cmd.id, args: {}, label: getLanguage() === "en" ? cmd.en : cmd.de };
  }
  for (const cmd of COMMANDS) {
    if (!cmd.pattern) continue;
    const m = norm.match(cmd.pattern);
    if (!m) continue;
    const target = m[1].trim();
    if (["alles", "everything", "ganzes diagramm", "whole diagram"].includes(target)) return { id: "focus_all", args: {}, label: t("Ganzes Diagramm") };
    const node = resolveTarget(target, nodes, { containersOnly: true, containerKinds });
    if (!node) return { id: cmd.id, args: { target }, label: t("Fokus auf „{target}“", { target }), error: t("Kein Container passt zu „{target}“.", { target }) };
    return { id: cmd.id, args: { target, node }, label: t("Fokus auf {name}", { name: node.name }) };
  }
  return null;
}

/** Human-readable list for the help popover. */
export function commandHelp() {
  return COMMANDS.map((c) => ({ id: c.id, de: c.de, en: c.en, examples: (c.phrases || []).slice(0, 3) }));
}
