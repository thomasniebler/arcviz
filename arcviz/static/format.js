// UI labels built from API data. Pure module: no DOM access, unit-tested with node --test.
import { locale, t } from "./i18n.js";

/** Tool line of a chat turn: `name(k=v, …)`, strings raw, everything else as JSON. */
export function describeCall(name, args) {
  const parts = Object.entries(args || {}).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`);
  return `${name}(${parts.join(", ")})`;
}

/** The `error` of a JSON tool result, or the content itself. */
export function toolError(content) {
  try { return JSON.parse(content).error || content; } catch { return content; }
}

export function tokenMeta(usage) {
  const total = (usage?.input_tokens || 0) + (usage?.output_tokens || 0);
  return total ? t("{count} Tokens", { count: total.toLocaleString(locale()) }) : "";
}

export function truncate(text, max) { return text.length > max ? text.slice(0, max - 1) + "…" : text; }


/** Messages anywhere in a sequence diagram's fragment tree. */
function messageCount(interaction) {
  const count = (fragments) => (fragments || []).reduce((n, f) => n + (f.element === "message" ? 1 : 0) + (f.operands || []).reduce((m, op) => m + count(op.fragments), 0), 0);
  return count(interaction.fragments);
}

export function diagramStats(d) {
  const when = t("zuletzt {time}", { time: new Date(d.updated_at).toLocaleString(locale()) });
  if (d.interaction) return t("{lifelines} Lebenslinien · {messages} Nachrichten · {when}", { lifelines: d.interaction.lifelines.length, messages: messageCount(d.interaction), when });
  return t("{nodes} Elemente · {edges} Kanten · {when}", { nodes: Object.keys(d.nodes).length, edges: d.edges.length, when });
}

/** Who caused a version or wrote a chat entry (M20); "" when unknown (written before workspaces, or by the system). */
export function authorName(entry) { return entry?.author_name || entry?.author || ""; }

/** Entry of the version selector: number, label, source, time and, when known, the author. */
export function versionOptionLabel(v) {
  const author = authorName(v);
  return `v${v.version} · ${truncate(v.label, 48)} (${v.source}, ${new Date(v.created_at).toLocaleString(locale())}${author ? `, ${author}` : ""})`;
}

/** Tooltip of a version: its full label and who made it. */
export function versionTitle(v) {
  const author = authorName(v);
  return author ? t("{label} – von {author}", { label: v.label, author }) : v.label;
}

/** Entry of the comparison dialog's selectors. */
export function versionLabel(v) { return `v${v.version} · ${truncate(v.label, 40)}`; }

/** Meta line of an assistant entry of a reloaded transcript. */
export function chatEntryMeta(entry) {
  const ops = entry.operations?.length ? t("{count} Operation(en)", { count: entry.operations.length }) : "";
  const ver = entry.version ? t("Version v{version}", { version: entry.version }) : entry.proposed ? t("Vorschlag") : "";
  return [ops, ver, tokenMeta(entry.usage)].filter(Boolean).join(" · ");
}

/** Meta line of the reply bubble from a `final` event. */
export function finalMeta(ev) {
  return [ev.operations.length ? t("{count} Operation(en)", { count: ev.operations.length }) : "", ev.version ? t("Version v{version}", { version: ev.version.version }) : "", tokenMeta(ev.usage)].filter(Boolean).join(" · ");
}

/** Text of the comparison banner. `target` null is the current state. */
export function compareSummary(base, target, diff) {
  const s = diff.summary;
  const extras = [diff.title_changed ? t("Titel geändert") : "", diff.direction_changed ? t("Richtung geändert") : ""].filter(Boolean);
  return t("Vergleich v{base} → {target}: +{added} neu · −{removed} entfernt · ~{changed} geändert{extras}", {
    base, target: target != null ? `v${target}` : t("aktueller Stand"), added: s.added, removed: s.removed, changed: s.changed,
    extras: extras.length ? " · " + extras.join(" · ") : "",
  });
}

/** Caption of an edge row's details button: the explicit direction and the number of properties. */
export function edgeSummary(edge) {
  const count = Object.keys(edge.properties || {}).length;
  return [{ forward: "→", both: "↔", none: "—" }[edge.direction] || "", count || ""].filter(Boolean).join(" ") || "⋯";
}

/** File name of an export of the shown view: `<diagramId>[-<focus>][-v<preview>][-v<base>-vs-…].<ext>`. */
export function exportName({ diagramId, focus, previewVersion, compare }, ext) {
  const parts = [diagramId || "diagram"];
  if (focus) parts.push(focus);
  if (previewVersion != null) parts.push(`v${previewVersion}`);
  if (compare) parts.push(`v${compare.base}-vs-${compare.target != null ? `v${compare.target}` : t("aktuell")}`);
  return `${parts.join("-")}.${ext}`;
}

/** Status line of a review result: number of findings, the outcome of the LLM call, tokens. */
export function reviewMeta(result) {
  const count = result.findings?.length ?? 0;
  const parts = [count === 1 ? t("{count} Befund", { count }) : t("{count} Befunde", { count })];
  const llm = result.llm;
  if (llm && !llm.ok) parts.push(t("KI-Review fehlgeschlagen: {error}", { error: llm.error }));
  if (llm?.dropped) parts.push(t("{count} ungültige KI-Befunde verworfen", { count: llm.dropped }));
  if (llm?.partial) parts.push(t("Diagramm zu groß: eingeklappte Container nicht von der KI geprüft"));
  if (llm) parts.push(tokenMeta(llm.usage));
  return parts.filter(Boolean).join(" · ");
}
