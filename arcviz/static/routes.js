// Hash routes of the app shell (spec: docs/architecture/specs/app-shell.md, ADR-0194): which main view the URL
// hash selects. Pure: the DOM-facing router is archview.js.

/** The tabs of the Architektur page, in display order; the first is the default. */
export const ARCH_TABS = ["drivers", "scenarios", "decisions", "stakeholders", "vision", "documentation", "gaps"];

function decode(part) {
  try { return decodeURIComponent(part); } catch { return ""; }
}

/** `{view: "diagram"}`, `{view: "requirements"}` or `{view: "architecture", tab}` for a location hash. A permalink
 * `#/p/<project>[/d/<diagram> | /requirements | /architecture/<tab>]` adds `project` (and `diagram`) to the route. */
export function parseRoute(hash) {
  const parts = typeof hash === "string" ? hash.replace(/^#\/?/, "").split("/") : [];
  if (parts[0] === "p" && parts[1] && decode(parts[1])) {
    const project = decode(parts[1]);
    if (parts[2] === "d" && parts[3] && decode(parts[3])) return { view: "diagram", project, diagram: decode(parts[3]) };
    return { ...parseRoute(`#/${parts.slice(2).join("/")}`), project };
  }
  if (parts[0] === "requirements") return { view: "requirements" };
  if (parts[0] === "architecture") return { view: "architecture", tab: ARCH_TABS.includes(parts[1]) ? parts[1] : ARCH_TABS[0] };
  return { view: "diagram" };
}

/** The permalink hash of a project, of one of its diagrams, or of one of its views (`route.view`/`route.tab`). */
export function permalink(route) {
  const base = `#/p/${encodeURIComponent(route.project)}`;
  if (route.diagram) return `${base}/d/${encodeURIComponent(route.diagram)}`;
  return base + formatRoute(route).replace(/^#/, "");
}

/** The hash for a route (empty for the diagram view). */
export function formatRoute(route) {
  if (route?.view === "requirements") return "#/requirements";
  if (route?.view === "architecture") return `#/architecture/${ARCH_TABS.includes(route.tab) ? route.tab : ARCH_TABS[0]}`;
  return "";
}
