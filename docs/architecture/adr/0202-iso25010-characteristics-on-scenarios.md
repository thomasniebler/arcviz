# ADR-0202: ISO/IEC 25010 characteristics are stored on scenarios, suggested by the model and chosen by a person

- Status: Accepted
- Date: 2026-10-26
- Spec: [ISO 25010 quality characteristics](../specs/iso25010-qualities.md)

## Context

Drivers state what the architecture must achieve; the quality scenarios (ADR-0166) are the concrete stories that
describe a driver. Neither says which software quality the story is about, so a project cannot see which ISO/IEC 25010
characteristics its drivers cover. The text of a scenario (stimulus, response, response measure) carries that
information, so a model can propose it, but a model may also answer nonsense.

## Options

1. **Store on the driver.** Thin input (title and description), and a driver usually mixes several concerns.
2. **Store on the scenario, derive for the driver.** The scenario is the concrete story with the most text; the
   driver's characteristics are the union over its active scenarios and cannot drift.
3. **Store on both.** Two sources of truth that can disagree.
4. **Let the model write the field** (on save, or in agent tools without a person).

## Decision

Option 2: `Scenario.qualities` holds one or at most two of the nine characteristics of ISO/IEC 25010:2023 (closed set,
validated by the operations); `driver_qualities` computes a driver's from its scenarios. The model only suggests, on
demand (`POST …/scenarios/qualities`, validated, never stored); a person confirms in the editor, where all nine stay
selectable. Agents may set the field through the same operations they already use for scenarios (`create_scenario`,
proposal items), checked like every other argument.

## Consequences

- Old data is unchanged: the field is optional and omitted from dumps while empty. Bundles that use it are
  `schema_version` 8, which older versions refuse instead of dropping it.
- Invalid model output costs nothing: unknown names are dropped, and without a valid name the editor says so and the
  user picks.
- A driver whose scenarios span more than two characteristics shows all of them; the limit of two is per scenario.
- A new review rule `scenario-no-quality` (low) nudges towards a complete assignment without making the field required.
