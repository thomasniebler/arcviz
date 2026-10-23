// Unit tests for the hash routes (spec: docs/architecture/specs/app-shell.md). Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { ARCH_TABS, formatRoute, parseRoute } from "../../arcviz/static/routes.js";

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
