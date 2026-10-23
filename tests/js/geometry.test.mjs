// Unit tests for the pan/zoom and splitter maths. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CANVAS_MIN, CHAT_MIN, FIT_PADDING, SPLITTER_WIDTH, ZOOM_MAX, ZOOM_MIN,
  chatWidthMax, clampChatWidth, fitTransform, viewTransform, wheelFactor, zoomAt, zoomLabel,
} from "../../arcviz/static/geometry.js";

const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("zoomAt keeps the diagram point under the cursor fixed", () => {
  const view = { scale: 1.5, x: 40, y: -20 };
  const cx = 300, cy = 200;
  const before = { x: (cx - view.x) / view.scale, y: (cy - view.y) / view.scale };
  const next = zoomAt(view, 1.2, cx, cy);
  close((cx - next.x) / next.scale, before.x);
  close((cy - next.y) / next.scale, before.y);
  close(next.scale, 1.8);
  assert.deepEqual(view, { scale: 1.5, x: 40, y: -20 }, "input view is not mutated");
});

test("zoomAt clamps to the interactive limits", () => {
  assert.equal(zoomAt({ scale: 3.9, x: 0, y: 0 }, 2, 0, 0).scale, ZOOM_MAX);
  assert.equal(zoomAt({ scale: 0.11, x: 0, y: 0 }, 0.5, 0, 0).scale, ZOOM_MIN);
  // at the limit the translation does not drift
  assert.deepEqual(zoomAt({ scale: ZOOM_MAX, x: 10, y: 20 }, 1.5, 100, 100), { scale: ZOOM_MAX, x: 10, y: 20 });
});

test("fitTransform centres the diagram with padding", () => {
  const fit = fitTransform({ width: 400, height: 100 }, { width: 848, height: 648 });
  close(fit.scale, 2);
  close(fit.x, FIT_PADDING); // 800 px of content fill the padded width exactly
  close(fit.y, FIT_PADDING + (600 - 200) / 2);
});

test("fitTransform may go below the interactive minimum but not above the maximum", () => {
  assert.ok(fitTransform({ width: 100000, height: 100 }, { width: 848, height: 648 }).scale < ZOOM_MIN);
  assert.equal(fitTransform({ width: 10, height: 10 }, { width: 848, height: 648 }).scale, ZOOM_MAX);
});

test("fitTransform gives up on an empty canvas or an unknown size", () => {
  assert.equal(fitTransform({ width: 100, height: 100 }, { width: 40, height: 600 }), null);
  assert.equal(fitTransform(null, { width: 800, height: 600 }), null);
});

test("wheelFactor normalises deltaMode and pinches faster", () => {
  close(wheelFactor({ deltaY: 100, deltaMode: 0, ctrlKey: false }, 500), Math.exp(-0.15));
  close(wheelFactor({ deltaY: 3, deltaMode: 1, ctrlKey: false }, 500), Math.exp(-3 * 16 * 0.0015));
  close(wheelFactor({ deltaY: 1, deltaMode: 2, ctrlKey: false }, 500), Math.exp(-500 * 0.0015));
  close(wheelFactor({ deltaY: -10, deltaMode: 0, ctrlKey: true }, 500), Math.exp(0.1));
  assert.ok(wheelFactor({ deltaY: 100, deltaMode: 0 }, 500) < 1, "scrolling down zooms out");
});

test("view transform and zoom label", () => {
  assert.equal(viewTransform({ scale: 0.5, x: 12, y: -3 }), "translate(12px, -3px) scale(0.5)");
  assert.equal(zoomLabel(1), "100 %");
  assert.equal(zoomLabel(1.234), "123 %");
});

test("chat width is clamped between its minimum and the canvas minimum", () => {
  const layout = 1400;
  const max = layout - CANVAS_MIN - SPLITTER_WIDTH;
  assert.equal(chatWidthMax(layout), max);
  assert.equal(clampChatWidth(100, layout), CHAT_MIN);
  assert.equal(clampChatWidth(500.4, layout), 500);
  assert.equal(clampChatWidth(Infinity, layout), max);
  // a layout too narrow for both minimums keeps the chat minimum
  assert.equal(chatWidthMax(400), CHAT_MIN);
  assert.equal(clampChatWidth(900, 400), CHAT_MIN);
});
