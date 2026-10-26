// The steward's status line on the Architektur page (spec: docs/architecture/specs/project-steward.md, ADR-0204): switched
// on or off for the project (owner) and run now (editor). Hidden while the installation has the steward switched off.
import { api } from "./api.js";
import { $ } from "./dom.js";
import { t } from "./i18n.js";
import { currentProject, onProjectShown } from "./navigator.js";
import { statusLine, stewardRights } from "./steward.js";
import { state } from "./state.js";

let status = null;

const path = (...parts) => `/api/projects/${[currentProject(), "steward", ...parts].map((p) => encodeURIComponent(p)).join("/")}`;

function render(role) {
  const box = $("steward-bar");
  box.classList.toggle("hidden", !status?.available);
  if (!status?.available) return;
  const rights = stewardRights(role);
  $("steward-line").textContent = statusLine(status);
  const toggle = $("steward-enabled");
  toggle.checked = status.enabled;
  toggle.disabled = !rights.switch;
  $("steward-run").disabled = !rights.run;
}

/** Load the steward's state of the open project (when the Architektur page is shown). */
export async function loadSteward() {
  if (!currentProject()) return;
  try {
    status = await api(path());
  } catch {
    status = null; // no steward line is better than an error banner on every page
  }
  render(state.role);
}

export function initStewardPanel() {
  $("steward-enabled").addEventListener("change", async (e) => {
    try { status = await api(path(), { method: "PUT", body: JSON.stringify({ enabled: e.target.checked }) }); } catch (err) { $("steward-line").textContent = err.message || String(err); return; }
    render(state.role);
  });
  $("steward-run").addEventListener("click", async () => {
    $("steward-line").textContent = t("Prüfung läuft …");
    $("steward-run").disabled = true;
    try {
      const report = await api(path("run"), { method: "POST" });
      status = report;
    } catch (err) { $("steward-line").textContent = err.message || String(err); $("steward-run").disabled = false; return; }
    render(state.role);
    document.dispatchEvent(new CustomEvent("arcviz:proposals-changed"));
  });
  onProjectShown(async () => { if (!$("architecture-view").classList.contains("hidden")) await loadSteward(); });
}
