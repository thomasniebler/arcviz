// The top bar is a menu bar (spec: docs/architecture/specs/frontend-modules.md, top bar): the menus **Diagramm**,
// **Version**, **Hilfe** and the user menu are <details> elements. This module makes them behave like menus: at most one
// is open, choosing an action, clicking elsewhere or pressing Esc closes it. The controls keep their ids and listeners;
// only where they live changed.
import { $ } from "./dom.js";

export function initTopbar() {
  const bar = document.querySelector(".topbar");
  if (!bar) return;
  const menus = () => [...bar.querySelectorAll("details.menu")];
  const closeAll = (except = null) => { for (const m of menus()) if (m !== except) m.open = false; };
  for (const m of menus()) m.addEventListener("toggle", () => { if (m.open) closeAll(m); });
  // choosing an action closes the menu (a selector or a label inside it keeps it open)
  bar.addEventListener("click", (ev) => { if (ev.target.closest(".menu-items button, .menu-items a")) closeAll(); });
  document.addEventListener("click", (ev) => { if (!ev.target.closest(".topbar details.menu")) closeAll(); });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    const open = menus().find((m) => m.open);
    if (!open) return;
    open.open = false;
    open.querySelector("summary")?.focus();
  });
}
