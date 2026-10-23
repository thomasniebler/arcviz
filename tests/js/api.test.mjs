// 401 handling of the API client (spec: docs/architecture/specs/authentication.md).
import { test } from "node:test";
import assert from "node:assert/strict";

const { apiFetch, api, loginUrl } = await import("../../arcviz/static/api.js");

function fakeBrowser(status, body = {}) {
  const assigned = [];
  globalThis.window = { location: { pathname: "/", search: "?d=demo", assign: (url) => assigned.push(url) } };
  globalThis.fetch = async () => ({ status, ok: status < 400, statusText: "x", json: async () => body });
  return assigned;
}

test("the login URL brings the user back to the current page", () => {
  assert.equal(loginUrl({ pathname: "/", search: "?d=a b" }), "/login?next=%2F%3Fd%3Da%20b");
});

test("a 401 sends the browser to the login page and rejects", async () => {
  const assigned = fakeBrowser(401, { detail: "Authentication required." });
  await assert.rejects(apiFetch("/api/config"), /Anmeldung erforderlich/);
  await assert.rejects(api("/api/config"), /Anmeldung erforderlich/);
  assert.deepEqual(assigned, ["/login?next=%2F%3Fd%3Ddemo", "/login?next=%2F%3Fd%3Ddemo"]);
});

test("other responses pass through unchanged", async () => {
  const assigned = fakeBrowser(200, { ok: 1 });
  assert.deepEqual(await api("/api/config"), { ok: 1 });
  fakeBrowser(413, { detail: "Request body too large (max 10 bytes)." });
  await assert.rejects(api("/api/x", { method: "POST" }), /Request body too large/);
  assert.deepEqual(assigned, []);
  delete globalThis.window;
  delete globalThis.fetch;
});

test("a 403 explains the read-only access in German", async () => {
  fakeBrowser(403, { detail: "Read-only access: your role on project 'shop' is viewer; this needs editor." });
  await assert.rejects(api("/api/diagrams/x/ops", { method: "POST" }), /^Error: Nur Lesezugriff: .*\(Read-only access/);
  delete globalThis.window;
  delete globalThis.fetch;
});
