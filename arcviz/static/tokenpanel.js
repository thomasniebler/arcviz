// Access for agents (spec: docs/architecture/specs/agent-authentication.md): the caller's personal access tokens for the
// MCP endpoint, with ready-to-paste client configuration. The value of a new token is shown once.
import { api } from "./api.js";
import { $ } from "./dom.js";
import { claudeAddCommand, expiryText, jsonConfig, mcpUrl, scopeLabel, tokenBody } from "./agentaccess.js";
import { t } from "./i18n.js";
import { state } from "./state.js";

let created = null; // the token value of the token made in this dialog session, to build the snippets

function say(text) { $("agent-status").textContent = text || ""; }

async function guarded(action) {
  try { await action(); } catch (err) { say(err.message || String(err)); }
}

function mcp() { return state.config?.mcp || { enabled: false, path: "/mcp", oauth: false }; }

function snippet(label, text) {
  const box = document.createElement("div");
  box.className = "agent-snippet";
  const caption = document.createElement("div");
  caption.className = "muted";
  caption.textContent = label;
  const code = document.createElement("pre");
  code.textContent = text;
  const copy = document.createElement("button");
  copy.type = "button"; copy.className = "small"; copy.textContent = t("Kopieren");
  copy.addEventListener("click", () => guarded(async () => {
    await navigator.clipboard.writeText(text);
    say(t("In die Zwischenablage kopiert."));
  }));
  box.append(caption, code, copy);
  return box;
}

function renderCreated() {
  const box = $("agent-created");
  box.replaceChildren();
  box.classList.toggle("hidden", created === null);
  if (created === null) return;
  const url = mcpUrl(window.location.origin, mcp().path);
  const warning = document.createElement("p");
  warning.className = "agent-warning";
  warning.textContent = t("Dieses Token wird nur jetzt angezeigt. Kopiere es in die Konfiguration des Agenten.");
  box.append(
    warning,
    snippet(t("Token"), created),
    snippet("Claude Code", claudeAddCommand(url, created)),
    snippet(t("Andere Clients (.mcp.json)"), jsonConfig(url, created)),
  );
}

async function render() {
  const data = await api("/api/tokens");
  const list = $("agent-list");
  list.replaceChildren();
  $("agent-empty").classList.toggle("hidden", data.tokens.length > 0);
  for (const token of data.tokens) {
    const li = document.createElement("li");
    li.className = "model-row agent-row";
    li.dataset.tokenId = token.id;
    const text = document.createElement("span");
    text.textContent = `${token.label} · ${scopeLabel(token.scope)} · ${expiryText(token.expires_at)}`;
    const revoke = document.createElement("button");
    revoke.type = "button"; revoke.className = "small danger agent-revoke"; revoke.textContent = t("Widerrufen");
    revoke.addEventListener("click", () => guarded(async () => {
      if (!confirm(t("Token „{label}“ widerrufen? Agenten, die es benutzen, verlieren sofort den Zugriff.", { label: token.label }))) return;
      await api(`/api/tokens/${encodeURIComponent(token.id)}`, { method: "DELETE" });
      say(t("Token „{label}“ widerrufen.", { label: token.label }));
      await render();
    }));
    li.append(text, revoke);
    list.appendChild(li);
  }
  const info = mcp();
  $("agent-disabled").classList.toggle("hidden", info.enabled);
  $("agent-oauth").classList.toggle("hidden", !info.oauth);
  $("agent-admin").classList.toggle("hidden", state.config?.auth?.mode !== "token");
  $("agent-url").textContent = mcpUrl(window.location.origin, info.path);
  renderCreated();
}

async function open() {
  created = null;
  say("");
  await render();
  $("agent-dialog").showModal();
}

export function initTokenPanel() {
  $("btn-agent-access").addEventListener("click", () => {
    $("settings-dialog")?.close(); // opened from the personal settings: one dialog at a time
    guarded(open);
  });
  $("agent-close").addEventListener("click", () => { created = null; $("agent-dialog").close(); });
  $("agent-new").addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(async () => {
      const body = tokenBody({ label: $("agent-new-label").value, scope: $("agent-new-scope").value, days: $("agent-new-days").value });
      if (!body) { say(t("Bitte eine Bezeichnung angeben.")); return; }
      const result = await api("/api/tokens", { method: "POST", body: JSON.stringify(body) });
      created = result.token;
      $("agent-new-label").value = "";
      say(t("Token „{label}“ angelegt.", { label: result.record.label }));
      await render();
    });
  });
}
