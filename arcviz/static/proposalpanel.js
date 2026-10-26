// Proposals that agents stored (spec: docs/architecture/specs/agent-access.md, ADR-0172): the open project's list with
// the diff of each, applied or discarded by a person. The button shows how many wait for a decision; the count is read
// when a project is shown, every half minute while the page is visible, and after every decision.
import { api } from "./api.js";
import { showDiagram } from "./diagram.js";
import { $ } from "./dom.js";
import { currentProject, onProjectShown, refreshDiagramListLabel } from "./navigator.js";
import {
  canApply, canDecide, describeDiff, describeItem, isProjectProposal, itemOpLabel, itemSections, itemStateLabel, pendingCount, pendingItems,
  selectionClosure, statusLabel, storedHeading,
} from "./proposal.js";
import { focusQuery, state } from "./state.js";
import { t } from "./i18n.js";

const POLL_MS = 30000;
let proposals = []; // GET /api/projects/{pid}/proposals of the open project
let titles = {}; // diagram id -> title, for the headings

function path(...parts) {
  return `/api/projects/${[currentProject(), ...parts].map((p) => encodeURIComponent(p)).join("/")}`;
}

function say(text) { $("proposal-status").textContent = text || ""; }

function showCount() {
  const n = pendingCount(proposals);
  $("btn-proposals").textContent = n ? t("Vorschläge ({count})", { count: n }) : t("Vorschläge");
}

async function load() {
  if (!currentProject()) { proposals = []; showCount(); return; }
  proposals = (await api(path("proposals"))).proposals;
  showCount();
}

async function refreshBadge() {
  if (document.visibilityState === "hidden" || !currentProject()) return;
  try { await load(); if ($("proposal-dialog").open) render(); } catch { /* the badge is a hint: the next poll tries again */ }
}

async function guarded(action) {
  try { await action(); } catch (err) { say(err.message || String(err)); try { await load(); render(); } catch { /* shown above */ } }
}

async function refreshShown(diagramId) {
  if (diagramId === state.diagramId) showDiagram(await api(`/api/diagrams/${encodeURIComponent(diagramId)}${focusQuery()}`));
  await refreshDiagramListLabel();
}

function button(text, className, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = className;
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

function itemRow(item) {
  // Built with DOM APIs: titles, texts and the agent's rationale are free text and must never reach innerHTML.
  const li = document.createElement("li");
  li.className = `proposal-item proposal-item-${item.state}`;
  li.dataset.itemKey = item.key;
  const head = document.createElement("label");
  head.className = "proposal-item-head";
  if (item.state === "pending") {
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.className = "proposal-item-tick";
    tick.setAttribute("data-edit", "");
    head.appendChild(tick);
  }
  const what = document.createElement("strong");
  what.textContent = t("{op}: {title}", { op: itemOpLabel(item.op), title: item.title || item.key });
  const state = document.createElement("span");
  state.className = "muted";
  state.textContent = ` ${itemStateLabel(item.state)}${item.created ? ` (${item.created})` : ""}`;
  head.append(what, state);
  li.appendChild(head);
  if (item.error) {
    const err = document.createElement("p");
    err.className = "proposal-item-error";
    err.textContent = item.error;
    li.appendChild(err);
  }
  if (item.rationale) {
    const why = document.createElement("p");
    why.className = "proposal-item-why";
    why.textContent = item.rationale;
    li.appendChild(why);
  }
  if (item.cites?.length) {
    const cites = document.createElement("p");
    cites.className = "proposal-item-cites muted";
    cites.textContent = t("Bezieht sich auf: {ids}", { ids: item.cites.join(", ") });
    li.appendChild(cites);
  }
  const details = describeItem(item);
  if (details.length) {
    const dl = document.createElement("ul");
    dl.className = "proposal-item-details";
    for (const line of details) {
      const d = document.createElement("li");
      d.textContent = `${line.label}: ${line.text}`;
      dl.appendChild(d);
    }
    li.appendChild(dl);
  }
  return li;
}

function projectActions(proposal, li) {
  const actions = document.createElement("div");
  actions.className = "proposal-actions";
  actions.setAttribute("data-edit-zone", "");
  const ticked = () => [...li.querySelectorAll(".proposal-item-tick")].filter((t) => t.checked).map((t) => t.closest("li").dataset.itemKey);
  const send = (action, keys, done) => guarded(async () => {
    const result = await api(path("proposals", proposal.id, action), { method: "POST", body: JSON.stringify(keys ? { [action === "apply" ? "accept" : "keys"]: keys } : {}) });
    say(done(result.proposal));
    await load(); render();
  });
  const chosen = () => {
    const picked = ticked();
    if (!picked.length) { say(t("Nichts angekreuzt.")); return null; }
    const { keys, blocked } = selectionClosure(proposal.items, picked);
    if (blocked.length) say(t("{blocked}: nicht mehr möglich.", { blocked: blocked.map((b) => t("{key} braucht {needs}", { key: b.key, needs: b.needs })).join("; ") }));
    if (!keys.length) return null;
    for (const t of li.querySelectorAll(".proposal-item-tick")) t.checked = keys.includes(t.closest("li").dataset.itemKey);
    return keys;
  };
  const applied = (p) => t("Vorschlag {id}: {done} von {total} übernommen.", { id: p.id, done: p.items.filter((i) => i.state === "applied").length, total: p.items.length });
  const discarded = (p) => t("Vorschlag {id}: {count} verworfen.", { id: p.id, count: p.items.filter((i) => i.state === "discarded").length });
  if (canApply(proposal) && pendingItems(proposal).length) {
    actions.append(
      button(t("Ausgewählte übernehmen"), "small proposal-apply-selected", () => { const keys = chosen(); if (keys) send("apply", keys, applied); }),
      button(t("Alle übernehmen"), "small proposal-apply", () => send("apply", null, applied)),
    );
  }
  if (canDecide(proposal) && pendingItems(proposal).length) {
    actions.append(
      button(t("Ausgewählte verwerfen"), "small danger proposal-discard-selected", () => { const picked = ticked(); if (picked.length) send("discard", picked, discarded); else say(t("Nichts angekreuzt.")); }),
      button(t("Alle verwerfen"), "small danger proposal-discard", () => send("discard", null, discarded)),
    );
  }
  return actions;
}

function card(proposal) {
  // Built with DOM APIs: names and the agent's rationale are free text and must never reach innerHTML.
  const li = document.createElement("li");
  li.className = `proposal-card proposal-${proposal.status}`;
  li.dataset.proposalId = proposal.id;
  const head = document.createElement("div");
  head.className = "proposal-head";
  const heading = document.createElement("strong");
  heading.textContent = storedHeading(proposal, titles);
  const badge = document.createElement("span");
  badge.className = "proposal-state muted";
  badge.textContent = statusLabel(proposal.status);
  head.append(heading, " ", badge);
  li.appendChild(head);
  if (proposal.rationale) {
    const why = document.createElement("p");
    why.className = "proposal-rationale";
    why.textContent = proposal.rationale;
    li.appendChild(why);
  }
  if (isProjectProposal(proposal)) {
    const list = document.createElement("ul");
    list.className = "proposal-items";
    list.replaceChildren(
      ...itemSections(proposal.items).flatMap((section) => {
        const rows = section.items.map(itemRow);
        if (!section.label) return rows;
        const heading = document.createElement("li");
        heading.className = "proposal-group";
        heading.textContent = section.label;
        return [heading, ...rows];
      }),
    );
    li.appendChild(list);
    if (canDecide(proposal)) li.appendChild(projectActions(proposal, li));
    return li;
  }
  const diff = document.createElement("ul");
  diff.className = "proposal-diff";
  for (const line of describeDiff(proposal.diff)) {
    const item = document.createElement("li");
    item.textContent = `${line.sign} ${line.text}`;
    diff.appendChild(item);
  }
  li.appendChild(diff);
  if (canDecide(proposal)) {
    const actions = document.createElement("div");
    actions.className = "proposal-actions";
    actions.setAttribute("data-edit-zone", "");
    if (canApply(proposal)) {
      const apply = document.createElement("button");
      apply.type = "button"; apply.className = "small proposal-apply"; apply.textContent = t("Übernehmen");
      apply.addEventListener("click", () => guarded(async () => {
        const result = await api(path("proposals", proposal.id, "apply"), { method: "POST" });
        say(t("Vorschlag {id} übernommen (Version {version}).", { id: proposal.id, version: result.proposal.version }));
        await load(); render();
        await refreshShown(proposal.diagram);
      }));
      actions.appendChild(apply);
    }
    const discard = document.createElement("button");
    discard.type = "button"; discard.className = "small danger proposal-discard"; discard.textContent = t("Verwerfen");
    discard.addEventListener("click", () => guarded(async () => {
      await api(path("proposals", proposal.id, "discard"), { method: "POST" });
      say(t("Vorschlag {id} verworfen.", { id: proposal.id }));
      await load(); render();
    }));
    actions.appendChild(discard);
    li.appendChild(actions);
  }
  return li;
}

function render() {
  const list = $("proposal-list");
  list.replaceChildren(...proposals.map(card));
  $("proposal-empty").classList.toggle("hidden", proposals.length > 0);
}

async function open() {
  if (!currentProject()) return;
  const diagrams = await api(path("diagrams"));
  titles = Object.fromEntries((diagrams.diagrams || diagrams || []).map((d) => [d.id ?? d.diagram, d.title]));
  await load();
  say("");
  render();
  $("proposal-dialog").showModal();
}

export function initProposalPanel() {
  $("btn-proposals").addEventListener("click", () => guarded(open));
  $("proposal-close").addEventListener("click", () => $("proposal-dialog").close());
  onProjectShown(() => {
    if ($("proposal-dialog").open) $("proposal-dialog").close();
    refreshBadge();
  });
  setInterval(refreshBadge, POLL_MS);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refreshBadge(); });
  document.addEventListener("arcviz:proposals-changed", refreshBadge); // a derivation stored a proposal (requirementview.js)
}
