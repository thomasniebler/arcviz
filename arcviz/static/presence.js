// Presence (spec: docs/architecture/specs/collaboration.md, #171): who else has the diagram or project open, what
// they have selected and who is chatting with the assistant. Pure module: no DOM access, unit-tested with node --test.

import { t } from "./i18n.js";

// Outline colours, distinguishable in both themes; a user keeps theirs (hash of the id).
export const PRESENCE_COLORS = ["#d9480f", "#1971c2", "#2f9e44", "#ae3ec9", "#e8590c", "#0c8599", "#c2255c", "#5c940d"];

/** Up to two initials of a display name (or id). */
export function initials(name) {
  const words = String(name || "").trim().split(/[\s._@-]+/).filter(Boolean);
  if (!words.length) return "?";
  const letters = words.length === 1 ? [...words[0]].slice(0, 2) : [[...words[0]][0], [...words[words.length - 1]][0]];
  return letters.join("").toUpperCase();
}

/** The colour of a user: the same on every screen. */
export function colorOf(userId) {
  let hash = 0;
  for (const ch of String(userId || "")) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return PRESENCE_COLORS[hash % PRESENCE_COLORS.length];
}

function person(entry) {
  return { user: entry.user, name: entry.name || entry.user, initials: initials(entry.name || entry.user), color: colorOf(entry.user) };
}

/**
 * The people to show as chips: of the diagram (`viewers`), else of the project (`project_viewers`, a project stream
 * or the project chat), one per user, without the caller (`me`), with `chatting` when they are in a chat turn here.
 */
export function viewersToShow(snapshot, me, { project = false } = {}) {
  if (!snapshot) return [];
  const list = project || !snapshot.viewers?.length ? snapshot.project_viewers || [] : snapshot.viewers;
  const chatting = new Set((snapshot.chatting || []).map((c) => c.user));
  const seen = new Map();
  for (const entry of list) {
    if (entry.user === me || seen.has(entry.user)) continue;
    seen.set(entry.user, { ...person(entry), chatting: chatting.has(entry.user) });
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** The selections of others on the shown diagram: one mark per user and element. */
export function selectionMarks(snapshot, me) {
  const marks = new Map();
  for (const entry of snapshot?.viewers || []) {
    if (entry.user === me || !entry.selection) continue;
    marks.set(`${entry.user}\u0000${entry.selection}`, { ...person(entry), element: entry.selection });
  }
  return [...marks.values()];
}

/** The line above the chat input while others chat with the assistant here, or "". */
export function chatNotice(snapshot, me) {
  const names = [...new Set((snapshot?.chatting || []).filter((c) => c.user !== me).map((c) => c.name || c.user))];
  if (!names.length) return "";
  const who = names.length === 1 ? names[0] : t("{names} und {last}", { names: names.slice(0, -1).join(", "), last: names[names.length - 1] });
  return names.length === 1 ? t("{who} chattet gerade mit dem Assistenten.", { who }) : t("{who} chatten gerade mit dem Assistenten.", { who });
}
