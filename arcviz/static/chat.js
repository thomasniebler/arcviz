// Chat turn: POST /chat read as server-sent events, live renders, and propose mode (confirm or
// discard a previewed change). The pane shows the diagram chat or the project chat (state.chatScope,
// spec: docs/architecture/specs/project-chat.md); a project turn moves the canvas to the diagram it edits.
import { api, apiFetch, errorText } from "./api.js";
import { renderView } from "./canvas.js";
import { addMessage, makeToolLine, setRichText, showChat } from "./chatlog.js";
import { isStaleView, showDiagram } from "./diagram.js";
import { $, el } from "./dom.js";
import { applyEmpty, currentProjectInfo, markChanged, onProjectShown, openInProject, refreshDiagramListLabel } from "./navigator.js";
import { refreshRequirements } from "./requirementview.js";
import { finalMeta, toolError } from "./format.js";
import { changedDiagramItems, diagramSwitch, scopeLabel } from "./projectchat.js";
import { viewOf } from "./types.js";
import { describeDiff } from "./proposal.js";
import { createReplyBuffer } from "./reply.js";
import { readSse } from "./sse.js";
import { focusQuery, state } from "./state.js";
import { setBusy, setStatus } from "./ui.js";
import { endCompare } from "./versions.js";
import { speakReply } from "./voice.js";
import { t } from "./i18n.js";

function projectPath(...parts) {
  return `/api/projects/${[state.projectId, ...parts].map((p) => encodeURIComponent(p)).join("/")}`;
}

/** Switch the chat pane between the open diagram's transcript, the project's and the requirements chat's. */
export async function setChatScope(scope) {
  if (state.busy) return;
  state.chatScope = ["project", "requirements"].includes(scope) ? scope : "diagram";
  const project = state.chatScope === "project";
  const requirements = state.chatScope === "requirements";
  // only the diagram and the project scope are remembered: the requirements chat belongs to its view
  try { if (!requirements) localStorage.setItem("arcviz.chatScope", state.chatScope); } catch {}
  $("btn-project-chat").setAttribute("aria-pressed", String(project));
  $("chat-scope").classList.toggle("hidden", !project && !requirements);
  $("chat-scope-label").textContent = requirements ? requirementsScopeLabel(currentProjectInfo()) : scopeLabel(currentProjectInfo());
  $("btn-chat-scope-back").textContent = requirements ? t("Zum Diagramm") : t("Zum Diagramm-Chat");
  applyEmpty(); // an empty project: the project chat stays usable, only the canvas is inert
  if (requirements) {
    if (state.projectId) showChat((await api(projectPath("requirements", "chat"))).chat, { scope: "requirements" });
  } else if (project) {
    if (state.projectId) showChat((await api(projectPath("chat"))).chat, { scope: "project" });
  } else if (state.diagramId) {
    showChat((await api(`/api/diagrams/${state.diagramId}`)).chat);
  } else {
    showChat([]);
  }
}

function requirementsScopeLabel(project) {
  return project?.title ? t("Anforderungs-Chat · {title}", { title: project.title }) : t("Anforderungs-Chat");
}

/** The diagrams a project turn changed, as buttons that open them, below the reply. */
function changedList(diagrams) {
  const items = changedDiagramItems(diagrams);
  if (!items.length) return null;
  const box = document.createElement("div");
  box.className = "changed-diagrams";
  const label = document.createElement("span");
  label.textContent = t("Geändert:");
  box.appendChild(label);
  for (const item of items) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = item.label; b.dataset.diagram = item.id; b.title = t("Diagramm öffnen");
    b.addEventListener("click", () => { if (!state.busy) openInProject(item.id); });
    box.appendChild(b);
  }
  return box;
}

/** One turn of the requirements chat: the same events, no diagram; the view reads the requirements again afterwards. */
async function sendRequirementsMessage(text) {
  addMessage("user", text);
  setBusy(true);
  setStatus(t("Assistent arbeitet …"));
  const pending = addMessage("assistant", "…");
  let gotFinal = false;
  try {
    const res = await apiFetch(projectPath("requirements", "chat"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(errorText(res.status, body.detail || res.statusText));
    }
    const reply = createReplyBuffer();
    await readSse(res.body, async (ev) => {
      if (ev.type === "text_delta") { const chunk = reply.delta(ev.text); if (chunk) setRichText(pending, chunk); }
      else if (ev.type === "text") { const chunk = reply.round(ev.text); if (chunk) setRichText(pending, chunk); }
      else if (ev.type === "tool_call") { setStatus(t("Arbeite: {name} …", { name: ev.name })); el.messages.insertBefore(makeToolLine(ev), pending); }
      else if (ev.type === "tool_result") {
        const node = el.messages.querySelector(`[data-call="${ev.id}"]`);
        if (node && ev.is_error) { node.classList.add("failed"); node.textContent += `  ✗ ${toolError(ev.content)}`; }
        else if (node) node.textContent += "  ✓";
      }
      else if (ev.type === "error") { pending.className = "msg error"; pending.textContent = ev.message; }
      else if (ev.type === "final") {
        gotFinal = true;
        if (ev.reply) setRichText(pending, ev.reply);
        else if (!ev.error) pending.remove();
        if (ev.error && pending.isConnected && !ev.reply) { pending.className = "msg error"; pending.textContent = ev.error; }
        await refreshRequirements();
        if ((ev.operations || []).some((op) => op.op === "derive_requirements")) document.dispatchEvent(new CustomEvent("arcviz:proposals-changed"));
      }
      el.messages.scrollTop = el.messages.scrollHeight;
    });
    if (!gotFinal && pending.textContent === "…") { pending.className = "msg error"; pending.textContent = t("Verbindung endete ohne Antwort."); }
  } catch (err) {
    pending.className = "msg error";
    pending.textContent = t("Fehler: {error}", { error: err.message || err });
  } finally {
    setBusy(false);
    setStatus("");
  }
}

/** Run one chat turn. `propose` (default: the Vorschau checkbox) makes it a dry run shown as a proposal. */
export async function sendMessage(text, { propose = $("propose-mode").checked } = {}) {
  if (state.chatScope === "requirements") return sendRequirementsMessage(text);
  endCompare(); // live renders replace the comparison
  addMessage("user", text);
  setBusy(true);
  setStatus(t("Modell arbeitet …"));
  const pending = addMessage("assistant", "…");
  const project = state.chatScope === "project";
  let gotFinal = false;
  try {
    const mode = propose ? "propose" : "apply";
    const [path, body] = project
      ? [projectPath("chat"), { message: text, active: state.diagramId, focus: state.focus, mode }]
      : [`/api/diagrams/${state.diagramId}/chat`, { message: text, focus: state.focus, mode }];
    const res = await apiFetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(errorText(res.status, body.detail || res.statusText));
    }
    const reply = createReplyBuffer();
    await readSse(res.body, async (ev) => {
      if (ev.type === "text_delta") { const chunk = reply.delta(ev.text); if (chunk) setRichText(pending, chunk); }
      else if (ev.type === "text") { const chunk = reply.round(ev.text); if (chunk) setRichText(pending, chunk); }
      else if (ev.type === "tool_call") { setStatus(t("Wende an: {name} …", { name: ev.name })); el.messages.insertBefore(makeToolLine(ev), pending); }
      else if (ev.type === "tool_result") {
        const node = el.messages.querySelector(`[data-call="${ev.id}"]`);
        if (node && ev.is_error) { node.classList.add("failed"); node.textContent += `  ✗ ${toolError(ev.content)}`; }
        else if (node) node.textContent += "  ✓";
        const other = project && !ev.is_error ? diagramSwitch(ev, state.diagramId) : null;
        if (other) await openInProject(other); // the turn works on another diagram now: show it first
        // a live render for the focus the turn started with is skipped once the user has changed it (#216)
        if (ev.renderer && !isStaleView(ev)) { if (ev.focus !== undefined) state.focus = ev.focus; renderView(viewOf(ev)); }
      }
      else if (ev.type === "error") { pending.className = "msg error"; pending.textContent = ev.message; }
      else if (ev.type === "final") {
        gotFinal = true;
        if (ev.reply) { setRichText(pending, ev.reply); speakReply(ev.reply); }
        else if (!ev.error) pending.remove();
        const meta = finalMeta(project ? { ...ev, version: null } : ev); // project turns list versions per diagram below
        if (meta && pending.isConnected) { const m = document.createElement("span"); m.className = "meta"; m.textContent = meta; pending.appendChild(m); }
        if (project) {
          const list = changedList(ev.diagrams);
          if (list && pending.isConnected) pending.appendChild(list);
          const target = ev.proposal?.diagram_id || ev.active;
          if (target && target !== state.diagramId) await openInProject(target);
          markChanged((ev.diagrams || []).map((d) => d.id));
        }
        if (ev.focus !== undefined && !isStaleView(ev)) state.focus = ev.focus;
        if (state.diagramId) {
          const detail = await api(`/api/diagrams/${state.diagramId}${focusQuery()}`);
          // the highlight is the turn's render, for the focus it started with: dropped once the user left that (#216)
          const own = ev.renderer && (!project || ev.diagram_id === state.diagramId) && !isStaleView(ev);
          showDiagram(detail, { highlight: own ? viewOf(ev) : undefined });
        }
        if (ev.proposal) showProposal(pending, ev.proposal, text, project);
        else await refreshDiagramListLabel();
      }
      el.messages.scrollTop = el.messages.scrollHeight;
    });
    if (!gotFinal && pending.textContent === "…") { pending.className = "msg error"; pending.textContent = t("Verbindung endete ohne Antwort."); }
  } catch (err) {
    pending.className = "msg error";
    pending.textContent = t("Fehler: {error}", { error: err.message || err });
  } finally {
    setBusy(Boolean(state.proposal)); // a pending proposal keeps the UI locked until it is confirmed or discarded
    setStatus(state.proposal ? t("Vorschlag prüfen: übernehmen oder verwerfen") : "");
  }
}

// ---------- propose mode: confirm or discard a previewed change ------------------------------

function showProposal(bubble, proposal, userText, project = false) {
  state.proposal = proposal;
  el.preview.textContent = t("Vorschlag – noch nicht gespeichert");
  el.preview.classList.remove("hidden");
  const list = document.createElement("ul");
  list.className = "diff";
  for (const line of describeDiff(proposal.diff)) {
    const li = document.createElement("li");
    li.className = { "+": "add", "−": "remove", "~": "change" }[line.sign];
    li.textContent = `${line.sign} ${line.text}`;
    list.appendChild(li);
  }
  const actions = document.createElement("div");
  actions.className = "proposal-actions";
  const apply = document.createElement("button");
  apply.type = "button";
  apply.id = "btn-proposal-apply";
  apply.textContent = t("Übernehmen");
  const discard = document.createElement("button");
  discard.type = "button";
  discard.id = "btn-proposal-discard";
  discard.textContent = t("Verwerfen");
  actions.append(apply, discard);
  bubble.append(list, actions);

  const label = userText.split("\n")[0].slice(0, 100);
  async function finish(path, body) {
    apply.disabled = discard.disabled = true;
    const id = project ? proposal.diagram_id : state.diagramId;
    try {
      // A project proposal is confirmed through the project, which notes the outcome in its transcript.
      const url = project ? `${projectPath("proposal", path)}${focusQuery()}` : `/api/diagrams/${id}/proposal/${path}${focusQuery()}`;
      const payload = project ? { ...(body || {}), diagram: id } : body;
      const answer = await api(url, { method: "POST", body: payload ? JSON.stringify(payload) : undefined });
      actions.textContent = answer.version ? t("Übernommen als Version v{version}", { version: answer.version.version }) : t("Verworfen");
      showDiagram(answer.diagram ? answer : await api(`/api/diagrams/${id}${focusQuery()}`));
    } catch (err) {
      actions.remove();
      addMessage("error", t("Vorschlag nicht übernommen: {error}", { error: err.message || err }));
      showDiagram(await api(`/api/diagrams/${id}${focusQuery()}`));
    } finally {
      state.proposal = null;
      setBusy(false);
      setStatus("");
      await refreshDiagramListLabel();
    }
  }
  const { diff, diagram_id: _proposalDiagram, ...replay } = proposal; // the server needs operations, base, result
  apply.addEventListener("click", () => finish("apply", project ? { ...replay, label } : { ...proposal, label }));
  discard.addEventListener("click", () => finish("discard"));
}

export function initChat() {
  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = el.input.value.trim();
    if (!text || state.busy) return;
    el.input.value = "";
    sendMessage(text);
  });
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); el.form.requestSubmit(); }
  });
  $("btn-project-chat").addEventListener("click", () => setChatScope(state.chatScope === "project" ? "diagram" : "project"));
  try { if (localStorage.getItem("arcviz.chatScope") === "project") state.chatScope = "project"; } catch {}
  onProjectShown(async () => { // a different project: show its transcript (or keep the diagram chat)
    if (state.chatScope === "project" || state.chatScope === "requirements") await setChatScope(state.chatScope);
  });
  const proposeToggle = $("propose-mode");
  try { proposeToggle.checked = localStorage.getItem("arcviz.propose") === "1"; } catch {}
  proposeToggle.addEventListener("change", () => { try { localStorage.setItem("arcviz.propose", proposeToggle.checked ? "1" : "0"); } catch {} });
}
