// Unit tests for the SSE reader of the chat turn. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { createSseParser, readSse } from "../../arcviz/static/sse.js";

test("parser returns complete events only", () => {
  const p = createSseParser();
  assert.deepEqual(p.push('data: {"type":"text","text":"a"}\n\ndata: {"type":'), [{ type: "text", text: "a" }]);
  assert.deepEqual(p.push('"done"}\n'), []);
  assert.deepEqual(p.push("\n"), [{ type: "done" }]);
});

test("parser skips blocks without a data line and ignores other fields", () => {
  const p = createSseParser();
  assert.deepEqual(p.push(": keep-alive\n\nevent: x\nid: 3\ndata: {\"n\":1}\n\n"), [{ n: 1 }]);
});

test("parser rejects malformed JSON", () => {
  assert.throws(() => createSseParser().push("data: {nope\n\n"), SyntaxError);
});

function streamOf(chunks) {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(typeof c === "string" ? enc.encode(c) : c);
      controller.close();
    },
  });
}

test("readSse awaits the handler per event, in order", async () => {
  const seen = [];
  await readSse(streamOf(['data: {"i":1}\n\ndata: {"i":2}\n', '\ndata: {"i":3}\n\n']), async (ev) => {
    await new Promise((r) => setTimeout(r, 2));
    seen.push(ev.i);
  });
  assert.deepEqual(seen, [1, 2, 3]);
});

test("readSse decodes multi-byte characters split across reads", async () => {
  const bytes = new TextEncoder().encode('data: {"text":"Kante → ü"}\n\n');
  const cut = bytes.indexOf(0xe2) + 1; // inside the arrow
  const seen = [];
  await readSse(streamOf([bytes.slice(0, cut), bytes.slice(cut)]), (ev) => seen.push(ev.text));
  assert.deepEqual(seen, ["Kante → ü"]);
});

test("readSse propagates a handler error", async () => {
  await assert.rejects(readSse(streamOf(['data: {"i":1}\n\n']), () => { throw new Error("boom"); }), /boom/);
});

test("readSse drops an incomplete trailing event", async () => {
  const seen = [];
  await readSse(streamOf(['data: {"i":1}\n\ndata: {"i":2}']), (ev) => seen.push(ev.i));
  assert.deepEqual(seen, [1]);
});
