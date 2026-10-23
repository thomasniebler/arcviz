# ADR-0200: Deployment lives in a separate repository

- Status: Accepted
- Date: 2026-10-23

## Context

The application repository used to contain a Pulumi project and a manual deploy workflow for one organisation's
Azure environment. They depend on private modules and name environment details, so they are of no use to other
readers and do not belong in a public repository. The release workflow already publishes the container image to
GHCR.

## Options

1. Keep infrastructure code next to the application.
2. Keep it in a separate, private repository that pulls the published image.
3. Describe only the manual CLI path in the documentation.

## Decision

Option 2, with option 3 as the public part. This repository ships the application, the image
`ghcr.io/thomasniebler/arcviz:<version>` and the generic guide [Deploy to Azure](../../deploy-azure.md). A
deployment repository pins an image tag or digest and carries its own secrets, state and workflows.

## Consequences

- No environment-specific names, identities or private dependencies in this repository.
- A deployment is an explicit bump of the image tag in the deployment repository; nothing is triggered from a release.
- ADR-0156 (Pulumi deployment) moved to the deployment repository.
