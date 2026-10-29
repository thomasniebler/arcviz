// The documentation tab of the Architektur page (spec: docs/architecture/specs/arc42-export.md, ADR-0207, ADR-0208): the
// links to the live arc42 page and its downloads, and the notes of the arc42 sections the model does not cover.
import { api } from "./api.js";
import { $, option } from "./dom.js";
import { t } from "./i18n.js";
import { confirmDiscard } from "./mdpane.js";
import { currentProject } from "./navigator.js";

const MAX_NOTE = 8000;

let sections = []; // [{id, title, gaps}] of the arc42 document, in the request language
let notes = {}; // section id -> note
let dirty = false;
let wanted = null; // a section to preselect when the tab loads (a next step of the gap analysis)

/** Preselect `id` in the section list the next time the tab is shown. */
export function focusSection(id) { wanted = id; }

function say(text) { $("documentation-status").textContent = text || ""; }

const base = () => `/api/projects/${encodeURIComponent(currentProject())}`;

/** Whether the editor holds changes that are not stored. */
export function isDocumentationDirty() { return dirty; }

function fillNote() {
  const id = $("documentation-section").value;
  $("documentation-note").value = notes[id] ?? "";
  const gaps = sections.find((s) => s.id === id)?.gaps ?? [];
  $("documentation-gaps").textContent = gaps.join(" ");
  dirty = false;
}

function setLinks() {
  const project = encodeURIComponent(currentProject());
  $("arc42-open").href = `/project/${project}/arc42`;
  $("arc42-markdown").href = `${base()}/arc42.md`;
  $("arc42-asciidoc").href = `${base()}/arc42.zip?format=asciidoc`;
  $("arc42-docx").href = `${base()}/arc42.docx`;
}

async function save() {
  const id = $("documentation-section").value;
  const text = $("documentation-note").value.trim();
  if (text.length > MAX_NOTE) { say(t("Eine Notiz darf höchstens {n} Zeichen haben.", { n: MAX_NOTE })); return; }
  if (text === (notes[id] ?? "")) { dirty = false; say(t("Keine Änderungen.")); return; }
  await api(`${base()}/ops`, { method: "POST", body: JSON.stringify({ op: "set_arc42_note", args: { section: id, text } }) });
  if (text) notes[id] = text; else delete notes[id];
  await refreshGaps();
  dirty = false;
  say(t("Gespeichert."));
}

async function refreshGaps() {
  const doc = await api(`${base()}/arc42`);
  sections = doc.sections.map((s) => ({ id: s.id, title: s.title, gaps: s.gaps }));
  $("documentation-gaps").textContent = (sections.find((s) => s.id === $("documentation-section").value)?.gaps ?? []).join(" ");
}

/** Load the document structure and the notes of the open project (when the tab is shown). */
export async function loadDocumentation() {
  if (!currentProject()) return;
  const [doc, project] = await Promise.all([api(`${base()}/arc42`), api(base())]);
  sections = doc.sections.map((s) => ({ id: s.id, title: s.title, gaps: s.gaps }));
  notes = project.project.arc42_notes ?? {};
  setLinks();
  say("");
  if (dirty) return; // data arriving late must not wipe what the user has already typed
  const select = $("documentation-section");
  const chosen = wanted || select.value || sections[0]?.id;
  wanted = null;
  select.replaceChildren(...sections.map((s) => option(s.id, `${Number(s.id)} ${s.title}`, s.id === chosen)));
  fillNote();
}

export function initDocumentationPanel() {
  const form = $("documentation-form");
  $("documentation-section").addEventListener("change", () => {
    if (!confirmDiscard(dirty)) return;
    fillNote();
  });
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("submit", (e) => { e.preventDefault(); save().catch((err) => say(err.message || String(err))); });
  $("documentation-cancel").addEventListener("click", () => { if (confirmDiscard(dirty)) fillNote(); });
}
