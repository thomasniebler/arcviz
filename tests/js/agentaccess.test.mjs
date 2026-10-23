import assert from "node:assert/strict";
import test from "node:test";
import { claudeAddCommand, expiryText, jsonConfig, mcpUrl, scopeLabel, tokenBody } from "../../arcviz/static/agentaccess.js";

test("the MCP url joins origin and path", () => {
  assert.equal(mcpUrl("https://arc.example.org", "/mcp"), "https://arc.example.org/mcp");
  assert.equal(mcpUrl("https://arc.example.org/", "/mcp"), "https://arc.example.org/mcp");
  assert.equal(mcpUrl("http://localhost:8000", undefined), "http://localhost:8000/mcp");
});

test("snippets carry the url and the token", () => {
  const url = "https://arc.example.org/mcp";
  assert.equal(
    claudeAddCommand(url, "avp_x"),
    'claude mcp add --transport http arcviz https://arc.example.org/mcp --header "Authorization: Bearer avp_x"',
  );
  assert.deepEqual(JSON.parse(jsonConfig(url, "avp_x")), {
    mcpServers: { arcviz: { type: "http", url, headers: { Authorization: "Bearer avp_x" } } },
  });
  assert.equal(claudeAddCommand(url, null), "claude mcp add --transport http arcviz https://arc.example.org/mcp");
});

test("expiry in words", () => {
  const now = Date.parse("2026-10-19T12:00:00Z");
  assert.equal(expiryText("2026-10-19T18:00:00+00:00", now), "läuft heute ab");
  assert.equal(expiryText("2026-10-20T13:00:00+00:00", now), "läuft morgen ab");
  assert.equal(expiryText("2026-11-01T12:00:00+00:00", now), "läuft in 13 Tagen ab");
  assert.equal(expiryText("2026-10-19T11:00:00+00:00", now), "abgelaufen");
  assert.equal(expiryText("kaputt", now), "");
});

test("scope labels and the create body", () => {
  assert.equal(scopeLabel("read"), "nur lesen");
  assert.equal(scopeLabel("write"), "lesen und schreiben (Codeverknüpfungen, Vorschläge)");
  assert.equal(scopeLabel("x"), "x");
  assert.deepEqual(tokenBody({ label: "  Laptop ", scope: "write", days: "30" }), { label: "Laptop", scope: "write", expires_in_days: 30 });
  assert.equal(tokenBody({ label: " ", scope: "read", days: "30" }), null);
  assert.deepEqual(tokenBody({ label: "x", scope: "read", days: "abc" }), { label: "x", scope: "read", expires_in_days: 90 });
});
