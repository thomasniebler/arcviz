# ADR-0195: Remove the Serviette and Codex skins

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/app-shell.md`

## Context

[ADR-0049](0049-ui-skins-as-token-overrides-diagram-excluded.md) introduced five skins. Serviette and Codex each
carry about 30 selector-specific rules, which a shell redesign would have to port to every new component. The
maintainer does not want them.

## Options

1. **Keep and port all five.** Largest cost, for skins nobody wants.
2. **Remove Serviette and Codex, keep Klassisch, Mission Control, Synthwave.**
3. **Remove all but Klassisch.** Contradicts the maintainer's wish to keep the neon skins.

## Decision

Option 2. A stored `napkin` or `codex` resolves to Klassisch through `resolveSkin`; ADR-0049's mechanism is
unchanged.

## Consequences

`ui-skins.md` lists three skins; the CSS shrinks by about 60 rules. Anyone who had picked a removed skin sees
Klassisch after the update.
