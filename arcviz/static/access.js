// Users, own and shared projects and their roles in the UI (spec: docs/architecture/specs/personal-workspaces.md).
// Pure module: no DOM access, unit-tested in tests/js/access.test.mjs.

import { N_, t } from "./i18n.js";

export const ROLE_LABELS = { owner: N_("Besitzen"), editor: N_("Bearbeiten"), viewer: N_("Betrachten") };
export const ROLES = ["viewer", "editor", "owner"];
export const SHARE_ROLES = ["viewer", "editor"]; // what a share can be: a project has exactly one owner
export const EMPTY_PROJECTS_TEXT = N_("Sie haben noch keine Projekte. Legen Sie eines an, oder lassen Sie sich ein Projekt freigeben."); // t() where shown
export const READ_ONLY_HINT = N_("Nur Lesezugriff: Sie können dieses Projekt ansehen, aber nicht ändern."); // t() where shown
export function forbiddenText() { return t("Nur Lesezugriff: Ihre Rolle erlaubt diese Änderung nicht."); }

const RANK = { viewer: 1, editor: 2, owner: 3 };

/** Whether `role` may change content (editor or owner). Unknown (null: no workspaces, an older server) is allowed:
 * the server decides anyway. */
export function canEdit(role) { return role == null || RANK[role] >= RANK.editor; }

/** Whether `role` may share and delete (project owner). */
export function canOwn(role) { return role == null || role === "owner"; }

/** German label of a role, e.g. for the sharing dialog. */
export function roleLabel(role) { return ROLE_LABELS[role] ? t(ROLE_LABELS[role]) : role || ""; }

/**
 * The groups of the project select: the caller's own projects ("Meine Projekte"), then those shared with them
 * ("Mit mir geteilt"), each in the order of `projects`; empty groups are left out.
 */
export function projectGroups(projects) {
  const own = (projects || []).filter((p) => !p.shared);
  const shared = (projects || []).filter((p) => p.shared);
  return [
    { key: "own", label: t("Meine Projekte"), projects: own },
    { key: "shared", label: t("Mit mir geteilt"), projects: shared },
  ].filter((g) => g.projects.length);
}

/**
 * Whether a fresh installation gets the demo diagram (in the caller's own default project): only for the admin, only
 * while their own empty default project is all they have, and once per browser (`offered`).
 */
export function offerDemo(projects, me, offered) {
  if (!me?.admin || offered) return false;
  const list = projects || [];
  return list.length === 1 && !list[0].shared && list[0].id.startsWith("home_") && list[0].diagram_count === 0;
}

/** The option label of a project: a shared one names its owner. */
export function sharedLabel(project, label) {
  return project.shared && project.owner ? `${label} · ${project.owner.name}` : label;
}

/** "Name <e-mail>" or the name alone. */
export function personLabel(p) { return p.email && p.email !== p.name ? `${p.name} <${p.email}>` : p.name; }

/**
 * Rows of the sharing dialog from `GET /api/projects/{pid}/access`: the owner first (never editable), then every
 * share. Only the owner changes or removes shares, choosing viewer or editor.
 */
export function shareRows(access) {
  const editable = access.role === "owner";
  const owner = { ...access.owner, role: "owner", note: t("Besitzer des Projekts"), editable: false, roles: ["owner"] };
  const shares = (access.shares || []).map((s) => ({
    ...s,
    note: "",
    editable,
    roles: [...SHARE_ROLES],
  }));
  return [owner, ...shares].map((r) => ({ id: r.id, label: personLabel(r), role: r.role, note: r.note, editable: r.editable, roles: r.roles }));
}

/** What the UI may offer for the caller's `role` on the open project. */
export function abilities(role) {
  return { edit: canEdit(role), own: canOwn(role) };
}

/**
 * The disabled state of a control whose enabled state belongs to its feature (`data-edit-restore`, e.g. the mic):
 * read-only mode disables it and remembers that (`locked`), so that leaving read-only mode enables exactly the
 * controls it disabled and never one the feature disabled (no speech recognition).
 */
export function editLock(readOnly, disabled, locked) {
  if (readOnly) return { disabled: true, locked: locked || !disabled };
  return { disabled: locked ? false : disabled, locked: false };
}

/**
 * The confirmation after sharing: the display name the server resolved and what the owner typed, and the role; the
 * server tells nothing more about the person (ADR-0091, ADR-0101).
 */
export function addedText(added, typed, role) {
  if (!added) return t("Freigegeben für {name}: {role}.", { name: typed, role: roleLabel(role) });
  return t("Freigegeben für {name} ({user}): {role}.", { name: added.name, user: added.user, role: roleLabel(role) });
}

/** The share dialog's suggestions while typing (ADR-0165): the text a suggestion shows next to the user id it fills
 * in, `Name · e-mail` (the e-mail only when the server trusts it). */
export function suggestionLabel(user) {
  return [user.name, user.email].filter(Boolean).join(" · ");
}

/** Whether the typed text is worth asking the server for suggestions: two characters or more (the server answers none
 * below that), and not exactly one of the users already suggested (it was just chosen). */
export function wantsSuggestions(text, shown) {
  const typed = (text || "").trim();
  return typed.length >= 2 && !(shown || []).some((u) => u.id === typed);
}
