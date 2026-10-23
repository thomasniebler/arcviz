# ADR-0020: Native ES modules by feature, pure logic in separate Node-testable modules

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/frontend-modules.md`

## Context

`arcviz/static/app.js` is about 1300 lines with shared mutable state and functions that call each
other across features. The project has no frontend build step (CLAUDE.md), the Docker image and the
wheel serve `arcviz/static/` as is, and `tests/js/` already tests a few pure modules with
`node --test`. Issue #88 asks for a split into modules that can be tested like those. Later milestones
add frontend features on top of the result.

## Options

1. **Native ES modules, split by feature, without a bundler.** Every browser the UI targets loads
   `<script type="module">` and relative imports; the server already serves the directory. Costs a few
   more requests on first load (HTTP/1.1 keep-alive, a few KB each, cached afterwards).
2. **A bundler (esbuild, Rollup) producing one file.** Fewer requests and the freedom to use npm
   packages, but a build step in `make`, CI, the Dockerfile and the wheel, and a generated file that
   must not drift from its sources.
3. **Keep one file, only extract more pure helpers.** Smallest diff, but the features stay entangled
   through shared state and the file keeps growing with every milestone.

How feature modules that need each other avoid import cycles:

A. **Hooks.** The diagram module, which shows a new state, offers `onDiagramShown` /
   `onDiagramSwitched`; features that must react (inspector, versions) register there. Lower modules
   never import higher ones; a test checks that the graph is acyclic.
B. **Allow cycles between modules that only export function declarations.** Works in ES modules as
   long as nothing runs during evaluation, but the order of evaluation becomes fragile and one
   top-level statement in the wrong module breaks the page.
C. **A global event bus.** Decoupled, but every call becomes an untyped string event that is hard to
   follow in a code base of this size.

## Decision

Option 1 with A: native ES modules split by feature with pure logic in separate DOM-free modules, and
hooks on the diagram module instead of cycles, because it needs no build step or dependency, makes the
logic testable in Node and keeps a direction in the code that a test can enforce.

## Consequences

- `index.html` is unchanged; the browser loads about twenty small modules instead of five.
- Pure logic gets unit tests in `tests/js/`; DOM wiring remains covered only by the e2e smoke test.
- New frontend code has a place: pure logic in a pure module with a test, DOM wiring in the feature's
  module, cross-feature reactions through hooks. `tests/js/modules.test.mjs` fails on a cycle, on DOM
  access during evaluation, or on a pure module importing a DOM module.
- If the number of modules ever makes first load slow, a bundler can be added later without changing
  the module boundaries (option 2 stays open).
