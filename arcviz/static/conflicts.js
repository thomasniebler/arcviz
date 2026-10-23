// Concurrent edits in the inspector (spec: docs/architecture/specs/collaboration.md, ADR-0094, ADR-0099): what a
// remote change means for an open form, which fields conflict after a 412, and the retry delays of the live stream.
// Pure module: no DOM access, unit-tested with node --test.
import { t } from "./i18n.js";

/** Whether two field values are equal (values are strings, booleans, arrays or null). */
export function same(a, b) { return JSON.stringify(a ?? null) === JSON.stringify(b ?? null); }

/** A key/value object as one comparable field value: its entries sorted by key, as JSON. */
export function canonicalProperties(properties) {
  return JSON.stringify(Object.entries(properties || {}).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

/** A node's inspector fields as the server holds them (the base or the current values). */
export function nodeValues(node) {
  return {
    name: node.name,
    kind: node.kind,
    parent: node.parent ?? null,
    description: node.description || "",
    properties: canonicalProperties(node.properties),
  };
}

/**
 * A remote change while a form is open. `base`: the values the form was opened (or last rebased) with; `theirs`: the
 * values now on the server; `mine`: the form's values. Returns the fields changed remotely (`changed`), the new values
 * of the ones the user has not touched (`updates`), the touched ones that now differ (`conflicts`), and whether the
 * form can move on to the new revision silently (`rebase`: no conflict).
 */
export function remoteChange(base, theirs, mine) {
  const keys = Object.keys({ ...base, ...theirs });
  const changed = keys.filter((k) => !same(base[k], theirs[k]));
  const touched = new Set(keys.filter((k) => !same(mine[k], base[k])));
  const updates = Object.fromEntries(changed.filter((k) => !touched.has(k)).map((k) => [k, theirs[k]]));
  const conflicts = changed.filter((k) => touched.has(k) && !same(mine[k], theirs[k]));
  return { changed, updates, conflicts, rebase: conflicts.length === 0 };
}

/** After a 412: the fields both the user and someone else changed (against `base`), to different values. */
export function fieldConflicts(base, theirs, mine) {
  return Object.keys({ ...base, ...mine })
    .filter((k) => !same(mine[k], base[k]) && !same(theirs[k], base[k]) && !same(mine[k], theirs[k]))
    .map((k) => ({ key: k, mine: mine[k], theirs: theirs[k] }));
}

/** The revision in an ETag header (`"rev"` or `W/"rev"`), or null. */
export function parseEtag(header) {
  if (!header) return null;
  const value = header.trim().replace(/^W\//, "").replace(/^"|"$/g, "");
  return value || null;
}

/** Milliseconds before reconnect number `attempt` (0 = the first) of the live stream: 1 s doubling up to 30 s. */
export function liveBackoff(attempt) { return Math.min(30000, 1000 * 2 ** Math.max(0, attempt)); }

/** Who a change is attributed to in the UI. */
export function changedBy(name) { return name || t("jemand anderem"); }

/** A field value for the conflict dialog: "—" for nothing, `k=v, …` for properties, lists joined. */
export function displayValue(key, value) {
  if (value == null || value === "") return "—";
  if (key === "properties") {
    const entries = JSON.parse(value);
    return entries.length ? entries.map(([k, v]) => `${k}=${v}`).join(", ") : "—";
  }
  if (Array.isArray(value)) return value.join(", ") || "—";
  if (typeof value === "boolean") return value ? t("ja") : t("nein");
  return String(value);
}

/**
 * Delete conditionally on the revision the user was asked about (#186, ADR-0123): `ask(question)`, then
 * `remove(current.revision)` (sent as `If-Match`). A 412 (changed meanwhile) calls `reload()` for the new
 * `{revision, author}` (null: nothing to delete any more, stop; `{refused: text}`: stop and `tell(text)` why) and asks
 * again with `changed(author)`. Resolves true once deleted, false when the user declined or it stopped; any other
 * failure rejects. Every retry waits for a new confirmation.
 */
export async function confirmedDelete({ current, reload, ask, remove, question, changed, tell = async () => {} }) {
  let text = question;
  for (;;) {
    if (!(await ask(text))) return false;
    try {
      await remove(current.revision);
      return true;
    } catch (err) {
      if (err?.status !== 412) throw err;
    }
    current = await reload();
    if (!current) return false;
    if (current.refused) {
      await tell(current.refused);
      return false;
    }
    text = changed(current.author);
  }
}

/**
 * Who made the change a 412 reports, as far as this tab can tell (#186): the live stream's author of exactly that
 * `revision`, else the author of the newest version unless that is the caller (`me`) — a change by the caller cannot be
 * what got in the way, and a newer unversioned change by somebody else would then be misattributed. null: unknown
 * (shown as "jemand anderem").
 */
export function changeAuthor({ revision, lastChange, versions, me }) {
  if (lastChange && lastChange.revision === revision) return lastChange.author_name ?? null;
  const newest = versions?.at?.(-1);
  if (!newest || (me?.id && newest.author === me.id)) return null;
  return newest.author_name || newest.author || null;
}
