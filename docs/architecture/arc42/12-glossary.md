# 12. Glossary

| Term | Meaning |
|---|---|
| Diagram | A model of nodes (with kinds and nesting) and edges, stored under an id; the unit of versioning and chat |
| Node, container, leaf | An element. Containers (kinds such as `deployment`, `vnet`, `cluster`) may have children and are drawn as subgraphs; leaves may not. |
| Kind, kind catalogue, kinds file | The type of a node or an edge. Built-in sets plus kinds added at startup from `ARCVIZ_KINDS_FILE`; the active set is the catalogue. |
| Operation | A named, validated mutation (`add_node`, `move_node`, `apply_batch`, …) in `operations.py`; the only way to change a diagram |
| Tool, tool call | An operation offered to the LLM as a JSON-schema function. A tool call is the model's request to run one. |
| Turn | One chat message and everything the agent does for it: rounds of model calls and tool calls, then finalization |
| Round | One model request inside a turn (`ARCVIZ_MAX_ROUNDS`) |
| Finalization | End of a turn: auto version if the content changed, transcript entry, `final` event |
| `latest` | The current state of a diagram, rewritten after every change |
| Version, snapshot | An immutable copy of a diagram with metadata (`source`, `fingerprint`, label) |
| Fingerprint | A hash of a diagram's content, used to detect "no change" and stale proposals |
| Undo/redo target | The version that undo or redo would restore, computed from the version sources |
| Proposal, propose mode | A dry-run turn whose operations are shown as a diff and applied only on confirmation |
| Compaction, summary entry | Folding older transcript entries into a cumulative LLM-written summary |
| Finding | A result of the architecture review (rule check or LLM): category, severity, message, element and edge ids, optional suggestion |
| Rule check | A deterministic function registered per diagram type that returns findings |
| Focus | Showing one container's subtree (plus stubs for crossing edges) |
| Comparison | A merged render of two versions with added, removed and changed marks |
| Git mirror | An optional Git repository that receives one commit per version; never read back |
| Provider | An implementation of `LLMProvider` for one API (Anthropic, OpenAI-compatible) |
| `ScriptedProvider` | The test provider that replays fixed model replies |
| Docs bundle | The offline documentation site built into `arcviz/docs_site/` and served under `/docs` |
| C4 | Context, Containers, Components, Code: the diagram levels used in sections 3 and 5 |
| arc42 | The template of this documentation (twelve sections) |
