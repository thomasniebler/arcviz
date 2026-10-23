# Onboarding: starters in the empty project and a guided tour

- Status: Implemented
- ADRs: [0174 The tour is opt-in and the landing is the empty project](../adr/0174-onboarding-opt-in-tour-and-starters.md)
- Builds on: [Frontend split into ES modules](frontend-modules.md), [Projects](projects.md),
  [Personal workspaces](personal-workspaces.md)

## Summary

A new user in proxy mode lands in an empty *Standardprojekt* and sees "Dieses Projekt enthält noch keine Diagramme"
and one button. Nothing says that the chat is the main tool, which diagram types exist or what the other controls do.
This change adds two things, both in the browser only:

1. **Starter cards** in the empty-project placeholder: one per diagram type (deployment, activity, sequence), each with
   **Mit Chat starten** (an empty diagram of that type and a fitting first message in the chat input) and **Beispiel
   ansehen** (the finished template of that type).
2. **A guided tour** (**Hilfe → Rundgang starten**, also offered in the empty project): six steps that highlight the
   navigator, chat, canvas, review, versions and help menu with a short text each.

## Goals

- A first-time user reaches a rendered diagram in one click and sees what to type.
- The tour is short, skippable (Esc, **Beenden**) and repeatable.
- No server change, no new endpoint, no stored state.

## Non-goals

- A modal that opens by itself, a "seen" flag per user, a landing page outside the app, interactive tutorials that
  wait for the user's actions.

## Design

`onboarding.js` (pure, tested): `STARTERS` (type, German title and blurb, template, title of a new diagram, first
message), `starterBody(starter, project, example)` (body of `POST /api/diagrams`), `availableStarters(config)` (types the
server offers), `TOUR_STEPS` (CSS selector, heading, text) and `moveStep`. `welcome.js` (features layer): fills
`#starter-cards` once `/api/config` is loaded, creates the diagram through the existing `POST /api/diagrams` and opens it
through `loadDiagramList`; the tour is a non-modal card `#tour` with the target marked by `.tour-target`. A step whose
target has no layout box (a control moved into the **⋯** menu) is skipped.

The placeholder keeps `#empty-text` and `#btn-empty-new`. `navigator.setEmpty` shows `#empty-starters` only when the
project is empty (not when the caller has no project) and the block is `edit-only`, so a viewer of an empty shared
project does not see it.

## Testing

`tests/js/onboarding.test.mjs` (starters, request body, availability, step movement); `modules.test.mjs` (layers).
E2E: the empty project of a new user offers the starters and **Mit Chat starten** creates the diagram and fills the
chat input.

## Migration / compatibility

None.

## Decisions

ADR-0174, accepted.
