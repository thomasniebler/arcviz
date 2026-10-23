// Unit tests for the streamed reply buffer. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { createReplyBuffer } from "../../arcviz/static/reply.js";

test("deltas accumulate into the running round", () => {
  const b = createReplyBuffer();
  assert.equal(b.value(), "");
  assert.equal(b.delta("Ich lege "), "Ich lege ");
  assert.equal(b.delta("das an."), "Ich lege das an.");
});

test("the round text replaces what was streamed for it", () => {
  const b = createReplyBuffer();
  b.delta("Ich lege das ");
  b.delta("an?? (garbled)");
  assert.equal(b.round("Ich lege das an."), "Ich lege das an.");
});

test("rounds are joined with a blank line, empty rounds are skipped", () => {
  const b = createReplyBuffer();
  b.round("Erste Runde.");
  b.round("");
  b.delta("Zweite");
  assert.equal(b.value(), "Erste Runde.\n\nZweite");
  assert.equal(b.round("Zweite Runde."), "Erste Runde.\n\nZweite Runde.");
});

test("rounds without deltas (non-streaming provider) behave as before", () => {
  const b = createReplyBuffer();
  assert.equal(b.round("A"), "A");
  assert.equal(b.round("B"), "A\n\nB");
});
