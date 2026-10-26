// The live arc42 page (spec: docs/architecture/specs/arc42-export.md): starts the vendored Mermaid on the figures. The page
// holds no other script; Mermaid runs in strict mode like in the app.
if (window.mermaid) {
  window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", htmlLabels: true });
  window.mermaid.run({ querySelector: "pre.mermaid" });
}
