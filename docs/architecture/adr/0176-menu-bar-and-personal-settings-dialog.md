# ADR-0176: A menu bar for diagram actions and a personal settings dialog behind the user menu

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/personal-settings.md`

## Context

The top bar was one row of about fifteen controls: project toggle, diagram actions, history, versions, chat reset, the
LLM provider line, the user name, the skin selector, logout and help. `topbar.js` pushed controls into a **⋯** menu when
they did not fit, so what was visible depended on the window width. Personal things (skin, agent access, logout) sat
between diagram tools, and **Agenten-Zugang…** lived in the *project* menu although a token belongs to the user.

## Options

1. **Keep the flat bar, only reorder and group with separators.** Small change, but the bar stays crowded and the
   overflow menu keeps moving controls around.
2. **A menu bar** (**Diagramm**, **Version**, **Hilfe**, user menu) built from the existing `<details class="menu">`
   pattern, **Undo/Redo** as the only frequent actions left outside, and a **modal dialog** for personal settings.
3. **A settings page** under its own route. More room, but it leaves the diagram and needs routing the app does not have.

## Decision

Option 2. The menus reuse the existing pattern (no new widget library), every control keeps its id, so listeners and
tests are unchanged. The width-dependent overflow logic is dropped: the bar is short enough to fit at every supported
width and wraps instead. Personal settings are a dialog like the other dialogs of the app (model, agent access), opened
from the user menu; the dialog owns the skin selector, the agent access button and the provider line, the user menu owns
**Abmelden**.

## Consequences

- Diagram actions need one more click (open the menu); **Undo/Redo** and the keyboard shortcuts stay direct.
- The e2e test opens menus through a helper before it uses a control that moved.
- **Agenten-Zugang…** leaves the project menu; a user who looks there finds it under the user menu.
- `topbar.js` shrinks to menu behaviour; `settings.js` is a new feature-layer module.
