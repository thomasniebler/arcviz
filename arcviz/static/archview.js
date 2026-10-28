// The Architektur page (spec: docs/architecture/specs/app-shell.md and decision-support.md, ADR-0194): one main view with
// the tabs Treiber, Szenarien, Entscheidungen, Stakeholder, Vision and Dokumentation. Every tab loads its data when it is shown; a decision taken
// from a scenario analysis opens in the Entscheidungen tab. Every tab is a two-pane list and editor (spec:
// architecture-workspace.md); leaving a tab or the page with unsaved edits asks first. Which tab is shown is part of the URL hash (routes.js); the
// shell (shell.js) calls showArchitectureView.
import { $, setHash } from "./dom.js";
import { initDecisionPanel, isDecisionDirty, loadDecisions } from "./decisionpanel.js";
import { initDriverPanel, isDriverDirty, loadDrivers } from "./driverpanel.js";
import { focusSection, initDocumentationPanel, isDocumentationDirty, loadDocumentation } from "./documentationpanel.js";
import { initGapsPanel, isGapsDirty, loadGaps } from "./gapspanel.js";
import { confirmDiscard, initAutosize } from "./mdpane.js";
import { currentProject, onProjectShown } from "./navigator.js";
import { ARCH_TABS, formatRoute } from "./routes.js";
import { initStewardPanel, loadSteward } from "./stewardpanel.js";
import { initScenarioPanel, isScenarioDirty, loadScenarios } from "./scenariopanel.js";
import { initStakeholderPanel, isStakeholderDirty, loadStakeholders } from "./stakeholderpanel.js";
import { initVisionPanel, isVisionDirty, loadVision } from "./visionpanel.js";

const LOADERS = { drivers: loadDrivers, scenarios: loadScenarios, decisions: loadDecisions, stakeholders: loadStakeholders, vision: loadVision, documentation: loadDocumentation, gaps: loadGaps };

const DIRTY = { drivers: isDriverDirty, scenarios: isScenarioDirty, decisions: isDecisionDirty, stakeholders: isStakeholderDirty, vision: isVisionDirty, documentation: isDocumentationDirty, gaps: isGapsDirty };

let shown = false;
let current = ARCH_TABS[0];

/** The tab that is (or was last) shown. */
export function currentArchTab() { return current; }

/** Whether the shown tab may be left: its editor holds no unsaved changes, or the user agreed to discard them. */
export function confirmLeaveTab() { return confirmDiscard(DIRTY[current]()); }

async function show(tab, ...args) {
  if (tab !== current && !confirmLeaveTab()) { // a route change to another tab with unsaved edits: stay, put the hash back
    setHash(formatRoute({ view: "architecture", tab: current }), { replace: true });
    return;
  }
  current = tab;
  for (const button of document.querySelectorAll("#arch-tabs [role=tab]")) {
    const on = button.dataset.tab === tab;
    button.setAttribute("aria-selected", String(on));
    $(`arch-panel-${button.dataset.tab}`).hidden = !on;
  }
  try {
    await LOADERS[tab](...args);
  } catch (err) {
    $(`${tab}-status`).textContent = err.message || String(err);
  }
}

/** Show or hide the page (chat.js switches the other main views with it); `tab` defaults to the last shown one. */
export async function showArchitectureView(on, tab) {
  shown = on;
  $("architecture-view").classList.toggle("hidden", !on);
  document.querySelector(".layout")?.classList.toggle("architecture-mode", on);
  if (on && currentProject()) {
    loadSteward(); // the status line loads beside the tab, never blocks it
    await show(ARCH_TABS.includes(tab) ? tab : current);
  }
}

/** Follow the next step of a gap (gapspanel.js): click an existing control, or go to a tab, preselecting a documentation
 * section or opening a decision. */
async function followStep(step) {
  if (step.click) { $(step.click)?.click(); return; }
  if (!step.route) return;
  if (step.section) focusSection(step.section);
  setHash(step.route); // no hashchange follows a setHash: show the tab ourselves (the gap analysis has no editor to discard)
  await show(step.route.split("/").pop(), step.item);
}

/** `onApply(text)` sends an analysis option's instruction as a preview chat turn (and leaves the page first). */
export function initArchView({ onApply }) {
  initAutosize($("architecture-view"));
  initDriverPanel();
  initDecisionPanel();
  initStakeholderPanel();
  initVisionPanel();
  initStewardPanel();
  initDocumentationPanel();
  initGapsPanel({ step: followStep });
  initScenarioPanel({
    onDecided: async (decisionId) => {
      setHash(formatRoute({ view: "architecture", tab: "decisions" }));
      await show("decisions", decisionId);
    },
    onApply,
  });
  onProjectShown(async () => { if (shown) await show(current); });
}
