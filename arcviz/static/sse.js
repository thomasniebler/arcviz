// Server-sent events over a POST response body (the chat turn; spec: docs/spec/frontend.md).
// Pure module: no DOM access, unit-tested with node --test.

/** Incremental parser: `push(text)` returns the events completed by `text` (JSON of each `data:` line). */
export function createSseParser() {
  let buffer = "";
  return {
    push(text) {
      buffer += text;
      const events = [];
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (line) events.push(JSON.parse(line.slice(6)));
      }
      return events;
    },
  };
}

/** Read a `ReadableStream` of SSE bytes and await `onEvent(event)` for every event, in order. */
export async function readSse(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const ev of parser.push(decoder.decode(value, { stream: true }))) await onEvent(ev);
  }
}
