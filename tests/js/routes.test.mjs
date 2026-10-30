// Unit tests for the hash routes (spec: docs/architecture/specs/app-shell.md). Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { ARCH_TABS, formatRoute, parseRoute, permalink } from "../../arcviz/static/routes.js";

test("an empty or unknown hash is the diagram view", () => {
  for (const h of ["", "#", "#/", "#/nope", "#/architecture/../x/y", null, undefined, 42]) {
    if (h === "#/architecture/../x/y") continue; // handled below: it is an architecture route with an unknown tab
    assert.deepEqual(parseRoute(h), { view: "diagram" }, String(h));
  }
});

test("architecture routes carry the tab, unknown tabs fall back to drivers", () => {
  assert.deepEqual(parseRoute("#/architecture"), { view: "architecture", tab: "drivers" });
  assert.deepEqual(parseRoute("#/architecture/"), { view: "architecture", tab: "drivers" });
  for (const tab of ARCH_TABS) assert.deepEqual(parseRoute(`#/architecture/${tab}`), { view: "architecture", tab });
  assert.deepEqual(parseRoute("#/architecture/unicorn"), { view: "architecture", tab: "drivers" });
  assert.deepEqual(parseRoute("#/architecture/../x/y"), { view: "architecture", tab: "drivers" });
});

test("the requirements route", () => {
  assert.deepEqual(parseRoute("#/requirements"), { view: "requirements" });
});

test("formatRoute is the inverse of parseRoute", () => {
  assert.equal(formatRoute({ view: "diagram" }), "");
  assert.equal(formatRoute({ view: "requirements" }), "#/requirements");
  for (const tab of ARCH_TABS) {
    const route = { view: "architecture", tab };
    assert.equal(formatRoute(route), `#/architecture/${tab}`);
    assert.deepEqual(parseRoute(formatRoute(route)), route);
  }
  assert.equal(formatRoute({ view: "architecture", tab: "nope" }), "#/architecture/drivers");
});

test("permalinks name a project, a diagram or a view of a project", () => {
  assert.deepEqual(parseRoute("#/p/web-1a2b3c4d5e"), { view: "diagram", project: "web-1a2b3c4d5e" });
  assert.deepEqual(parseRoute("#/p/pr/d/dia-1"), { view: "diagram", project: "pr", diagram: "dia-1" });
  assert.deepEqual(parseRoute("#/p/pr/requirements"), { view: "requirements", project: "pr" });
  assert.deepEqual(parseRoute("#/p/pr/architecture/scenarios"), { view: "architecture", tab: "scenarios", project: "pr" });
  assert.deepEqual(parseRoute("#/p/pr/architecture/unicorn"), { view: "architecture", tab: "drivers", project: "pr" });
  for (const h of ["#/p", "#/p/", "#/p//d/x", "#/p/%E0%A4%A"]) assert.deepEqual(parseRoute(h), { view: "diagram" }, h);
  assert.deepEqual(parseRoute("#/p/pr/d/"), { view: "diagram", project: "pr" });
});

test("permalink is the inverse of parseRoute and escapes ids", () => {
  for (const route of [
    { project: "pr" },
    { project: "pr", diagram: "dia-1" },
    { project: "pr", view: "requirements" },
    { project: "pr", view: "architecture", tab: "gaps" },
  ]) {
    const parsed = parseRoute(permalink(route));
    assert.equal(parsed.project, route.project);
    assert.equal(parsed.diagram, route.diagram);
    if (route.view) assert.equal(parsed.view, route.view);
  }
  assert.equal(permalink({ project: "a/b c", diagram: "d?" }), "#/p/a%2Fb%20c/d/d%3F");
  assert.equal(parseRoute(permalink({ project: "a/b c", diagram: "d?" })).project, "a/b c");
});
