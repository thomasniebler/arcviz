# 10. Quality requirements

## Quality tree

| Quality goal (section 1) | Refined by scenarios |
|---|---|
| Model integrity | Q1, Q2, Q3 |
| Recoverability | Q4, Q5, Q6 |
| Live feedback | Q7, Q8 |
| Self-containment and portability | Q9, Q10 |
| Changeability | Q11, Q12, Q13 |
| Operability and security (not a goal yet, but needed for shared deployments) | Q14, Q15, Q16, Q17 |

## Quality scenarios

Status: ✅ met (with evidence), ◐ partly met, ❌ not met. The evidence column names the test or the
issue. A PR that changes a status updates this table (ADR-0041).

| # | Scenario (stimulus → expected response) | Status | Evidence / gap |
|---|---|---|---|
| Q1 | The LLM calls a tool with an unknown id, a wrong kind or a containment cycle → the diagram is unchanged, and the model gets an error naming valid ids and kinds and can retry in the same turn. | ✅ | `tests/test_operations.py`, `tests/test_agent.py`, property tests in `tests/test_properties.py` |
| Q2 | A tool or `/ops` call has arguments of the wrong *type* (`kind: 42`, `name: 42`) → controlled 4xx or tool error, and no partial change is persisted. | ✅ | `tests/test_operation_arguments.py`, ill-typed arguments in the property tests (#66, [ADR-0045](../adr/0045-argument-validation-from-operation-signatures.md)) |
| Q3 | A corrupt or crafted document is imported (cycles, dangling edges, unknown kinds) → rejected with every problem listed; nothing stored. | ✅ | `tests/test_exchange.py`; corrupt stored data with a containment cycle terminates every traversal (`tests/test_cycles.py`, #68). |
| Q4 | A user undoes a bad LLM turn → the state before the turn, in one step, also after a restart. | ✅ | `tests/test_store.py` (contract over all backends), `undo_target` and `redo_target` specs |
| Q5 | The browser tab is closed mid-turn → applied operations are kept *and* versioned, so the turn can be undone. | ● | `latest` is kept; the server closes the turn when the response ends, so the version and the transcript are written at once (#112, ADR-0126). |
| Q6 | Two tabs, or chat plus inspector, edit the same diagram at the same time → both changes are kept, or one gets an explicit conflict. | ✅ | Conditional writes on a revision on every backend (#65): `tests/test_concurrency.py`, contract tests in `tests/store_contract.py`. Clients: `ETag`/`If-Match` → 412 and the inspector's choice (#86): `tests/test_server_live.py`, e2e step 18. |
| Q7 | The model applies five operations in one turn → the diagram updates after each operation, and text streams as it is generated. | ✅ | SSE `tool_result` with Mermaid source and `text_delta`; e2e smoke test |
| Q8 | A diagram with 500 elements → the UI stays usable, and turn latency is dominated by the model, not by Arcviz. | ◐ | Rendering is fast enough (ADR-0038 measurements). Every response reads the version metadata once and carries one page of it (#114, ADR-0129). The prompt is not bounded for flat diagrams (#115). |
| Q9 | The app runs without internet access, with a local model → the UI, rendering and documentation all work. | ✅ | Vendored Mermaid, `/docs` built offline (`tests/test_docs_serving.py`), OpenAI-compatible provider |
| Q10 | Building the image twice from one commit → the same dependencies. | ✅ | `uv.lock` used by every install (`uv sync --locked`), base images pinned by digest; `tests/test_reproducible_builds.py` (#116). Debian packages from `apt-get` are the exception. |
| Q11 | Add an LLM provider → implement five hooks; no change to the agent. | ✅ | `LLMProvider` ([LLM layer](../../spec/llm-layer.md)), two providers today |
| Q12 | Add a storage backend → implement the primitives and pass the contract suite. | ✅ | `tests/store_contract.py`, three backends |
| Q13 | Add a diagram type → kinds, rules, tools and renderer per type, without touching the deployment type. | ◐ | Rules are registered per type (ADR-0022). Kinds, tools and renderer per type are M13 (#98). Review highlights depend on Mermaid's SVG ids (ADR-0025). |
| Q14 | An unauthenticated client reaches the app → it cannot read or change diagrams. | ✅ (when enabled) | `ARCVIZ_AUTH=token` or `proxy` (#82, `tests/test_auth.py`, token mode in the e2e test). Off by default for local use; a deployment has to enable it or put a proxy in front. |
| Q18 | A user of another team, or a viewer, reaches a project → the outsider cannot tell it exists (404), the viewer can read, export and review but change nothing (403), including through the chats and proposals. | ✅ (proxy mode) | One access policy in the services (M20, ADR-0084): `tests/test_access.py` (every service method and both agents per role), `tests/test_access_matrix.py` (every endpoint × role), two users in the e2e test. Depends on the proxy being the only way in (R19). |
| Q15 | A foreign web page in the user's browser → cannot change a local instance. | ✅ | JSON content type plus no CORS (`tests/test_cross_origin.py`) |
| Q16 | A client sends a 50 MB message or floods `/chat` → rejected, with no LLM cost. | ✅ | 413 for bodies, chat messages, imports and proposals over their limits (on by default); 429 per client and class once rate limits are configured (#85, `tests/test_limits.py`, [ADR-0048](../adr/0048-request-limits-and-in-process-rate-limiting.md)) |
| Q17 | The operator investigates a slow or failed turn → structured logs with token usage, and traces. | ◐ | JSON logs with request and trace ids and one `llm_turn` record per turn (`tests/test_observability.py`, no content: `tests/test_log_privacy.py`); request traces with `ARCVIZ_TELEMETRY` (#83). No spans inside a turn and no metrics yet. |
