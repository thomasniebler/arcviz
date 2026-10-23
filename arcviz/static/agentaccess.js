// Access for agents (spec: docs/architecture/specs/agent-authentication.md): pure helpers for the token dialog. No DOM;
// tested in tests/js/agentaccess.test.mjs.

import { N_, t } from "./i18n.js";

/** The address of the MCP endpoint of the page's origin. */
export function mcpUrl(origin, path = "/mcp") {
  return `${origin.replace(/\/$/, "")}${path || "/mcp"}`;
}

/** The command that adds the endpoint to Claude Code (the Authorization header only with a token). */
export function claudeAddCommand(url, token, name = "arcviz") {
  const header = token ? ` --header "Authorization: Bearer ${token}"` : "";
  return `claude mcp add --transport http ${name} ${url}${header}`;
}

/** The same as a `.mcp.json` / client configuration. */
export function jsonConfig(url, token, name = "arcviz") {
  const server = { type: "http", url };
  if (token) server.headers = { Authorization: `Bearer ${token}` };
  return JSON.stringify({ mcpServers: { [name]: server } }, null, 2);
}

/** "läuft in 13 Tagen ab" / "läuft heute ab" / "abgelaufen"; empty for an unreadable date. */
export function expiryText(iso, now = Date.now()) {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "";
  if (at <= now) return t("abgelaufen");
  const days = Math.floor((at - now) / 86400000);
  if (days === 0) return new Date(at).getUTCDate() === new Date(now).getUTCDate() ? t("läuft heute ab") : t("läuft morgen ab");
  if (days === 1) return t("läuft morgen ab");
  return t("läuft in {days} Tagen ab", { days });
}

const SCOPE_LABELS = { read: N_("nur lesen"), write: N_("lesen und schreiben (Codeverknüpfungen, Vorschläge)") };

export function scopeLabel(scope) {
  return SCOPE_LABELS[scope] ? t(SCOPE_LABELS[scope]) : scope;
}

/** The POST body of a new token from the form's values, or null without a label. */
export function tokenBody(values) {
  const label = (values.label || "").trim();
  if (!label) return null;
  const days = Number.parseInt(values.days, 10);
  return { label, scope: values.scope || "read", expires_in_days: Number.isFinite(days) ? days : 90 };
}
