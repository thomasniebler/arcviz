// The command palette (spec: docs/architecture/specs/command-palette.md, ADR-0197): a modal dialog with a text field and a
// grouped result list over the registry of palette.js plus the diagrams of the open project. Commands run by clicking the
// existing control, setting the location hash or opening a link; what a control cannot do now is not offered. Everything
// is built with DOM APIs: diagram titles are free text and must never reach innerHTML.
import { $ } from "./dom.js";
import { t } from "./i18n.js";
import { currentProject, openInProject, projectDiagrams } from "./navigator.js";
import { COMMANDS, GROUPS, rankCommands, rememberCommand } from "./palette.js";
import { state } from "./state.js";

const RECENT_KEY = "arcviz.palette.recent";

let candidates = []; // the commands that can run now, collected when the palette opens
let rows = []; // what the list shows: {command, ranges, recent?}
let active = 0;
let opener = null; // where the focus returns

function recents() {
  try { const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); return Array.isArray(v) ? v : []; } catch { return []; }
}

function remember(id) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(rememberCommand(recents(), id))); } catch {}
}

const control = (command) => (command.click ? document.getElementById(command.click) : null);

/** Whether the command can run right now: a project is open, its control exists, is enabled and not hidden or inert. */
function available(command) {
  if (command.needs === "project" && !currentProject()) return false;
  if (command.diagram) return !state.busy; // no control behind it that the busy UI would lock: a pending proposal or a streaming turn must not lose its chat
  if (!("click" in command)) return true;
  const el = control(command);
  if (!el || el.disabled || el.hidden || el.closest("[inert], .hidden")) return false;
  if (document.body.classList.contains("readonly") && el.closest(".edit-only")) return false;
  if (document.body.classList.contains("not-owner") && el.closest(".owner-only")) return false;
  return true;
}

/** A registry command with its label and hint in the current language (diagram entries hold user text and stay as they are). */
function translated(command) {
  if (command.diagram) return command;
  return { ...command, label: t(command.label), ...(command.hint ? { hint: t(command.hint) } : {}) };
}

function diagramCommands() {
  return projectDiagrams().map((d) => ({ id: `diagram:${d.id}`, label: d.title, group: "Diagramme", keywords: d.type || "", hint: d.type || "", diagram: d.id }));
}

function highlighted(text, ranges) {
  const out = document.createDocumentFragment();
  let at = 0;
  for (const [from, to] of ranges) {
    if (from > at) out.appendChild(document.createTextNode(text.slice(at, from)));
    const mark = document.createElement("mark");
    mark.textContent = text.slice(from, to);
    out.appendChild(mark);
    at = to;
  }
  if (at < text.length) out.appendChild(document.createTextNode(text.slice(at)));
  return out;
}

function setActive(index, scroll = true) {
  active = index;
  const items = $("palette-list").querySelectorAll(".palette-item");
  items.forEach((li, i) => li.setAttribute("aria-selected", String(i === index)));
  const current = items[index];
  $("palette-input").setAttribute("aria-activedescendant", current ? current.id : "");
  if (scroll && current) current.scrollIntoView({ block: "nearest" });
}

function render() {
  const query = $("palette-input").value;
  rows = rankCommands(candidates, query, recents());
  const list = $("palette-list");
  list.replaceChildren();
  $("palette-empty").classList.toggle("hidden", rows.length > 0);
  const grouped = query.trim() === ""; // headings only for the browsing view; a search is one list by match quality
  const order = grouped ? [...rows].sort((a, b) => groupRank(a) - groupRank(b)) : rows;
  rows = order;
  let heading = null;
  order.forEach((row, i) => {
    const group = row.recent ? "Zuletzt" : row.command.group; // German source text of the group
    if (grouped && group !== heading) {
      heading = group;
      const h = document.createElement("li");
      h.className = "palette-group"; h.setAttribute("role", "presentation"); h.textContent = t(group);
      list.appendChild(h);
    }
    const li = document.createElement("li");
    li.className = "palette-item"; li.id = `palette-opt-${i}`; li.setAttribute("role", "option");
    const label = document.createElement("span");
    label.className = "palette-label";
    label.appendChild(highlighted(row.command.label, row.ranges));
    const hint = document.createElement("span");
    hint.className = "palette-hintcol";
    hint.textContent = row.command.hint || (grouped ? "" : t(row.command.group));
    li.append(label, hint);
    li.addEventListener("pointermove", () => { if (active !== i) setActive(i, false); });
    li.addEventListener("click", () => run(row.command));
    list.appendChild(li);
  });
  setActive(0, false);
}

const groupRank = (row) => (row.recent ? 0 : GROUPS.indexOf(row.command.group));

function exec(command) {
  if (command.diagram) {
    if (state.busy) return; // became busy since the palette opened
    if (state.mainView !== "diagram") location.hash = ""; // the shell switches the view; the diagram opens meanwhile
    openInProject(command.diagram);
  } else if ("click" in command) {
    control(command)?.click();
  } else if ("route" in command) {
    location.hash = command.route;
  } else if ("href" in command) {
    window.open(command.href, "_blank", "noopener");
  }
}

function run(command) {
  remember(command.id);
  opener = null; // a dialog the command opens takes the focus itself
  $("palette").close();
  setTimeout(() => exec(command), 0);
}

function open() {
  const dialog = $("palette");
  if (dialog.open) return;
  opener = document.activeElement;
  candidates = [...COMMANDS, ...diagramCommands()].filter(available).map(translated);
  $("palette-input").value = "";
  dialog.showModal();
  render();
  $("palette-input").focus();
}

function toggle() {
  const dialog = $("palette");
  if (dialog.open) dialog.close(); else open();
}

export function initPalette() {
  const dialog = $("palette");
  const input = $("palette-input");
  $("palette-kbd").textContent = /Mac|iPhone|iPad/.test(navigator.platform || "") ? "⌘K" : t("Strg K");
  $("palette-open").addEventListener("click", open);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") { e.preventDefault(); toggle(); }
  });
  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (rows.length) setActive((active + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (rows[active]) run(rows[active].command);
    }
  });
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); }); // the backdrop
  dialog.addEventListener("close", () => { opener?.focus?.(); opener = null; });
}
