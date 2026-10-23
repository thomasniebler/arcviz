// The inspector's writes (spec: docs/architecture/specs/collaboration.md, ADR-0099): one after another, each bound to
// the diagram it was queued for. A write based on a revision the user saw is sent with that revision, advanced only
// by this tab's own writes completed since (never by someone else's change, which must answer 412).
// Pure module: no DOM access, unit-tested with node --test.

const KEEP = 64; // own revision steps remembered (one diagram; the oldest go first)

/**
 * A queue over `shown()` → `{diagramId, revision}` (what the tab shows now). `enqueue(run, {diagramId, revision})`
 * calls `run(expected)` when its turn comes and resolves to `{dropped: false, value}`; a write whose diagram is no
 * longer shown by then resolves to `{dropped: true}` and is never sent (it would land on another diagram).
 * `revision` is the revision the write is based on (captured when the user saved), moved along `advanced` steps;
 * without one the shown revision at the start is used. `advanced(diagramId, from, to)` records that one of this tab's
 * writes, sent with If-Match `from`, produced `to`. A failing write rejects its own promise only.
 */
export function createWriteQueue(shown) {
  let chain = Promise.resolve();
  let steps = { diagramId: null, next: new Map() };

  function follow(diagramId, revision) {
    if (steps.diagramId !== diagramId) return revision;
    const seen = new Set();
    while (steps.next.has(revision) && !seen.has(revision)) { seen.add(revision); revision = steps.next.get(revision); }
    return revision;
  }

  return {
    enqueue(run, { diagramId, revision } = {}) {
      const task = chain.then(async () => {
        const now = shown();
        if (now.diagramId !== diagramId) return { dropped: true };
        const expected = revision == null ? now.revision : follow(diagramId, revision);
        return { dropped: false, value: await run(expected) };
      });
      chain = task.catch(() => {});
      return task;
    },
    advanced(diagramId, from, to) {
      if (from == null || to == null || from === to) return;
      if (steps.diagramId !== diagramId) steps = { diagramId, next: new Map() };
      steps.next.set(from, to);
      if (steps.next.size > KEEP) steps.next.delete(steps.next.keys().next().value);
    },
  };
}
