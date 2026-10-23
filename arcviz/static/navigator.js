// Project navigator (spec: docs/architecture/specs/project-navigator.md, ADR-0055): project switcher, the
// section tree with its diagrams, and the actions that change which diagrams exist and where they are.
// Layer between the hub (diagram.js) and the features: features refresh the list through this module.
import { api, apiFetch, ifMatch } from "./api.js";
import { showChat } from "./chatlog.js";
import { changeAuthor, changedBy, confirmedDelete } from "./conflicts.js";
import { openDiagram, showDiagram } from "./diagram.js";
import { $, el, option } from "./dom.js";
import { N_, t } from "./i18n.js";
import { nextDiagramId, pickDiagram, pickProject, projectOptionLabel, sectionChoices, siblingMove, treeRows } from "./projects.js";
import { EMPTY_PROJECTS_TEXT, offerDemo, projectGroups, sharedLabel } from "./access.js";
import { focusQuery, state } from "./state.js";
import { TYPE_LABELS, templateChoices } from "./types.js";
import { carriedText } from "./sharedmodel.js";
import { setRole, setStatus } from "./ui.js";

const nav = { projects: [], view: null, changed: new Set() }; // changed: diagrams the last project turn changed
const projectHooks = [];

/** `fn(project)` runs after a project's view is shown (start, switch, import, deletion of the open one). */
export function onProjectShown(fn) { projectHooks.push(fn); }

/** The diagrams of the open project (`{id, title, type, …}`; empty before the first load). */
export function projectDiagrams() { return nav.view?.diagrams ?? []; }

/** The open project document (null before the first load). */
export function currentProjectInfo() { return nav.view?.project ?? null; }

function recall(key) { try { return localStorage.getItem(key); } catch { return null; } }
function remember(key, value) { try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch {} }

function collapsedOf(projectId) {
  try { return JSON.parse(recall(`arcviz.collapsed.${projectId}`) || "[]"); } catch { return []; }
}

/** Id of the open project (null before the first load). */
export function currentProject() { return nav.view?.project.id ?? null; }

/** The caller's own projects and those shared with them (spec: personal-workspaces.md). */
function projectListPath() { return "/api/projects"; }

/** Reload the projects and show `projectId` (default: the open, else the remembered, else the first one),
 * opening `selectId` if it is in that project, else the remembered or first diagram. */
export async function loadDiagramList(selectId, projectId) {
  const listPath = projectListPath();
  let projects = await api(listPath);
  if (offerDemo(projects, state.me, recall("arcviz.demo-offered"))) {
    // a fresh installation: the demo diagram in the admin's own default project (a diagram without a project)
    remember("arcviz.demo-offered", "1");
    const created = await api("/api/diagrams", { method: "POST", body: JSON.stringify({ title: "Demo deployment", id: "demo", template: "demo" }) }).catch(() => null);
    if (created) { projects = await api(listPath); selectId = created.diagram.id; }
  }
  if (!projects.length) { nav.projects = []; showNoProjects(); return; }
  nav.projects = projects;
  await showProject(pickProject(projects, projectId ?? currentProject(), recall("arcviz.project")), selectId);
}

/** No project to show: the caller owns none and nothing is shared with them. */
function showNoProjects() {
  nav.view = null;
  nav.changed.clear();
  state.projectId = null;
  $("project-select").innerHTML = "";
  $("project-description").textContent = "";
  $("nav-tree").innerHTML = "";
  setRole(null);
  emptyKind = "projects";
  setEmpty(true);
  for (const fn of projectHooks) fn(null);
}

/** Switch to another project and open its remembered (or first) diagram. */
export async function openProject(projectId) { await showProject(projectId, null); }

async function showProject(projectId, selectId) {
  const view = await api(`/api/projects/${encodeURIComponent(projectId)}`);
  setView(view);
  remember("arcviz.project", projectId);
  const diagramId = pickDiagram(view, selectId, recall("arcviz.diagram"));
  emptyKind = "project";
  if (diagramId) {
    setEmpty(false);
    if (diagramId !== state.diagramId || selectId) await openDiagram(diagramId);
  } else {
    setEmpty(true);
  }
  renderTree();
  for (const fn of projectHooks) await fn(view.project);
}

function setView(view) {
  if (nav.view?.project.id !== view.project.id) nav.changed.clear();
  nav.view = view;
  state.projectId = view.project.id;
  if (view.role !== undefined) setRole(view.role); // the caller's role decides what is editable (read-only mode)
  const summary = nav.projects.find((p) => p.id === view.project.id);
  if (summary) summary.diagram_count = view.diagrams.length;
  else nav.projects.push({ id: view.project.id, title: view.project.title, diagram_count: view.diagrams.length });
  fillProjectSelect(view.project.id);
  $("project-description").textContent = view.project.description || "";
  $("btn-project-delete").disabled = view.diagrams.length > 0;
  renderTree();
}

/** The project select: "Meine Projekte", then "Mit mir geteilt" (shared projects name their owner). */
function fillProjectSelect(selected) {
  const select = $("project-select");
  select.innerHTML = "";
  for (const group of projectGroups(nav.projects)) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = group.label;
    for (const p of group.projects) optgroup.appendChild(option(p.id, sharedLabel(p, projectOptionLabel(p)), p.id === selected));
    select.appendChild(optgroup);
  }
}

/** No diagram to show (an empty project): placeholder, workspace and diagram buttons inert. */
let projectEmpty = false;
let emptyKind = "project"; // what is empty: "project" (no diagram) or "projects" (the caller has no project at all)
const EMPTY_TEXT = {
  project: [N_("Dieses Projekt enthält noch keine Diagramme."), N_("Neues Diagramm anlegen")],
  projects: [EMPTY_PROJECTS_TEXT, N_("Neues Projekt anlegen")], // shown through t()
};

/** No diagram to show (an empty project): placeholder, workspace and diagram buttons inert. In the project-chat
 * scope only the canvas is inert, so the chat can create the first diagram (spec: project-chat.md). */
function setEmpty(empty) {
  projectEmpty = empty;
  if (!empty) emptyKind = "project";
  const [text, button] = EMPTY_TEXT[emptyKind];
  $("empty-text").textContent = t(text);
  $("btn-empty-new").textContent = t(button);
  $("empty-starters").classList.toggle("hidden", emptyKind !== "project"); // starters need a project to create in
  applyEmpty();
  if (empty) {
    el.viewport.innerHTML = ""; // nothing of the previous project's diagram stays visible
    el.source.textContent = "";
    state.diagramId = null;
    state.focus = null;
    state.undoTarget = null; state.redoTarget = null; // keyboard shortcuts check the buttons, not inert
    $("btn-undo").disabled = true; $("btn-redo").disabled = true;
    el.title.textContent = "";
    el.stats.textContent = "";
    if (state.chatScope === "diagram") showChat([]);
  }
}

/** Apply the empty-project state for the current chat scope (called again when the scope changes). */
export function applyEmpty() {
  const projectChat = ["project", "requirements"].includes(state.chatScope) && emptyKind === "project"; // without a project: nothing to chat in
  $("empty-project").classList.toggle("hidden", !projectEmpty || projectChat);
  document.querySelector(".layout").inert = projectEmpty && !projectChat;
  document.querySelector(".canvas").inert = projectEmpty && projectChat;
  for (const group of document.querySelectorAll(".tools")) group.inert = projectEmpty;
}

/** Reload the open project's view (titles, order) and redraw the tree; used after edits that rename. */
export async function refreshDiagramListLabel() {
  const projectId = currentProject();
  if (!projectId) return;
  setView(await api(`/api/projects/${encodeURIComponent(projectId)}`));
}

/** Open a diagram of the open project (the project chat follows the active diagram, the project review jumps to a
 * finding) and update the tree. */
export async function openInProject(diagramId) {
  nav.changed.delete(diagramId);
  if (diagramId !== state.diagramId) { setEmpty(false); await openDiagram(diagramId); }
  renderTree();
}

/** Mark the tree rows of diagrams a project turn changed ("geändert" until the diagram is opened). */
export function markChanged(diagramIds) {
  for (const id of diagramIds || []) if (id !== state.diagramId) nav.changed.add(id);
  renderTree();
}

/** Open the next (`delta` 1) or previous (-1) diagram of the open project (voice commands). */
export async function nextDiagram(delta = 1) {
  if (!nav.view) return;
  const id = nextDiagramId(nav.view, state.diagramId, delta);
  if (id) { await openDiagram(id); renderTree(); }
}

// ---------- tree ----------------------------------------------------------------

function menuButton(action, label, { disabled = false, danger = false, ownerOnly = false } = {}) {
  const b = document.createElement("button");
  b.type = "button"; b.dataset.action = action; b.textContent = label; b.disabled = disabled;
  if (danger) b.className = "danger";
  if (ownerOnly) b.classList.add("owner-only"); // hidden unless the caller owns the project (read-only mode)
  return b;
}

function rowMenu(buttons) {
  const details = document.createElement("details");
  details.className = "menu nav-menu";
  const summary = document.createElement("summary");
  summary.textContent = "⋯"; summary.title = t("Aktionen"); summary.setAttribute("aria-label", t("Aktionen"));
  const items = document.createElement("div");
  items.className = "menu-items";
  items.append(...buttons);
  details.append(summary, items);
  return details;
}

function renderTree() {
  const list = $("nav-tree");
  // a redraw (often a live event) keeps the row menu the user has open, instead of closing it under the pointer (#216)
  const open = list.querySelector(".nav-menu[open]")?.closest("li");
  const reopen = open ? (open.dataset.sectionId ? `s:${open.dataset.sectionId}` : `d:${open.dataset.diagramId}`) : null;
  list.innerHTML = "";
  if (!nav.view) return;
  const rows = treeRows(nav.view.tree, nav.view.diagrams, collapsedOf(nav.view.project.id));
  for (const row of rows) {
    const li = document.createElement("li");
    li.setAttribute("role", "treeitem");
    li.setAttribute("aria-level", String(row.depth + 1));
    li.style.setProperty("--depth", String(row.depth));
    li.dataset.index = String(row.index);
    li.dataset.count = String(row.count);
    const up = { disabled: siblingMove(row, -1) === null }, down = { disabled: siblingMove(row, 1) === null };
    if (row.kind === "section") {
      li.className = `nav-row nav-section${row.empty ? " empty" : ""}`;
      li.dataset.sectionId = row.id;
      li.setAttribute("aria-expanded", String(!row.collapsed));
      const toggle = document.createElement("button");
      toggle.type = "button"; toggle.className = "nav-toggle"; toggle.dataset.action = "toggle";
      toggle.textContent = row.collapsed ? "▸" : "▾";
      toggle.setAttribute("aria-label", row.collapsed ? t("Abschnitt aufklappen") : t("Abschnitt zuklappen"));
      const title = document.createElement("span");
      title.className = "nav-title"; title.textContent = row.title;
      const add = document.createElement("button");
      add.type = "button"; add.className = "nav-add"; add.dataset.action = "add-diagram"; add.textContent = "+";
      add.title = t("Neues Diagramm in diesem Abschnitt"); add.setAttribute("aria-label", add.title);
      li.append(toggle, title, add, rowMenu([
        menuButton("add-diagram", t("Neues Diagramm hier…")),
        menuButton("add-section", t("Unterabschnitt…")),
        menuButton("rename-section", t("Umbenennen…")),
        menuButton("section-up", t("Nach oben"), up),
        menuButton("section-down", t("Nach unten"), down),
        menuButton("delete-section", t("Abschnitt löschen"), { danger: true }),
      ]));
    } else {
      li.className = "nav-row nav-diagram";
      li.dataset.diagramId = row.id;
      if (row.id === state.diagramId) li.setAttribute("aria-current", "true");
      const open = document.createElement("button");
      open.type = "button"; open.className = "nav-open"; open.dataset.action = "open";
      open.textContent = row.title; open.title = t("„{title}“ öffnen", { title: row.title });
      li.append(open);
      if (nav.changed.has(row.id)) { // changed by the last project-chat turn, not opened since
        li.classList.add("changed");
        const badge = document.createElement("span");
        badge.className = "nav-changed"; badge.textContent = t("geändert"); badge.title = t("Im Projekt-Chat geändert");
        li.append(badge);
      }
      li.append(rowMenu([
        menuButton("rename-diagram", t("Umbenennen…")),
        menuButton("move-diagram", t("Verschieben…")),
        menuButton("diagram-up", t("Nach oben"), up),
        menuButton("diagram-down", t("Nach unten"), down),
        menuButton("delete-diagram", t("Diagramm löschen"), { danger: true, ownerOnly: true }),
      ]));
    }
    if (reopen === (row.kind === "section" ? `s:${row.id}` : `d:${row.id}`)) li.querySelector(".nav-menu").open = true;
    list.appendChild(li);
  }
}

// ---------- dialogs -------------------------------------------------------------

/** Ask for a name in #name-dialog; resolves to the trimmed text, or null when cancelled. */
export function askName(heading, value = "", { label = t("Name"), optional = false } = {}) {
  const dialog = $("name-dialog"), input = $("name-input");
  $("name-heading").textContent = heading;
  $("name-label").textContent = label;
  input.value = value;
  input.required = !optional;
  return new Promise((resolve) => {
    const done = (result) => { dialog.removeEventListener("close", onClose); resolve(result); };
    const onClose = () => done(dialog.returnValue === "ok" ? input.value.trim() : null);
    dialog.addEventListener("close", onClose);
    dialog.returnValue = "";
    dialog.showModal();
    input.select();
  });
}

function fillSections(select, tree, selected = "") {
  select.innerHTML = "";
  for (const c of sectionChoices(tree)) select.appendChild(option(c.value, c.label, c.value === (selected || "")));
}

let moving = null; // diagram id the move dialog is open for

async function openMoveDialog(diagramId) {
  moving = diagramId;
  const projectSelect = $("move-project");
  projectSelect.innerHTML = "";
  for (const p of nav.projects) projectSelect.appendChild(option(p.id, p.title, p.id === currentProject()));
  const member = nav.view.diagrams.find((d) => d.id === diagramId);
  $("move-heading").textContent = t("„{title}“ verschieben", { title: member?.title ?? diagramId });
  fillSections($("move-section"), nav.view.tree, member?.section ?? "");
  $("move-dialog").showModal();
}

const TEMPLATE_LABELS = {
  demo: N_("Demo (Deployment mit Services)"),
  "hub-spoke": N_("Hub-and-Spoke-Netzwerk (Hub-VNet, zwei Spokes, VPN)"),
  "aks-database": N_("AKS mit Datenbank (Cluster, Node-Pool, PostgreSQL)"),
  "event-driven": N_("Ereignisgesteuert (Topic, Functions, Abonnenten)"),
  "order-process": N_("Bestellprozess (Partitionen, Entscheidung, Fork/Join)"),
  "order-lifecycle": N_("Bestellung (Zustände, Regionen, Join, Guard)"),
  "webshop-domain": N_("Webshop-Fachmodell (Pakete, Komposition, Vererbung, Schnittstelle)"),
  "webshop-use-cases": N_("Webshop-Anwendungsfälle (System, Akteure, include, extend)"),
  "webshop-components": N_("Webshop-Bausteine (Teilsystem, Ports, Schnittstellen)"),
  checkout: N_("Checkout (Lebenslinien, alt, loop, ref, Aktivierungen)"),
  empty: N_("Leer"),
};

/** The new-diagram dialog: every type from /api/config (the shown one preselected), the sections of the open project. */
function openNewDialog(section = "") {
  $("new-title").value = "";
  const select = $("new-type");
  select.innerHTML = "";
  for (const type of Object.keys(state.config.diagram_types || { deployment: {} })) select.appendChild(option(type, TYPE_LABELS[type] || type, type === state.type));
  fillTemplates();
  fillSections($("new-section"), nav.view?.tree, section);
  $("new-dialog").showModal();
}

function fillTemplates() {
  const select = $("new-template");
  select.innerHTML = "";
  const labels = Object.fromEntries(Object.entries(TEMPLATE_LABELS).map(([k, v]) => [k, t(v)]));
  for (const [value, label] of templateChoices(state.config, $("new-type").value, labels)) select.appendChild(option(value, label));
}

// ---------- actions -------------------------------------------------------------

function projectPath(...parts) {
  return `/api/projects/${[currentProject(), ...parts].map((p) => encodeURIComponent(p)).join("/")}`;
}

export async function guarded(action) {
  try { await action(); } catch (err) { alert(err.message || String(err)); }
}

async function createProject() {
  const name = await askName(t("Neues Projekt"));
  if (!name) return;
  const created = await api("/api/projects", { method: "POST", body: JSON.stringify({ title: name }) });
  await loadDiagramList(null, created.project.id);
}

/** Who changed a diagram detail last, as far as this tab can tell (`conflicts.changeAuthor`: never the caller). */
function lastAuthorOf(detail) {
  return changeAuthor({ revision: detail.revision, lastChange: state.lastChange, versions: detail.versions, me: state.me });
}

/** Delete a diagram conditionally on the revision the user was asked about (#186): the shown one's, else read now; a
 * 412 reloads it (the shown one is shown again), says who changed it and asks again. */
async function deleteDiagram(diagramId, title) {
  const path = `/api/diagrams/${encodeURIComponent(diagramId)}`;
  const shown = () => diagramId === state.diagramId;
  const reload = async () => {
    const detail = await api(`${path}${shown() ? focusQuery() : ""}`);
    if (shown()) showDiagram(detail);
    return { revision: detail.revision, author: lastAuthorOf(detail) };
  };
  const current = shown() && state.revision ? { revision: state.revision } : await reload();
  const deleted = await confirmedDelete({
    current,
    reload,
    ask: async (text) => confirm(text),
    remove: (revision) => api(path, { method: "DELETE", headers: ifMatch(revision) }),
    question: t("Diagramm „{title}“ mit allen Versionen löschen?", { title }),
    changed: (who) => t("Diagramm „{title}“ wurde inzwischen von {who} geändert. Trotzdem mit allen Versionen löschen?", { title, who: changedBy(who) }),
  });
  if (!deleted) return;
  if (diagramId === state.diagramId) await loadDiagramList();
  else await refreshDiagramListLabel();
}

async function rowAction(action, li) {
  const sectionId = li.dataset.sectionId, diagramId = li.dataset.diagramId;
  const row = { index: Number(li.dataset.index), count: Number(li.dataset.count) };
  const title = li.querySelector(".nav-title, .nav-open")?.textContent ?? "";
  switch (action) {
    case "open": await openInProject(diagramId); break;
    case "toggle": {
      const collapsed = new Set(collapsedOf(currentProject()));
      if (collapsed.has(sectionId)) collapsed.delete(sectionId); else collapsed.add(sectionId);
      remember(`arcviz.collapsed.${currentProject()}`, JSON.stringify([...collapsed]));
      renderTree();
      break;
    }
    case "add-diagram": openNewDialog(sectionId); break;
    case "add-section": {
      const name = await askName(t("Neuer Unterabschnitt"));
      if (name) setView(await api(projectPath("sections"), { method: "POST", body: JSON.stringify({ title: name, parent: sectionId }) }));
      break;
    }
    case "rename-section": {
      const name = await askName(t("Abschnitt umbenennen"), title);
      if (name && name !== title) setView(await api(projectPath("sections", sectionId), { method: "PATCH", body: JSON.stringify({ title: name }) }));
      break;
    }
    case "section-up": case "section-down": {
      const position = siblingMove(row, action === "section-up" ? -1 : 1);
      if (position !== null) setView(await api(projectPath("sections", sectionId), { method: "PATCH", body: JSON.stringify({ position }) }));
      break;
    }
    case "delete-section":
      if (!confirm(t("Abschnitt „{title}“ löschen? Seine Diagramme und Unterabschnitte wandern eine Ebene nach oben.", { title }))) break;
      setView(await api(projectPath("sections", sectionId), { method: "DELETE" }));
      break;
    case "rename-diagram": {
      const name = await askName(t("Diagramm umbenennen"), title);
      if (!name || name === title) break;
      const detail = await api(`/api/diagrams/${encodeURIComponent(diagramId)}/ops${diagramId === state.diagramId ? focusQuery() : ""}`, {
        method: "POST", body: JSON.stringify({ op: "rename_diagram", args: { title: name } }),
      });
      if (diagramId === state.diagramId) showDiagram(detail);
      await refreshDiagramListLabel();
      break;
    }
    case "move-diagram": await openMoveDialog(diagramId); break;
    case "diagram-up": case "diagram-down": {
      const position = siblingMove(row, action === "diagram-up" ? -1 : 1);
      if (position !== null) setView(await api(projectPath("diagrams", diagramId, "move"), { method: "POST", body: JSON.stringify({ position }) }));
      break;
    }
    case "delete-diagram": await deleteDiagram(diagramId, title); break;
  }
}

function setNavigatorShown(shown) {
  $("navigator").classList.toggle("hidden", !shown);
  $("btn-navigator").setAttribute("aria-expanded", String(shown));
  remember("arcviz.navigator", shown ? null : "0");
}

export function initNavigator() {
  setNavigatorShown(recall("arcviz.navigator") !== "0");
  $("btn-navigator").addEventListener("click", () => setNavigatorShown($("navigator").classList.contains("hidden")));

  $("nav-tree").addEventListener("click", (e) => {
    const target = e.target.closest("[data-action]");
    const li = e.target.closest("li");
    if (!target || !li) return;
    target.closest("details")?.removeAttribute("open");
    guarded(() => rowAction(target.dataset.action, li));
  });

  $("project-select").addEventListener("change", () => guarded(() => openProject($("project-select").value)));
  $("btn-project-new").addEventListener("click", () => guarded(createProject));
  const closeProjectMenu = () => $("project-menu").removeAttribute("open");
  $("btn-project-rename").addEventListener("click", () => guarded(async () => {
    closeProjectMenu();
    const name = await askName(t("Projekt umbenennen"), nav.view.project.title);
    if (!name || name === nav.view.project.title) return;
    const view = await api(projectPath(), { method: "PATCH", body: JSON.stringify({ title: name }) });
    nav.projects.find((p) => p.id === view.project.id).title = view.project.title;
    setView(view);
  }));
  $("btn-project-describe").addEventListener("click", () => guarded(async () => {
    closeProjectMenu();
    const text = await askName(t("Projektbeschreibung"), nav.view.project.description, { label: t("Beschreibung"), optional: true });
    if (text === null) return;
    setView(await api(projectPath(), { method: "PATCH", body: JSON.stringify({ description: text }) }));
  }));
  $("btn-project-delete").addEventListener("click", () => guarded(async () => {
    closeProjectMenu();
    const deleted = currentProject(), title = nav.view.project.title;
    // conditional on the project revision the user was asked about (#186); a 412 shows the project again and asks again
    const removed = await confirmedDelete({
      current: { revision: nav.view.revision },
      reload: async () => {
        const view = await api(projectPath()); // a failure rejects: shown by `guarded`
        setView(view);
        if (view.diagrams.length) return { refused: t("Projekt „{title}“ enthält inzwischen Diagramme und wird nicht gelöscht.", { title }) };
        return { revision: view.revision, author: null };
      },
      ask: async (text) => confirm(text),
      remove: (revision) => api(`/api/projects/${encodeURIComponent(deleted)}`, { method: "DELETE", headers: ifMatch(revision) }),
      question: t("Projekt „{title}“ löschen?", { title }),
      tell: async (text) => alert(text),
      changed: (who) => t("Projekt „{title}“ wurde inzwischen von {who} geändert. Trotzdem löschen?", { title, who: changedBy(who) }),
    });
    if (!removed) return;
    remember(`arcviz.collapsed.${deleted}`, null);
    nav.view = null;
    await loadDiagramList(null, null);
  }));
  $("btn-project-export").addEventListener("click", () => guarded(async () => {
    closeProjectMenu();
    const res = await apiFetch(projectPath("export"));
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || `${currentProject()}.arcviz-project.json`;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }));
  const importFile = $("import-project-file");
  $("btn-project-import").addEventListener("click", () => { closeProjectMenu(); importFile.value = ""; importFile.click(); });
  importFile.addEventListener("change", () => guarded(async () => {
    const file = importFile.files?.[0];
    if (!file) return;
    let bundle;
    try { bundle = JSON.parse(await file.text()); } catch (err) { throw new Error(t("„{name}“ ist keine gültige JSON-Datei: {message}", { name: file.name, message: err.message })); }
    const view = await api("/api/projects/import", { method: "POST", body: JSON.stringify({ bundle }) });
    await loadDiagramList(null, view.project.id);
  }));
  $("btn-section-new").addEventListener("click", () => guarded(async () => {
    const name = await askName(t("Neuer Abschnitt"));
    if (name) setView(await api(projectPath("sections"), { method: "POST", body: JSON.stringify({ title: name }) }));
  }));

  $("name-cancel").addEventListener("click", () => $("name-dialog").close());
  $("move-cancel").addEventListener("click", () => $("move-dialog").close());
  $("move-project").addEventListener("change", () => guarded(async () => {
    const target = $("move-project").value;
    const tree = target === currentProject() ? nav.view.tree : (await api(`/api/projects/${encodeURIComponent(target)}`)).tree;
    fillSections($("move-section"), tree);
  }));
  $("move-form").addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(async () => {
      const project = $("move-project").value, section = $("move-section").value || null;
      const view = await api(projectPath("diagrams", moving, "move"), { method: "POST", body: JSON.stringify({ project, section }) });
      $("move-dialog").close();
      if (project === currentProject()) { setView(view); return; }
      const carried = carriedText(view.carried); // the linked elements and relations that came along (#153)
      if (carried) setStatus(carried);
      if (moving === state.diagramId) await loadDiagramList(moving, project); // follow the open diagram
      else { nav.projects = await api(projectListPath()); await refreshDiagramListLabel(); }
    });
  });

  // New diagram (topbar button, a section's add button, the empty-project placeholder) and deletion.
  $("btn-new").addEventListener("click", () => openNewDialog());
  $("btn-empty-new").addEventListener("click", () => {
    if (emptyKind === "projects") guarded(createProject);
    else openNewDialog();
  });
  $("new-type").addEventListener("change", fillTemplates);
  $("new-cancel").addEventListener("click", () => $("new-dialog").close());
  $("new-form").addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(async () => {
      const body = { title: $("new-title").value.trim(), type: $("new-type").value, template: $("new-template").value, project: currentProject(), section: $("new-section").value || null };
      const created = await api("/api/diagrams", { method: "POST", body: JSON.stringify(body) });
      $("new-dialog").close();
      await loadDiagramList(created.diagram.id);
    });
  });
  $("btn-delete").addEventListener("click", () => guarded(() => deleteDiagram(state.diagramId, el.title.textContent)));
}
