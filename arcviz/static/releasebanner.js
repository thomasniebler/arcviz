// The "new in this release" banner (spec: docs/architecture/specs/whats-new.md): shown once per release until it is
// dismissed, reopened from Hilfe -> Neu in dieser Version. Never blocks the page.
import { $ } from "./dom.js";
import { onProjectShown } from "./navigator.js";
import { state } from "./state.js";
import { bannerTitle, bannerVisible, CHANGELOG_PATH, dismissKey, highlights } from "./whatsnew.js";

function recall(key) { try { return localStorage.getItem(key); } catch { return null; } }
function remember(key, value) { try { localStorage.setItem(key, value); } catch {} }

let entry = null;

function render() {
  $("whats-new-title").textContent = bannerTitle(entry, state.config.version);
  const list = $("whats-new-list");
  list.replaceChildren(...entry.items.map((item) => {
    // DOM APIs only: the text comes from a file of the deployment.
    const li = document.createElement("li");
    const emoji = document.createElement("span");
    emoji.setAttribute("aria-hidden", "true");
    emoji.textContent = item.emoji;
    li.append(emoji, document.createTextNode(` ${item.text}`));
    return li;
  }));
}

function show(open) {
  $("whats-new").classList.remove("hidden");
  $("whats-new-details").open = open;
}

function dismiss() {
  $("whats-new").classList.add("hidden");
  if (entry) remember(dismissKey(entry), "1");
}

export function initReleaseBanner() {
  $("whats-new-link").href = CHANGELOG_PATH;
  $("whats-new-close").addEventListener("click", dismiss);
  $("btn-whats-new").addEventListener("click", () => {
    $("help-menu").open = false;
    if (entry) show(true);
  });
  // the configuration is loaded before the first project is shown; the banner is decided once
  let decided = false;
  onProjectShown(() => {
    if (decided) return;
    decided = true;
    entry = highlights(state.config);
    $("btn-whats-new").hidden = !entry;
    if (!entry) return;
    render();
    if (bannerVisible(entry, recall(dismissKey(entry)))) show(false);
  });
}
