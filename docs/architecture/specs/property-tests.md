# Property-based tests and broader server coverage

- Status: Implemented (milestone M11)
- Issue: #89
- ADR: [0021 Hypothesis as a dev-only dependency for property-based tests](../adr/0021-hypothesis-property-tests.md)

## Summary

The example-based tests pin the behaviour of each operation, of the Mermaid renderer and of the HTTP
endpoints for hand-picked inputs. What they cannot show is that the pipeline *operations → integrity →
Mermaid* holds its invariants for every sequence of operations a model or a user can send. This change
adds property-based tests with Hypothesis (dev dependency only) for that pipeline and for the
derived features built on it (exchange round trip, version comparison), and fills the gaps in the
server tests: error paths and endpoints that were only exercised indirectly.

## Goals

- Random sequences of operations, valid and invalid, never leave a diagram with integrity errors,
  and a rejected operation never changes the diagram.
- Every reachable diagram renders to Mermaid whose structure matches the model: one definition per
  node, containers as balanced subgraphs in the right parent, one edge line per edge in model order,
  user text only in escaped form, stubs exactly for the outside endpoints of a focused view.
- The derived functions agree with the model: the JSON export parses back to the same diagram, a
  diagram compared with itself has no changes, the merged comparison view contains both states.
- Every endpoint of `server.py` has a test for its main error responses (unknown diagram, unknown
  version, conflicts, invalid input) and for the static modules of the frontend.
- No network, no API key, deterministic in CI.

## Non-goals

- Fuzzing the LLM layer or the providers' wire formats.
- Property tests of the stores (the contract in `tests/store_contract.py` covers them).
- Changing behaviour. A property that fails because of a real defect is reported as an issue and the
  test documents the current limit (see Open questions), unless the fix is a one-line validation.
- A coverage threshold in CI.

## Design

### Dependency and configuration

`hypothesis` is added to the `dev` extra only (ADR-0021); the wheel and the Docker image do not change.
`tests/conftest.py` registers two settings profiles: `dev` (the default locally: 100 examples,
random seed, no deadline) and `ci` (selected when the environment variable `CI` is set, as on GitHub
Actions: `derandomize=True`, `deadline=None`, `print_blob=True`), so a CI failure is reproducible and
never depends on a lucky seed. `HYPOTHESIS_PROFILE` overrides the choice. `.hypothesis/` is already in
`.gitignore`.

### Generators (`tests/strategies.py`)

- Names and titles: printable Unicode text plus a pool of hostile strings (`<b>x</b>`, `"`, `&amp;`,
  `end`, `subgraph`, `#lt;`, `-->`, leading/trailing spaces). Control characters (including line
  breaks) are excluded; see Open questions.
- Operations are drawn *against the current diagram* (`st.data()`): a reference is an existing id, an
  existing name in another case, or an unknown string; kinds come from the active catalogue plus
  unknown kinds; edge directions from the allowed set plus junk. About one in four operations is
  invalid by construction, so both paths of every operation are exercised. Choices are weighted with an
  explicit table (`weighted()`), because Hypothesis does not pick `one_of` branches or `sampled_from`
  entries uniformly (it favours the first and last), which made junk dominate in a first version.
- Explicit node ids include blank strings; a wrong keyword argument is one of the operations, and one
  operation in ten gets an argument of the wrong JSON type, a dropped or an unknown argument, or a
  non-object `args` (`ill_typed`, #66), batch steps included.
- `apply_batch` steps are drawn from the same generator.

### Properties

`tests/test_properties.py`:

1. **Integrity:** after every `operations.apply` (success or `OperationError`), `integrity_errors()`
   is empty, every edge id is unique and node ids are the dict keys.
2. **Atomicity:** an `OperationError` leaves `model_dump()` unchanged (whole batch included).
3. **Results:** every id a successful operation reports as `touched` exists afterwards.
4. **Mermaid structure** (whole diagram): the first line is `flowchart <direction>`; `id_map` is
   injective and every value matches `n_[A-Za-z0-9_]+`; every node is defined exactly once, inside
   the subgraph of its parent; `subgraph`/`end` are balanced; there is one edge line per edge, in
   model order, between the mapped ids; quoted labels contain no raw `<`, `>`, `"` or `&` apart from
   the `<br/>` separator.
5. **Mermaid focus:** for every node as focus, exactly the focus subtree is defined inside the
   focused subgraph, the stubs are exactly the outside endpoints of edges touching the subtree, and
   only edges touching the subtree are drawn.
6. **Escaping:** `escape_text` output contains none of `<>"&` and decoding the entities gives the
   input back.
7. **Exchange:** `parse_document(export_document(d))` equals `d` (all model fields), and the PlantUML
   export starts with `@startuml` and ends with `@enduml`.
8. **Comparison:** `compare(d, d)` has no changes; for a diagram before and after a random sequence of
   operations, the merged view contains every node of both states, marks are consistent with the diff,
   and it renders (property 4's structure checks apply to it).

### Server tests

`tests/test_server_errors.py` adds, for both store backends:

- `POST /api/diagrams`: unknown template (400), duplicate id (409), id derived from the title.
- `DELETE /api/diagrams/{id}` of an unknown diagram (404).
- `GET /versions` of an unknown diagram (404) and the list order; `GET /versions/{n}` with a focus
  that the version does not contain (falls back to the whole diagram).
- `POST /versions/{n}/restore` with an unknown version (404); `POST /undo` with nothing to undo (409).
- `POST /ops`: unknown operation (400), wrong argument types (400 via the signature check, #66), unknown diagram
  (404), the focus parameter, a rejected operation creates no version.
- Every endpoint that takes a diagram id answers 404 for an unknown one.
- Invalid request bodies (missing `op`, empty chat message, unknown chat `mode`, missing title) are 422;
  a manual snapshot honours the focus and has the default label.
- `POST /chat` with an empty message (422) and for an unknown diagram (404);
  `DELETE /chat` clears the transcript and keeps the versions.
- `POST /chat/compact` when the provider fails (502).
- `GET /` and every module under `/static/*.js` with a JavaScript content type (module scripts are
  refused by browsers otherwise); `main()` reads `ARCVIZ_HOST`/`ARCVIZ_PORT` (uvicorn patched).

## Testing

The tests above are the deliverable. They run with `make test`; nothing else changes in CI.

## Migration / compatibility

None. Contributors get Hypothesis with `make install` (`.[dev]`).

## Findings

The properties found two defects in existing code:

1. **Blank explicit node id.** `add_node(id=" ")` stored a node with the empty id `""` (the import rules
   reject such a diagram, so it could not be exported and imported again). Fixed here, as a one-line
   validation: a blank id is treated like an omitted one, as `""` already was
   (`docs/spec/operations.md`; example test `test_add_node_treats_a_blank_id_as_none`).
2. **`escape_text` is not injective.** It leaves `#` alone, so a literal entity code in a name
   (`#lt;b#gt;`) is decoded by Mermaid and shown as `<b>`. Not a security problem (the result is still
   text), but the name is not shown as typed. Fixing it changes every rendered label containing `#` and
   needs a browser check that Mermaid 11 decodes `#35;`; it was left for issue #102, which escapes `#`
   first (`#35;`, checked in Chromium by the e2e test). Since then the property covers every text, and
   `test_escape_text_shows_literal_entity_codes_literally` passes.

## Open questions

- **Line breaks in names and labels** (answered by #103, ADR-0160). The operations make names, titles,
  labels and guards one line (`model.single_line`), and `escape_text` applies the same normalisation, so no
  raw line break reaches the Mermaid source. The generators now draw control characters too (lone surrogates
  excepted), and `assert_one_line` checks every state they reach.

## Decisions

- Hypothesis as a dev-only dependency; derandomised profile in CI (ADR-0021, Proposed).
- A blank explicit node id counts as none (finding 1, fixed); the `#` escaping (finding 2) is a
  follow-up, pinned by a strict `xfail`.
- Properties check the Mermaid *source structure* in Python, not the rendered SVG (no browser in
  pytest); the e2e smoke test remains the check that Mermaid accepts the source.
