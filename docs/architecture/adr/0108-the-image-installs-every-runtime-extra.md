# ADR-0108: The Docker image installs every runtime extra

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#84)

## Context

The published image is the deployment artifact for Azure Container Apps, and the owner's target uses Cosmos DB, an
LLM provider chosen per environment, managed identity and possibly Application Insights. The image so far installed
`.[cosmos]` and, through the core dependencies, both SDKs.

## Options

1. **Every runtime extra** (`anthropic`, `openai`, `azure-openai`, `cosmos`, `telemetry`). One image runs every
   configuration; about 60 MB more than the slimmest variant.
2. **Only the default provider.** Smallest, but switching provider means building an own image.
3. **One image per variant.** More images to build, publish and document, more CI minutes.

## Decision

Option 1, with a build argument `ARCVIZ_EXTRAS` (comma-separated) for anyone who wants a slimmer image.

## Consequences

The image keeps working for every configuration that worked before and gains Entra and telemetry. CI's smoke test
covers the full set. Image size grows by the OpenTelemetry and Azure Monitor packages.
