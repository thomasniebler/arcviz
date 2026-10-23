// The inspector's write queue (spec: docs/architecture/specs/collaboration.md): writes run one after another, each
// bound to the diagram it was queued for, with its If-Match revision advanced only by
// this tab's own writes.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createWriteQueue } from "../../arcviz/static/writequeue.js";

test("writes run one after another", async () => {
  const shown = { diagramId: "a", revision: "r1" };
  const queue = createWriteQueue(() => shown);
  const order = [];
  const slow = queue.enqueue(async () => { await new Promise((r) => setTimeout(r, 20)); order.push(1); return 1; }, { diagramId: "a" });
  const fast = queue.enqueue(async () => { order.push(2); return 2; }, { diagramId: "a" });
  assert.deepEqual(await Promise.all([slow, fast]), [{ dropped: false, value: 1 }, { dropped: false, value: 2 }]);
  assert.deepEqual(order, [1, 2]);
});

test("a write queued for a diagram that is no longer shown is dropped, never sent to the new one", async () => {
  const shown = { diagramId: "a", revision: "ra" };
  const queue = createWriteQueue(() => shown);
  const sent = [];
  const first = queue.enqueue(async () => { shown.diagramId = "b"; shown.revision = "rb"; return "first"; }, { diagramId: "a" });
  const second = queue.enqueue(async (revision) => { sent.push(revision); }, { diagramId: "a" });
  assert.deepEqual(await first, { dropped: false, value: "first" });
  assert.deepEqual(await second, { dropped: true });
  assert.deepEqual(sent, []);
});

test("without a captured revision a write uses the shown revision when it starts", async () => {
  const shown = { diagramId: "a", revision: "r1" };
  const queue = createWriteQueue(() => shown);
  const seen = [];
  queue.enqueue(async (revision) => { seen.push(revision); shown.revision = "r2"; }, { diagramId: "a" });
  await queue.enqueue(async (revision) => { seen.push(revision); }, { diagramId: "a" });
  assert.deepEqual(seen, ["r1", "r2"]);
});

test("a save based on what the user saw is advanced only by this tab's own writes", async () => {
  const shown = { diagramId: "a", revision: "r1" };
  const queue = createWriteQueue(() => shown);
  const seen = [];
  // an edge edit of this tab, queued first, moves r1 -> r2; the form save of X was based on r1
  queue.enqueue(async (revision) => { seen.push(revision); queue.advanced("a", revision, "r2"); shown.revision = "r2"; }, { diagramId: "a" });
  await queue.enqueue(async (revision) => { seen.push(revision); }, { diagramId: "a", revision: "r1" });
  assert.deepEqual(seen, ["r1", "r2"]);
  // someone else's change (r2 -> r3) is no own step: a save based on r2 is sent with r2 and answers 412
  shown.revision = "r3";
  const stale = await queue.enqueue(async (revision) => revision, { diagramId: "a", revision: "r2" });
  assert.equal(stale.value, "r2");
  // never another diagram's steps, never the shown revision for a captured one
  queue.advanced("b", "r2", "r9");
  assert.equal((await queue.enqueue(async (revision) => revision, { diagramId: "a", revision: "r1" })).value, "r1");
});

test("own steps are bounded and a cycle never hangs", async () => {
  const queue = createWriteQueue(() => ({ diagramId: "a", revision: "x" }));
  queue.advanced("a", "p", "q");
  queue.advanced("a", "q", "p");
  assert.ok(["p", "q"].includes((await queue.enqueue(async (r) => r, { diagramId: "a", revision: "p" })).value));
  for (let i = 0; i < 200; i++) queue.advanced("a", `s${i}`, `s${i + 1}`);
  assert.equal((await queue.enqueue(async (r) => r, { diagramId: "a", revision: "s0" })).value, "s0"); // forgotten
  assert.equal((await queue.enqueue(async (r) => r, { diagramId: "a", revision: "s190" })).value, "s200");
});

test("a failed write does not stop the queue", async () => {
  const queue = createWriteQueue(() => ({ diagramId: "a", revision: "r" }));
  await assert.rejects(queue.enqueue(async () => { throw new Error("boom"); }, { diagramId: "a" }));
  assert.deepEqual(await queue.enqueue(async () => "ok", { diagramId: "a" }), { dropped: false, value: "ok" });
});
