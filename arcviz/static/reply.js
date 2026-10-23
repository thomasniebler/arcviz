// Assembles the reply text of a chat turn from SSE events (spec: docs/architecture/specs/reply-streaming.md).
// `text_delta` events append to the running round; the round's `text` event is authoritative and
// replaces what was streamed for it. Rounds are joined with a blank line.

export function createReplyBuffer() {
  const rounds = [];
  let partial = "";
  return {
    /** Append a streamed piece of the running round; returns the text to show. */
    delta(text) {
      partial += text || "";
      return this.value();
    },
    /** Finish the running round with its full text; returns the text to show. */
    round(text) {
      rounds.push(text || "");
      partial = "";
      return this.value();
    },
    value() {
      return [...rounds, partial].filter((t) => t.trim()).join("\n\n");
    },
  };
}
