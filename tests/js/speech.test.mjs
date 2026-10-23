// Unit tests for the speech input wrapper with a fake window. Run with: node --test tests/js
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

class FakeRecognition {
  static instances = [];
  constructor() { this.started = false; FakeRecognition.instances.push(this); }
  start() { this.started = true; this.onstart?.(); }
  stop() { this.started = false; this.onend?.(); }
  abort() { this.started = false; this.onend?.(); }
  fail(code) { this.started = false; this.onerror?.({ error: code }); this.onend?.(); } // the browser ends the session after an error
}

globalThis.window = { webkitSpeechRecognition: FakeRecognition, isSecureContext: true };
const { createSpeechInput, speechSupported } = await import("../../arcviz/static/speech.js");

function listener() {
  const seen = { states: [], errors: [] };
  const input = createSpeechInput({ onState: (s) => seen.states.push(s), onError: (m, c) => seen.errors.push(c) });
  return { input, seen };
}

beforeEach(() => { FakeRecognition.instances = []; window.isSecureContext = true; });

test("availability needs the API and a secure context", () => {
  assert.equal(speechSupported(), true);
  window.isSecureContext = false;
  assert.equal(speechSupported(), false);
  assert.equal(createSpeechInput({}), null);
});

test("continuous mode restarts after a normal session end and after no-speech", () => {
  const { input, seen } = listener();
  input.configure({ continuous: true });
  input.begin();
  FakeRecognition.instances.at(-1).stop();          // browser timed the session out
  assert.equal(FakeRecognition.instances.length, 2);
  FakeRecognition.instances.at(-1).fail("no-speech"); // silence is not an error while waiting
  assert.equal(FakeRecognition.instances.length, 3);
  assert.deepEqual(seen.errors, []);
  assert.equal(input.active, true);
  input.stop();
});

for (const code of ["network", "audio-capture", "language-not-supported", "not-allowed", "service-not-allowed"]) {
  test(`continuous mode stops on terminal error '${code}' instead of looping`, () => {
    const { input, seen } = listener();
    input.configure({ continuous: true });
    input.begin();
    FakeRecognition.instances.at(-1).fail(code);
    assert.equal(FakeRecognition.instances.length, 1, "no new recogniser was started");
    assert.deepEqual(seen.errors, [code]);
    assert.equal(input.active, false);
    assert.equal(input.state, "idle");
  });
}
