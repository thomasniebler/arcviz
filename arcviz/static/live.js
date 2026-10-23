// Live updates (spec: docs/architecture/specs/collaboration.md, ADR-0095): one server-sent-event stream per tab, for the
// shown diagram (else the shown project), read with fetch so status codes count; reconnects with a backoff. Someone
// else's change re-renders the diagram at once (the inspector keeps what the user typed, ADR-0099). Presence (#171):
// chips of the others, their selections as outlines on the canvas, a notice while they chat with the assistant.
import { api, apiFetch } from "./api.js";
import { onRendered } from "./canvas.js";
import { showChat } from "./chatlog.js";
import { liveBackoff } from "./conflicts.js";
import { onDiagramShown, onSelection, showDiagram } from "./diagram.js";
import { $ } from "./dom.js";
import { nodeIdForSvgId } from "./model.js";
import { chatNotice, selectionMarks, viewersToShow } from "./presence.js";
import { loadDiagramList, onProjectShown, refreshDiagramListLabel } from "./navigator.js";
import { readSse } from "./sse.js";
import { focusQuery, state } from "./state.js";
import { setStatus } from "./ui.js";
import { t } from "./i18n.js";

const RETRY_LATER = 30000; // after 429/503 (the caps, ADR-0098)
const SELECTION_DELAY = 250; // debounce of the selection sent to the others
const SVG_NS = "http://www.w3.org/2000/svg";
const REFRESH_DELAY = 150; // coalesce bursts (a chat turn writes several times)
const BLOCKED_RETRY = 1500; // a chat turn, proposal, preview or comparison is shown: try again later

let current = null; // {key, path, controller}
const eventHooks = [];
let refreshTimer = null, pending = null;
let presence = null; // the last presence snapshot of the open stream
let selected = null, sentSelection, selectionTimer = null; // what this tab shows in the inspector (presence)

/** `fn(event)` runs for every event of the stream (presence uses it). */
export function onLiveEvent(fn) { eventHooks.push(fn); }

/** This page's session id: in memory only, so a duplicated tab (which copies sessionStorage) gets its own. */
function session() {
  if (!state.liveSession) {
    state.liveSession = (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, "");
  }
  return state.liveSession;
}

let projectRevision = null; // of the project the stream watches, to notice changes missed while reconnecting

function target() {
  if (state.diagramId) return { key: `d:${state.diagramId}`, path: `/api/diagrams/${encodeURIComponent(state.diagramId)}/live` };
  if (state.projectId) return { key: `p:${state.projectId}`, path: `/api/projects/${encodeURIComponent(state.projectId)}/live` };
  return null;
}

/** Follow what the tab shows: (re)open the stream when the diagram or project changes. */
function follow() {
  const next = target();
  if (current?.key === next?.key) return;
  current?.controller.abort();
  current = next ? { ...next, controller: new AbortController() } : null;
  projectRevision = null;
  showPresence(null);
  if (current) run(current);
}

async function run(stream) {
  let attempt = 0, connected = false;
  while (current === stream) {
    let wait = liveBackoff(attempt);
    try {
      const res = await apiFetch(`${stream.path}?session=${encodeURIComponent(session())}`, { signal: stream.controller.signal, headers: { Accept: "text/event-stream" } });
      if (res.status === 404) { // not visible (any more)
        // a reconnect that finds it gone (deleted, moved out of sight) after a stream ended with `bye`: the same as
        // `gone` (#218), so the user learns it whichever way the last stream ended
        if (current === stream) { current = null; showPresence(null); if (connected) handle({ type: "gone" }); }
        return;
      }
      if (res.status === 429 || res.status === 503) wait = RETRY_LATER;
      else if (res.ok) {
        let ended = null;
        await readSse(res.body, (event) => {
          if (current !== stream) return;
          if (event.type === "hello") { attempt = 0; connected = true; }
          if (event.type === "gone" || event.type === "bye") ended = event;
          handle(event);
        });
        if (ended?.type === "gone") { if (current === stream) { current = null; showPresence(null); } return; }
        if (ended?.type === "bye" && ended.reason !== "error") wait = 200 + Math.random() * 800; // a normal end
      }
    } catch (err) {
      if (err?.name === "AbortError" || current !== stream) return;
    }
    attempt += 1;
    await new Promise((resolve) => setTimeout(resolve, wait));
  }
}

function handle(event) {
  if (event.type === "hello") {
    state.liveSession = event.session;
    sentSelection = undefined; sendSelection(); // a new stream starts without one
    // a reconnect: what changed while no stream was open
    if (event.diagram && event.diagram === state.diagramId && state.revision && event.revision !== state.revision) {
      scheduleRefresh({ content: true, author_name: null, origin: null, revision: event.revision });
    }
    if (event.project && state.projectId && event.project !== state.projectId) followProject(event.project);
    else if (projectRevision !== null && event.project_revision !== projectRevision) refreshDiagramListLabel().catch(() => {});
    projectRevision = event.project_revision ?? null;
  } else if (event.type === "presence") showPresence(event);
  else if (event.type === "project") projectRevision = event.revision ?? null;
  if (event.type === "changed" && event.revision === state.revision && event.author_name) {
    state.lastChange = { revision: event.revision, author_name: event.author_name }; // named after the poll saw it
    if (pending?.revision === event.revision) pending.author_name = event.author_name;
  } else if (event.type === "changed" && event.revision !== state.revision) {
    state.lastChange = { revision: event.revision, author_name: event.author_name ?? null };
    scheduleRefresh({ content: true, author_name: event.author_name ?? null, origin: event.origin ?? null, revision: event.revision });
  } else if (event.type === "access") {
    scheduleRefresh({ content: true, revision: null }); // the role changed: the detail says how
  } else if (event.type === "chat" && event.diagram) {
    scheduleRefresh({ chat: true });
  } else if (event.type === "project") {
    if (event.project && state.projectId && event.project !== state.projectId) followProject(event.project);
    else refreshDiagramListLabel().catch(() => {});
  } else if (event.type === "gone") {
    setStatus(t("Dieses Diagramm ist nicht mehr verfügbar."));
    loadDiagramList().catch(() => {});
  }
  for (const fn of eventHooks) fn(event);
}

/** The open diagram was moved into another project (still visible): show that project, keeping the diagram open,
 * so the navigator, the project chat and the model panel work on the right project. */
function followProject(projectId) {
  loadDiagramList(state.diagramId, projectId).catch(() => {});
}

function blocked() {
  return state.busy || state.proposal || state.previewVersion != null || state.compare;
}

/** Merge what has to be refreshed and refresh soon (bursts of events become one request). */
function scheduleRefresh(change) {
  const before = pending ?? {};
  pending = {
    content: Boolean(before.content || change.content),
    chat: Boolean(before.chat || change.chat),
    author_name: change.content ? change.author_name ?? null : before.author_name ?? null,
    origin: change.content ? change.origin ?? null : before.origin ?? null,
    revision: change.content ? change.revision ?? null : before.revision ?? null,
  };
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, REFRESH_DELAY);
}

async function refresh() {
  if (!pending || !state.diagramId) { pending = null; return; }
  if (blocked()) { refreshTimer = setTimeout(refresh, BLOCKED_RETRY); return; }
  const change = pending;
  pending = null;
  // this tab's own change is shown already (its answer carried the revision)
  const content = change.content && !(change.revision && change.revision === state.revision);
  if (!content && !change.chat) return;
  const id = state.diagramId;
  try {
    const detail = await api(`/api/diagrams/${encodeURIComponent(id)}${focusQuery()}`);
    if (state.diagramId !== id || blocked()) { scheduleRefresh(change); return; }
    const chatChanged = JSON.stringify(detail.chat?.at(-1) ?? null) !== JSON.stringify(shownChat);
    if (content) showDiagram(detail, { remote: { author_name: change.author_name, origin: change.origin } });
    if (state.chatScope === "diagram" && chatChanged) { shownChat = detail.chat.at(-1) ?? null; showChat(detail.chat); }
    refreshFailures = 0;
  } catch (err) {
    // The stream already reported this change and will not again: retry with a backoff (a 404 ends with `gone`).
    if (err?.status === 404 || state.diagramId !== id) return;
    refreshFailures += 1;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => scheduleRefresh(change), liveBackoff(refreshFailures - 1));
  }
}

let refreshFailures = 0;

let shownChat = null; // the last transcript entry shown, to redraw the chat only when it changed

// ---------- presence (#171) ----------------------------------------------------------------------

function showPresence(snapshot) {
  presence = snapshot;
  const box = $("presence");
  box.innerHTML = "";
  for (const viewer of viewersToShow(snapshot, state.me?.id, { project: state.chatScope === "project" })) {
    const chip = document.createElement("span");
    chip.className = `presence-chip${viewer.chatting ? " chatting" : ""}`;
    chip.style.setProperty("--c", viewer.color);
    chip.dataset.user = viewer.user;
    chip.textContent = viewer.initials; // names are free text: textContent only
    chip.title = viewer.chatting ? t("{name} (chattet mit dem Assistenten)", { name: viewer.name }) : viewer.name;
    box.appendChild(chip);
  }
  const notice = $("chat-presence");
  notice.textContent = chatNotice(snapshot, state.me?.id);
  notice.classList.toggle("hidden", !notice.textContent);
  drawMarks();
}

/** The SVG group of an element of the shown rendering (Mermaid node or cluster, or a sequence element). */
function elementGroup(svg, id) {
  if (state.view?.renderer === "svg") return [...svg.querySelectorAll("[data-id]")].find((g) => g.dataset.id === id) ?? null;
  for (const g of svg.querySelectorAll("g.node, g.cluster, g.icon-shape")) if (nodeIdForSvgId(g.id, state.mermaidIds) === id) return g;
  return null;
}

/** Outline every element someone else has selected, with their initials (re-drawn after every render). */
function drawMarks() {
  const svg = document.querySelector("#diagram svg");
  if (!svg) return;
  svg.querySelectorAll(".presence-overlay").forEach((n) => n.remove());
  if (state.compare || state.previewVersion != null) return;
  const offsets = new Map();
  for (const mark of selectionMarks(presence, state.me?.id)) {
    const group = elementGroup(svg, mark.element);
    if (!group?.getBBox) continue;
    let box;
    try { box = group.getBBox(); } catch { continue; }
    const n = offsets.get(mark.element) ?? 0; // several people on one element: nested outlines
    offsets.set(mark.element, n + 1);
    const pad = 4 + 4 * n;
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("class", "presence-mark");
    rect.setAttribute("x", box.x - pad); rect.setAttribute("y", box.y - pad);
    rect.setAttribute("width", box.width + 2 * pad); rect.setAttribute("height", box.height + 2 * pad);
    rect.setAttribute("rx", 6);
    // Mermaid styles the shapes of a node (also with !important on its direct children): the mark sits in its own
    // group and states its paint as important inline styles.
    for (const [k, v] of [["fill", "none"], ["stroke", mark.color], ["stroke-width", "2.5px"], ["stroke-dasharray", "5 3"]]) rect.style.setProperty(k, v, "important");
    rect.dataset.user = mark.user;
    const tag = document.createElementNS(SVG_NS, "text");
    tag.setAttribute("class", "presence-tag");
    tag.setAttribute("x", box.x - pad + 12 * n); tag.setAttribute("y", box.y - pad - 3);
    tag.style.setProperty("fill", mark.color, "important");
    tag.style.setProperty("stroke", "none", "important");
    tag.textContent = mark.initials;
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = mark.name;
    tag.appendChild(title);
    const overlay = document.createElementNS(SVG_NS, "g");
    overlay.setAttribute("class", "presence-overlay");
    overlay.append(rect, tag);
    group.appendChild(overlay);
  }
}

/** Tell the others what this tab shows in the inspector (debounced; only on a diagram stream). */
function sendSelection() {
  clearTimeout(selectionTimer);
  selectionTimer = setTimeout(async () => {
    if (!current?.key.startsWith("d:") || !state.liveSession || sentSelection === selected) return;
    const diagramId = current.key.slice(2);
    const value = selected;
    try {
      await api(`/api/diagrams/${encodeURIComponent(diagramId)}/presence`, { method: "POST", body: JSON.stringify({ session: state.liveSession, selection: value }) });
      sentSelection = value;
    } catch {
      // the stream is (re)connecting: hello sends it again
    }
  }, SELECTION_DELAY);
}

export function initLive() {
  onDiagramShown((detail) => { if (detail.chat) shownChat = detail.chat.at(-1) ?? null; follow(); });
  onProjectShown(() => follow());
  onRendered(drawMarks);
  onSelection((id) => { selected = id; sendSelection(); });
}
