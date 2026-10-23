// Shared UI state. Mutated by the DOM modules; read by everything that builds requests.

export const state = {
  diagramId: null, renderSeq: 0, previewVersion: null, busy: false, fitPending: true,
  compare: null,        // {base, target} while a version comparison is shown (target null = current state)
  focus: null,          // node id the canvas is focused on (null = whole diagram)
  view: null,           // shown rendering {renderer, source} (types.js viewOf)
  type: "deployment",   // type of the shown diagram
  interaction: null,    // the shown sequence diagram's interaction (lifelines, fragments, executions)
  complete: [],         // relation kinds the shown diagram declares complete (spec: model-relations.md)
  hiddenRelations: [],  // relation ids the shown diagram intentionally leaves out
  nodes: {},            // current diagram nodes by id (for the view selector and breadcrumb)
  mermaidIds: {},       // node id -> Mermaid id, from the server; reversed for click handling
  config: { node_kinds: [], container_kinds: [], edge_kinds: [] },
  edges: [],            // current diagram edges
  versions: [],         // version list of the current diagram (oldest first): the newest page plus loaded older ones
  versionsTotal: 0,     // how many versions the diagram has (#114)
  inspecting: null,     // node id shown in the inspector, or null
  proposal: null,       // pending proposal of a propose-mode turn (locks the UI until confirmed or discarded)
  undoTarget: null, redoTarget: null, // from the last diagram detail; null disables Undo/Redo
  projectId: null,      // the open project (set by the navigator)
  chatScope: "diagram", // whose transcript the chat pane shows: "diagram", "project" (project-chat.md) or "requirements"
  mainView: "diagram",  // what fills the main area: the "diagram" stage or the "requirements" view (spec: requirements-workshop.md)
  me: null,             // the caller from GET /api/me (spec: personal-workspaces.md)
  role: null,           // the caller's role on the open project: "owner" | "editor" | "viewer" (null: unknown)
  revision: null,       // the shown diagram's latest revision (the ETag; sent back as If-Match, spec: collaboration.md)
  lastChange: null,     // {revision, author_name} of the last change another session made (live stream)
  liveSession: null,    // this tab's live session id (presence)
};

// Pan & zoom: the SVG lives in #viewport, which is translated/scaled as a whole.
export const view = { scale: 1, x: 0, y: 0, fitted: false }; // fitted: still showing the fit-to-view transform

/** Query string carrying the current focus, appended to every request that returns a render. */
export function focusQuery(prefix = "?") { return state.focus ? `${prefix}focus=${encodeURIComponent(state.focus)}` : ""; }
