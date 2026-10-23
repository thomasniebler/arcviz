// Pure logic of the project navigator (spec: docs/architecture/specs/project-navigator.md): the visible
// rows of a project's section tree, choices for section selects, what to open, positions for up/down.
// No DOM access; tested in tests/js/projects.test.mjs.

import { t } from "./i18n.js";

/**
 * Visible rows of `tree` (`{diagrams, sections}` from the project view) in display order: at every level
 * the sections first, then the diagrams. Diagrams without an entry in `diagrams` (deleted meanwhile) are
 * left out. Rows below a section in `collapsed` are hidden. `index`/`count` are the row's position among
 * the siblings of its kind (what the move endpoints call `position`).
 */
export function treeRows(tree, diagrams, collapsed = []) {
  const info = new Map((diagrams || []).map((d) => [d.id, d]));
  const folded = new Set(collapsed || []);
  const rows = [];
  const walk = (level, depth, parent, hidden) => {
    const sections = level.sections || [];
    sections.forEach((s, index) => {
      const isCollapsed = folded.has(s.id);
      const empty = !(s.diagrams || []).some((id) => info.has(id)) && !(s.sections || []).length;
      if (!hidden) rows.push({ kind: "section", id: s.id, title: s.title, depth, parent, index, count: sections.length, collapsed: isCollapsed, empty });
      walk(s, depth + 1, s.id, hidden || isCollapsed);
    });
    const members = (level.diagrams || []).filter((id) => info.has(id));
    if (!hidden) {
      members.forEach((id, index) => {
        rows.push({ kind: "diagram", id, title: info.get(id).title, depth, section: parent, index, count: members.length });
      });
    }
  };
  walk(tree || { diagrams: [], sections: [] }, 0, null, false);
  return rows;
}

/** Options for a section select: the top level first, then every section indented by its depth. */
export function sectionChoices(tree) {
  const out = [{ value: "", label: t("Oberste Ebene") }];
  const walk = (level, depth) => {
    for (const s of level.sections || []) {
      out.push({ value: s.id, label: " ".repeat(3 * depth) + s.title });
      walk(s, depth + 1);
    }
  };
  walk(tree || { sections: [] }, 0);
  return out;
}

/** The project to open: `wanted` if it exists, else `remembered` if it exists, else the first; null without projects. */
export function pickProject(projects, wanted, remembered) {
  const ids = (projects || []).map((p) => p.id);
  if (wanted && ids.includes(wanted)) return wanted;
  if (remembered && ids.includes(remembered)) return remembered;
  return ids[0] ?? null;
}

/** Diagram ids of a project view in display order (collapsed sections included). */
export function diagramOrder(view) {
  return treeRows(view.tree, view.diagrams).filter((r) => r.kind === "diagram").map((r) => r.id);
}

/** The diagram to open in a project view: `wanted`, else `remembered`, else the first in display order. */
export function pickDiagram(view, wanted, remembered) {
  const ids = diagramOrder(view);
  if (wanted && ids.includes(wanted)) return wanted;
  if (remembered && ids.includes(remembered)) return remembered;
  return ids[0] ?? null;
}

/** New position of `row` moved by `delta` among its siblings, or null when it is already at that end. */
export function siblingMove(row, delta) {
  const position = row.index + delta;
  return position < 0 || position >= row.count ? null : position;
}

/** The diagram `delta` (1 or -1) after `current` in display order, wrapping around; the first one when
 * `current` is not in the project; null when there is no other diagram. */
export function nextDiagramId(view, current, delta = 1) {
  const ids = diagramOrder(view);
  if (ids.length < 2 && ids[0] === current) return null;
  const i = ids.indexOf(current);
  if (i < 0) return ids[0] ?? null;
  return ids[(i + delta + ids.length) % ids.length];
}

export function projectOptionLabel(summary) {
  const n = summary.diagram_count;
  return n === 1 ? t("{title} ({n} Diagramm)", { title: summary.title, n }) : t("{title} ({n} Diagramme)", { title: summary.title, n });
}
