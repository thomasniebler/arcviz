// Structural rules of the frontend modules (spec: docs/architecture/specs/frontend-modules.md):
// the import graph is acyclic and follows the layers, pure modules import only pure modules, and every
// module except the entry point can be evaluated without a DOM. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const staticDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "arcviz", "static");
const modules = readdirSync(staticDir).filter((f) => f.endsWith(".js")).sort();
const ENTRY = "app.js";
const PURE = ["access.js", "agentaccess.js", "commands.js", "conflicts.js", "decisions.js", "drivers.js", "elements.js", "lifecycle.js", "listnav.js", "palette.js", "presence.js", "writequeue.js", "findings.js", "format.js", "geometry.js", "i18n.js", "model.js", "requirements.js", "steward.js", "testability.js", "vision.js", "markdown.js", "onboarding.js", "projects.js", "projectchat.js", "proposal.js", "qualities.js", "reply.js", "relations.js", "routes.js", "sharedmodel.js", "skins.js", "speech.js", "sse.js", "svgsafe.js", "types.js", "whatsnew.js"];
// Layer of every module (the table in the spec); a module imports only modules of a lower layer.
const LAYERS = {
  ...Object.fromEntries(PURE.map((f) => [f, 0])),
  "i18n.js": -1, // below every other module: all of them may translate
  "dom.js": 1, "state.js": 1, "api.js": 1,
  "canvas.js": 2, "mdpane.js": 2, "chatlog.js": 2, "ui.js": 2, "requirementpicker.js": 2,
  "diagram.js": 3,
  "navigator.js": 4,
  "appearance.js": 5, "decisionpanel.js": 5, "driverpanel.js": 5, "documentationpanel.js": 5, "scenariopanel.js": 5, "stakeholderpanel.js": 5, "stewardpanel.js": 5, "visionpanel.js": 5, "export.js": 5, "inspector.js": 5, "live.js": 5, "modelpanel.js": 5, "proposalpanel.js": 5, "requirementview.js": 5, "tokenpanel.js": 5, "review.js": 5, "settings.js": 5, "splitter.js": 5, "topbar.js": 5, "versions.js": 5, "voice.js": 5, "releasebanner.js": 5, "welcome.js": 5, "workspaces.js": 5,
  "chat.js": 6, "archview.js": 6, "paletteview.js": 6,
  "shell.js": 7,
  [ENTRY]: 8,
};

function importsOf(file) {
  const src = readFileSync(join(staticDir, file), "utf8");
  const found = [...src.matchAll(/^\s*(?:import|export)\s[^;]*?from\s+"([^"]+)"/gms), ...src.matchAll(/^\s*import\s+"([^"]+)"/gm)].map((m) => m[1]);
  for (const spec of found) assert.match(spec, /^\.\/[\w-]+\.js$/, `${file}: imports must be relative to arcviz/static (${spec})`);
  return found.map((s) => s.slice(2));
}

const graph = Object.fromEntries(modules.map((f) => [f, importsOf(f)]));

test("every imported module exists", () => {
  for (const [file, deps] of Object.entries(graph)) for (const d of deps) assert.ok(modules.includes(d), `${file} imports missing ${d}`);
});

test("the import graph is acyclic", () => {
  const state = {};
  const visit = (f, path) => {
    if (state[f] === "done") return;
    assert.notEqual(state[f], "active", `import cycle: ${[...path, f].join(" -> ")}`);
    state[f] = "active";
    for (const d of graph[f] || []) visit(d, [...path, f]);
    state[f] = "done";
  };
  for (const f of modules) visit(f, []);
});

test("pure modules import only pure modules", () => {
  for (const f of PURE) {
    assert.ok(modules.includes(f), `pure module ${f} exists`);
    for (const d of graph[f]) assert.ok(PURE.includes(d), `${f} is pure but imports ${d}`);
  }
});

test("every module has a layer and imports only from lower layers", () => {
  for (const f of modules) {
    assert.ok(f in LAYERS, `${f} has no layer: add it to the spec's table and to LAYERS`);
    for (const d of graph[f]) assert.ok(LAYERS[d] < LAYERS[f], `${f} (layer ${LAYERS[f]}) must not import ${d} (layer ${LAYERS[d]})`);
  }
});

test("nothing imports the entry point", () => {
  for (const [file, deps] of Object.entries(graph)) assert.ok(!deps.includes(ENTRY), `${file} imports ${ENTRY}`);
});

test("every module except the entry point evaluates without a DOM", async () => {
  assert.equal(typeof globalThis.document, "undefined");
  assert.equal(typeof globalThis.window, "undefined");
  for (const f of modules.filter((m) => m !== ENTRY)) {
    await assert.doesNotReject(import(pathToFileURL(join(staticDir, f)).href), `${f} must not touch the DOM at import time`);
  }
});
