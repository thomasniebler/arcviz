// Hash routes of the app shell (spec: docs/architecture/specs/app-shell.md, ADR-0194): which main view the URL
// hash selects. Pure: the DOM-facing router is archview.js.

/** The tabs of the Architektur page, in display order; the first is the default. */
export const ARCH_TABS = ["drivers", "scenarios", "decisions", "stakeholders"];

/** `{view: "diagram"}`, `{view: "requirements"}` or `{view: "architecture", tab}` for a location hash. */
export function parseRoute(hash) {
  const parts = typeof hash === "string" ? hash.replace(/^#\/?/, "").split("/") : [];
  if (parts[0] === "requirements") return { view: "requirements" };
  if (parts[0] === "architecture") return { view: "architecture", tab: ARCH_TABS.includes(parts[1]) ? parts[1] : ARCH_TABS[0] };
  return { view: "diagram" };
}

/** The hash for a route (empty for the diagram view). */
export function formatRoute(route) {
  if (route?.view === "requirements") return "#/requirements";
  if (route?.view === "architecture") return `#/architecture/${ARCH_TABS.includes(route.tab) ? route.tab : ARCH_TABS[0]}`;
  return "";
}
