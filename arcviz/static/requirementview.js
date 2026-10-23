// The requirements view (spec: docs/architecture/specs/requirements-workshop.md, ADR-0191): the table of the project's
// requirements as the working surface of the elaboration mode. It shows the pipeline (roh, Vorschlag, verfeinert,
// abgeleitet), filters, selection, the detail panel with the suggestion next to the fields in force, import, and the
// chunked steps (aufbereiten, ableiten). The chat of this scope is chat.js. Everything is built with DOM APIs: requirement
// texts are free text from users and must never reach innerHTML.
import { api } from "./api.js";
import { $, option } from "./dom.js";
import { t } from "./i18n.js";
import { requirementDeleteConfirm, requirementsDeleteConfirm } from "./lifecycle.js";
import { currentProject, onProjectShown } from "./navigator.js";
import {
  KIND_LABELS, PAGE_SIZE, PRIORITY_LABELS, acceptItems, coverageLabel, displayText, importProblemLines, importSummary,
  issueLabel, kindLabel, listQuery, pipeline, priorityLabel, progressText, rangeIds, shouldContinue, stepLabel,
  suggestionFields, unremarkableIds,
} from "./requirements.js";

let rows = []; // the loaded pages of GET /api/projects/{pid}/requirements
let total = 0; // rows matching the filters
let digest = null;
let filters = {};
let selected = []; // ticked ids, in tick order
let anchor = null; // the last ticked id (shift-click ranges)
let detailId = null;
let stakeholders = {}; // id -> name
let elementNames = {}; // project element id -> name
let loopRunning = false;
let cancelled = false;
let shown = false;

function path(...parts) {
  return `/api/projects/${[currentProject(), ...parts].map((p) => encodeURIComponent(p)).join("/")}`;
}

function say(text) { $("req-status").textContent = text || ""; }

async function guarded(action) {
  try { await action(); } catch (err) { say(err.message || String(err)); }
}

function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

function button(text, className, onClick, title) {
  const b = node("button", className, text);
  b.type = "button";
  if (title) b.title = title;
  b.addEventListener("click", onClick);
  return b;
}

function stakeholderName(id) { return stakeholders[id] || id; }

function rowById(id) { return rows.find((r) => r.id === id); }

// ---- loading ---------------------------------------------------------------------------------------------------------

async function load({ reset = true } = {}) {
  if (!currentProject()) { rows = []; total = 0; digest = null; render(); return; }
  const offset = reset ? 0 : rows.length;
  const page = await api(path("requirements") + listQuery(filters, { limit: PAGE_SIZE, offset }));
  rows = reset ? page.requirements : [...rows, ...page.requirements];
  total = page.total;
  digest = page.digest;
  selected = selected.filter((id) => rows.some((r) => r.id === id));
  render();
  if (detailId && !rowById(detailId)) await openDetail(detailId, { fetchOnly: true });
}

async function loadStakeholders() {
  if (!currentProject()) return;
  try {
    stakeholders = Object.fromEntries((await api(path("stakeholders"))).stakeholders.map((s) => [s.id, s.name]));
  } catch { stakeholders = {}; }
  try {
    elementNames = Object.fromEntries(((await api(path("elements"))).elements || []).map((e) => [e.id, e.name]));
  } catch { elementNames = {}; }
}

/** The shown requirements changed elsewhere (the chat, a proposal): read them again. */
export async function refreshRequirements() {
  if (!shown) return;
  await guarded(async () => { await loadStakeholders(); await load(); if (detailId) await openDetail(detailId); });
}

// ---- rendering -------------------------------------------------------------------------------------------------------

function render() {
  renderPipeline();
  renderTable();
  renderActions();
  $("req-total").textContent = digest ? t("{shown} von {total}", { shown: total, total: digest.total }) : "";
  $("req-more").classList.toggle("hidden", rows.length >= total);
  $("req-empty").classList.toggle("hidden", rows.length > 0);
}

function renderPipeline() {
  const box = $("req-pipeline");
  box.replaceChildren(
    ...pipeline(digest).map(({ step, label, count }) => {
      const b = button(`${label} ${count}`, `req-step${filters.step === step ? " active" : ""}`, () => guarded(async () => {
        filters = { ...filters, step: filters.step === step ? "" : step };
        await load();
      }), t("Nach diesem Schritt filtern"));
      b.dataset.step = step;
      return b;
    }),
  );
}

function renderTable() {
  const body = $("req-table").tBodies[0];
  body.replaceChildren(...rows.map(rowNode));
}

function rowNode(r) {
  const tr = document.createElement("tr");
  tr.dataset.id = r.id;
  tr.className = `req-row step-${r.step}${selected.includes(r.id) ? " selected" : ""}${detailId === r.id ? " open" : ""}`;
  const tick = document.createElement("input");
  tick.type = "checkbox";
  tick.checked = selected.includes(r.id);
  tick.setAttribute("aria-label", t("{id} auswählen", { id: r.id }));
  tick.addEventListener("click", (e) => { e.stopPropagation(); select(r.id, e.shiftKey, tick.checked); });
  const pick = node("td", "req-pick");
  pick.appendChild(tick);
  const text = node("td", "req-text");
  text.appendChild(node("span", "req-main", displayText(r)));
  if (r.suggestion) {
    const s = node("span", "req-suggestion", t("Vorschlag: {statement}", { statement: r.suggestion.statement }));
    s.title = t("Ein Assistent schlägt diese Aussage vor; sie gilt erst, wenn du sie übernimmst.");
    text.appendChild(s);
  }
  const issues = (r.issues || []).filter((i) => !i.resolved);
  if (issues.length) text.appendChild(node("span", "req-issues", issues.map((i) => issueLabel(i.kind)).join(", ")));
  tr.append(
    pick, node("td", "req-id", r.id), text, node("td", null, kindLabel(r.kind)), node("td", null, priorityLabel(r.priority)),
    node("td", null, r.theme || "–"), node("td", null, coverageLabel(r.coverage)), node("td", null, stepLabel(r.step)),
  );
  tr.addEventListener("click", () => guarded(() => openDetail(r.id)));
  return tr;
}

function select(id, range, on) {
  if (range && anchor) {
    const ids = rangeIds(rows.map((r) => r.id), anchor, id);
    selected = [...new Set([...selected, ...ids])];
  } else {
    selected = on ? [...new Set([...selected, id])] : selected.filter((x) => x !== id);
  }
  anchor = id;
  renderTable();
  renderActions();
}

function renderActions() {
  const n = selected.length;
  $("req-selection").textContent = n ? t("{count} ausgewählt", { count: n }) : "";
  $("req-accept").disabled = loopRunning || !selected.some((id) => rowById(id)?.suggestion);
  $("req-accept-safe").disabled = loopRunning || !unremarkableIds(rows).length;
  $("req-reject").disabled = loopRunning || !n;
  $("req-delete").disabled = loopRunning || !n;
  $("req-refine").disabled = loopRunning;
  $("req-derive").disabled = loopRunning;
  $("req-cancel").classList.toggle("hidden", !loopRunning);
  $("req-refine").textContent = n ? t("Ausgewählte aufbereiten") : t("Rohe aufbereiten");
  $("req-derive").textContent = n ? t("Aus Auswahl ableiten") : t("Ableiten");
}

// ---- the detail panel -----------------------------------------------------------------------------------------------

async function openDetail(id, { fetchOnly = false } = {}) {
  const detail = (await api(path("requirements", id))).requirement;
  const at = rows.findIndex((r) => r.id === id);
  if (at >= 0) rows[at] = { ...detail };
  detailId = id;
  if (!fetchOnly) renderTable();
  renderDetail(detail);
}

function closeDetail() {
  detailId = null;
  $("req-detail").classList.add("hidden");
  renderTable();
}

function labelled(text, control) {
  const l = node("label", "req-field");
  l.append(node("span", null, text), control);
  return l;
}

function selectOf(values, current) {
  const s = document.createElement("select");
  for (const [value, label] of Object.entries(values)) s.appendChild(option(value, t(label), value === current));
  return s;
}

function multiSelect(names, selected, label) {
  const s = document.createElement("select");
  s.multiple = true;
  s.setAttribute("aria-label", label);
  for (const [id, name] of Object.entries(names)) s.appendChild(option(id, name, selected.includes(id)));
  s.size = Math.min(4, Math.max(2, Object.keys(names).length));
  return s;
}

const picked = (select) => [...select.selectedOptions].map((o) => o.value);

function renderDetail(r) {
  const box = $("req-detail");
  box.classList.remove("hidden");
  const head = node("div", "req-detail-head");
  head.append(node("strong", null, `${r.id} · ${stepLabel(r.step)}`), button("×", "small", closeDetail, t("Schließen")));
  const raw = node("blockquote", "req-raw", r.raw);
  raw.title = t("Originalwortlaut: bleibt unverändert");
  const statement = document.createElement("textarea");
  statement.rows = 3; statement.value = r.statement; statement.maxLength = 400;
  const kind = selectOf(KIND_LABELS, r.kind);
  const priority = selectOf(PRIORITY_LABELS, r.priority);
  const measure = document.createElement("input"); measure.value = r.measure; measure.maxLength = 300;
  const theme = document.createElement("input"); theme.value = r.theme; theme.maxLength = 80;
  const people = multiSelect(stakeholders, r.stakeholders || [], t("Stakeholder"));
  const parts = multiSelect(elementNames, r.elements || [], t("Elemente"));
  const form = node("div", "req-form");
  form.setAttribute("data-edit-zone", "");
  form.append(
    labelled(t("Aussage"), statement), labelled(t("Art"), kind), labelled(t("Priorität"), priority),
    labelled(t("Maß"), measure), labelled(t("Thema"), theme),
    labelled(t("Stakeholder"), people), labelled(t("Elemente"), parts),
  );
  const save = button(t("Speichern"), "small req-save", () => guarded(async () => {
    await api(path("requirements", r.id), {
      method: "PATCH",
      body: JSON.stringify({ statement: statement.value, kind: kind.value, priority: priority.value, measure: measure.value, theme: theme.value, stakeholders: picked(people), elements: picked(parts) }),
    });
    say(t("{id} gespeichert.", { id: r.id }));
    await load(); await openDetail(r.id);
  }));
  form.appendChild(save);
  box.replaceChildren(head, raw, form, suggestionBlock(r), issuesBlock(r), statusBlock(r), linksBlock(r));
}

function suggestionBlock(r) {
  const block = node("section", "req-suggestion-block");
  const fields = suggestionFields(r, stakeholderName);
  if (!r.suggestion) return block;
  const heading = r.suggestion.by_name
    ? t("Vorschlag von {name} ({client})", { name: r.suggestion.by_name, client: r.suggestion.client })
    : t("Vorschlag ({client})", { client: r.suggestion.client });
  block.appendChild(node("h4", null, heading));
  for (const f of fields) {
    const line = node("div", `req-suggestion-field${f.accepted ? " accepted" : ""}`);
    line.append(node("span", "req-field-name", `${f.label}: `), node("span", "req-current", f.current || "–"), " → ", node("strong", null, f.suggested || "–"));
    if (f.accepted) line.appendChild(node("span", "muted", ` ${t("(übernommen)")}`));
    else {
      const take = button(t("Übernehmen"), "small", () => guarded(async () => {
        await accept([{ id: r.id, expected: r.suggestion.rev, fields: [f.field] }]);
      }));
      take.setAttribute("data-edit", "");
      line.appendChild(take);
    }
    block.appendChild(line);
  }
  const actions = node("div", "req-actions-row");
  actions.setAttribute("data-edit-zone", "");
  actions.append(
    button(t("Alles übernehmen"), "small", () => guarded(() => accept([{ id: r.id, expected: r.suggestion.rev }]))),
    button(t("Vorschlag verwerfen"), "small danger", () => guarded(async () => {
      await api(path("requirements", r.id, "reject-suggestion"), { method: "POST" });
      await load(); await openDetail(r.id);
    })),
  );
  block.appendChild(actions);
  return block;
}

function issuesBlock(r) {
  const block = node("section", "req-issues-block");
  if (!(r.issues || []).length) return block;
  block.appendChild(node("h4", null, t("Befunde")));
  r.issues.forEach((issue, index) => {
    const line = node("div", `req-issue${issue.resolved ? " resolved" : ""}`);
    const partner = issue.other ? ` (${issue.other})` : "";
    line.appendChild(node("span", null, t("{label}{partner}: {text}", { label: issueLabel(issue.kind), partner, text: issue.text || "" })));
    const toggle = button(issue.resolved ? t("Wieder öffnen") : t("Erledigt"), "small", () => guarded(async () => {
      await api(path("requirements", r.id, "issues", index), { method: "PATCH", body: JSON.stringify({ resolved: !issue.resolved }) });
      await load(); await openDetail(r.id);
    }));
    toggle.setAttribute("data-edit", "");
    line.appendChild(toggle);
    block.appendChild(line);
  });
  return block;
}

function statusBlock(r) {
  const block = node("section", "req-status-block");
  block.setAttribute("data-edit-zone", "");
  const setStatus = (status, extra = {}) => guarded(async () => {
    await api(path("requirements", "status"), { method: "POST", body: JSON.stringify({ ids: [r.id], status, ...extra }) }).then((result) => {
      if (result.skipped?.length) say(result.skipped[0].reason);
    });
    await load(); await openDetail(r.id);
  });
  const reason = document.createElement("input");
  reason.placeholder = t("Grund / Duplikat von (Kennung)");
  reason.setAttribute("aria-label", t("Grund oder Kennung des Originals"));
  block.append(
    button(t("Als verfeinert markieren"), "small", () => setStatus("refined"), t("Braucht eine Aussage")),
    button(t("Verwerfen"), "small", () => setStatus("rejected", { reason: reason.value })),
    button(t("Duplikat von …"), "small", () => setStatus("duplicate", { duplicate_of: reason.value })),
    button(t("Zurück auf roh"), "small", () => setStatus("raw")),
    reason,
    button(t("Löschen"), "small danger", () => guarded(async () => {
      if (!confirm(requirementDeleteConfirm(r))) return;
      await api(path("requirements", r.id), { method: "DELETE" });
      detailId = null; $("req-detail").classList.add("hidden");
      say(t("{id} gelöscht.", { id: r.id }));
      await load();
    }), t("Entfernt die Anforderung und ihre Verweise")),
  );
  return block;
}

function linksBlock(r) {
  const block = node("section", "req-links-block");
  const line = (name, value) => { if (value) block.appendChild(node("p", "muted", t("{name}: {value}", { name, value }))); };
  line(t("Quelle"), [r.source, r.external_id].filter(Boolean).join(" · "));
  line(t("Stakeholder"), (r.stakeholders || []).map(stakeholderName).join(", "));
  line(t("Elemente"), (r.elements || []).map((id) => elementNames[id] || id).join(", "));
  if (r.duplicate_of) line(t("Duplikat von"), r.duplicate_of);
  if (r.reason) line(t("Grund"), r.reason);
  const trace = r.trace || { drivers: [], scenarios: [], decisions: [] };
  line(t("Treiber"), trace.drivers.join(", "));
  line(t("Szenarien"), trace.scenarios.join(", "));
  line(t("Entscheidungen"), trace.decisions.join(", "));
  line(t("Angelegt"), `${r.author_name || r.author || "?"} (${r.client})`);
  return block;
}

// ---- actions ---------------------------------------------------------------------------------------------------------

async function accept(items) {
  if (!items.length) return;
  const result = await api(path("requirements", "accept"), { method: "POST", body: JSON.stringify({ items }) });
  const stale = result.stale.length ? `, ${t("{count} inzwischen geändert (bitte neu ansehen)", { count: result.stale.length })}` : "";
  const skipped = result.skipped.length ? `, ${t("{count} übersprungen", { count: result.skipped.length })}` : "";
  say(`${t("{count} übernommen", { count: result.accepted.length })}${stale}${skipped}.`);
  await load();
  if (detailId) await openDetail(detailId);
}

async function loop(verb, step) {
  loopRunning = true;
  cancelled = false;
  renderActions();
  let done = 0;
  let previous = null;
  try {
    for (;;) {
      const result = await step();
      done += 1;
      say(progressText(verb, done, result.remaining ?? 0));
      await load();
      if (result.llm && !result.llm.ok) { say(t("{verb}: {error}", { verb, error: result.llm.error || t("Das Modell hat nichts geliefert.") })); break; }
      if (result.proposal) document.dispatchEvent(new CustomEvent("arcviz:proposals-changed"));
      if (cancelled || !shouldContinue(result, previous)) break;
      previous = result.remaining;
    }
  } finally {
    loopRunning = false;
    renderActions();
  }
}

function chunkBody() {
  const ids = selected.slice(0, 25);
  return ids.length ? { ids } : {};
}

async function importDialog() {
  const dialog = $("req-import-dialog");
  $("req-import-text").value = "";
  $("req-import-preview").textContent = "";
  $("req-import-file").value = "";
  dialog.showModal();
}

async function importRun(dryRun) {
  const text = $("req-import-text").value;
  if (!text.trim()) { $("req-import-preview").textContent = t("Nichts eingegeben."); return; }
  const result = await api(path("requirements", "import"), {
    method: "POST",
    body: JSON.stringify({ text, format: $("req-import-format").value, source: $("req-import-source").value, dry_run: dryRun }),
  });
  $("req-import-preview").textContent = [importSummary(result), ...importProblemLines(result)].join("\n");
  if (!dryRun) {
    say(importSummary(result));
    $("req-import-dialog").close();
    await load();
  }
}

async function showMatrix() {
  const matrix = await api(path("traceability"));
  const body = $("req-matrix-table").tBodies[0];
  const names = (kind) => (ids) => ids.map((id) => matrix.objects[kind][id] ? `${id} ${matrix.objects[kind][id]}` : id).join(", ") || "–";
  body.replaceChildren(
    ...matrix.rows.map((r) => {
      const tr = document.createElement("tr");
      tr.append(
        node("td", "req-id", r.id), node("td", null, r.statement), node("td", null, coverageLabel(r.coverage)),
        node("td", null, names("drivers")(r.drivers)), node("td", null, names("scenarios")(r.scenarios)),
        node("td", null, names("decisions")(r.decisions)),
      );
      return tr;
    }),
  );
  $("req-matrix-empty").classList.toggle("hidden", matrix.rows.length > 0);
  $("req-matrix-dialog").showModal();
}

function readFilters() {
  filters = {
    step: filters.step || "",
    kind: $("req-filter-kind").value,
    coverage: $("req-filter-coverage").value,
    theme: $("req-filter-theme").value.trim(),
    q: $("req-filter-q").value.trim(),
    suggested: $("req-filter-suggested").checked,
  };
}

// ---- entry points ----------------------------------------------------------------------------------------------------

/** Show or hide the view (chat.js switches the chat scope with it). */
export async function showRequirementView(on) {
  shown = on;
  $("requirements-view").classList.toggle("hidden", !on);
  document.querySelector(".layout")?.classList.toggle("requirements-mode", on);
  $("btn-requirements").setAttribute("aria-pressed", String(on));
  if (on) await refreshRequirements();
}

export function initRequirementView() {
  $("req-filter-kind").append(option("", t("Alle Arten")), ...Object.entries(KIND_LABELS).map(([v, l]) => option(v, t(l))));
  $("req-filter-coverage").append(option("", t("Jede Abdeckung")), option("none", t("nicht verfeinert")), option("uncovered", t("offen")), option("driver", t("Treiber")), option("scenario", t("Szenario")), option("decision", t("Entscheidung")));
  for (const id of ["req-filter-kind", "req-filter-coverage", "req-filter-suggested"]) {
    $(id).addEventListener("change", () => guarded(async () => { readFilters(); await load(); }));
  }
  for (const id of ["req-filter-theme", "req-filter-q"]) {
    $(id).addEventListener("change", () => guarded(async () => { readFilters(); await load(); }));
  }
  $("req-more").addEventListener("click", () => guarded(() => load({ reset: false })));
  $("req-import").addEventListener("click", () => guarded(importDialog));
  $("req-import-file").addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    $("req-import-text").value = await file.text();
    if (!$("req-import-source").value) $("req-import-source").value = file.name;
  });
  $("req-import-check").addEventListener("click", () => guarded(() => importRun(true)));
  $("req-import-go").addEventListener("click", () => guarded(() => importRun(false)));
  $("req-import-cancel").addEventListener("click", () => $("req-import-dialog").close());
  $("req-matrix").addEventListener("click", () => guarded(showMatrix));
  $("req-matrix-close").addEventListener("click", () => $("req-matrix-dialog").close());
  $("req-accept").addEventListener("click", () => guarded(() => accept(acceptItems(rows, selected))));
  $("req-accept-safe").addEventListener("click", () => guarded(() => accept(acceptItems(rows, unremarkableIds(rows)))));
  $("req-reject").addEventListener("click", () => guarded(async () => {
    const result = await api(path("requirements", "status"), { method: "POST", body: JSON.stringify({ ids: selected, status: "rejected" }) });
    say(`${t("{count} verworfen", { count: result.changed.length })}${result.skipped.length ? `, ${t("{count} übersprungen", { count: result.skipped.length })}` : ""}.`);
    selected = [];
    await load();
  }));
  $("req-delete").addEventListener("click", () => guarded(async () => {
    if (!selected.length || !confirm(requirementsDeleteConfirm(rows, selected))) return;
    const result = await api(path("requirements", "delete"), { method: "POST", body: JSON.stringify({ ids: selected }) });
    say(`${t("{count} gelöscht", { count: result.removed.length })}${result.skipped.length ? `, ${t("{count} übersprungen", { count: result.skipped.length })}` : ""}.`);
    if (detailId && result.removed.includes(detailId)) { detailId = null; $("req-detail").classList.add("hidden"); }
    selected = [];
    await load();
  }));
  $("req-refine").addEventListener("click", () => guarded(() => loop(t("Aufbereiten"), () => api(path("requirements", "refine"), { method: "POST", body: JSON.stringify(chunkBody()) }))));
  $("req-derive").addEventListener("click", () => guarded(() => loop(t("Ableiten"), () => api(path("requirements", "derive"), { method: "POST", body: JSON.stringify(chunkBody()) }))));
  $("req-cancel").addEventListener("click", () => { cancelled = true; say(t("Wird nach dem laufenden Durchgang beendet …")); });
  $("req-select-all").addEventListener("click", () => { selected = rows.map((r) => r.id); renderTable(); renderActions(); });
  $("req-select-none").addEventListener("click", () => { selected = []; renderTable(); renderActions(); });
  onProjectShown(() => { detailId = null; selected = []; filters = {}; $("req-detail").classList.add("hidden"); if (shown) refreshRequirements(); });
}

