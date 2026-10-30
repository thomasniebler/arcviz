// The app shell (spec: docs/architecture/specs/app-shell.md, ADR-0194): which main view fills the work area (the
// diagram stage, the requirements view or the Architektur page) and the location hash that names it. The rail and the
// navigator entries only set the hash; this module turns the hash into a view on every hash change and once after the
// first project is shown, so a deep link such as `#/architecture/scenarios` opens its page. Views that change it from
// code call `setMainView`, which keeps the hash in line.
import { confirmLeaveTab, currentArchTab, showArchitectureView } from "./archview.js";
import { setChatScope } from "./chat.js";
import { $, setHash } from "./dom.js";
import { onDiagramSwitched } from "./diagram.js";
import { onProjectShown, openLocation } from "./navigator.js";
import { showRequirementView } from "./requirementview.js";
import { formatRoute, parseRoute } from "./routes.js";
import { state } from "./state.js";

let scopeBeforeRequirements = "diagram";

/** Keep the location hash and the marked navigation entries in line with the shown main view. */
function syncRoute(replace) {
  const view = state.mainView;
  setHash(formatRoute({ view, tab: currentArchTab() }), { replace });
  const hash = location.hash === "#" ? "" : location.hash;
  for (const entry of document.querySelectorAll("[data-route]")) {
    if (entry.dataset.area) entry.toggleAttribute("aria-current", entry.dataset.area === view);
    else if (!entry.matches("[role=tab]")) entry.toggleAttribute("aria-current", entry.dataset.route === hash);
  }
}

/** Switch the main area between the diagram stage, the requirements view and the Architektur page (`tab` names its
 * tab); the requirements view brings its own chat scope, the others keep the chat as it is. */
export async function setMainView(view, tab) {
  if (state.busy || (view !== "diagram" && !state.projectId)) { syncRoute(true); return; }
  const before = state.mainView;
  if (before === "architecture" && view !== "architecture" && !confirmLeaveTab()) { syncRoute(true); return; } // unsaved edits
  state.mainView = view;
  if (view === "requirements") {
    if (before !== "requirements") scopeBeforeRequirements = state.chatScope === "requirements" ? "diagram" : state.chatScope;
    await showArchitectureView(false);
    await showRequirementView(true);
    await setChatScope("requirements");
  } else {
    await showRequirementView(false);
    await showArchitectureView(view === "architecture", tab);
    if (before === "requirements") await setChatScope(scopeBeforeRequirements);
  }
  syncRoute(false);
}

async function apply() {
  const route = parseRoute(location.hash);
  if (route.project) { // a permalink (spec: permalinks.md): open what it names, then show the address in its in-app form
    const followed = await openLocation(route.project, route.diagram);
    if (!followed) { setHash("", { replace: true }); return; }
    setHash(formatRoute(route), { replace: true });
  }
  return setMainView(route.view, route.tab);
}

export function initShell() {
  document.addEventListener("click", (ev) => {
    const entry = ev.target.closest("[data-route]");
    if (!entry) return;
    const hash = entry.dataset.route;
    if ((location.hash === "#" ? "" : location.hash) === hash) { apply(); return; } // same hash: no hashchange comes
    location.hash = hash; // the hashchange below does the rest; "" clears the fragment (a bare "#" parses as the diagram)
  });
  window.addEventListener("hashchange", apply);
  $("btn-chat-scope-back").addEventListener("click", () => (state.chatScope === "requirements" ? setMainView("diagram") : setChatScope("diagram")));
  $("req-to-diagram").addEventListener("click", () => setMainView("diagram"));
  $("rail-settings").addEventListener("click", () => $("btn-settings").click());
  // a diagram opened in the project the page shows (tree, new, import, project chat) must be visible: leave the page; a
  // project switch opens the new project's diagram too, but there `state.projectId` is not yet the shown project
  let shownProject = null;
  onDiagramSwitched(() => { if (state.mainView === "architecture" && state.projectId === shownProject) setMainView("diagram"); });
  $("nav-tree").addEventListener("click", (ev) => { // also the diagram that is open already
    if (state.mainView === "architecture" && ev.target.closest('[data-action="open"]')) setMainView("diagram");
  });
  let first = true;
  onProjectShown(async (project) => {
    shownProject = project?.id ?? null;
    if (!project) { // the last project is gone: nothing for these views to show
      if (state.mainView !== "diagram") await setMainView("diagram");
      return;
    }
    if (!first) return; // the first project is there: open the view a deep link names
    first = false;
    if (location.hash) await apply();
  });
}
