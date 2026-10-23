# ADR-0110: Base images and GitHub Actions are pinned exactly; Dependabot updates them monthly

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#116)

## Context

The Dockerfile used `python:3.12-slim` and `ghcr.io/astral-sh/uv:0.8`, both moving tags. A digest pins the exact
image; a version next to it keeps the line readable. Update bots open pull requests, and every pull request into
`main` is a billed CI run (CLAUDE.md *CI minutes*).

## Options

1. **Tag plus digest** (`python:3.12.15-slim-trixie@sha256:…`), updated by hand with a documented command, or later
   by Dependabot/Renovate, which both understand the format.
2. **Exact tag only.** Readable, but a tag can be re-pushed.
3. **Pin, and enable Dependabot** for `uv`, `docker` and `github-actions`, monthly with one grouped pull request
   per ecosystem; GitHub Actions pinned by commit SHA with the version as a comment so Dependabot can bump them.
   About one to three pull requests and CI runs a month, plus security updates when an advisory appears.

## Decision

Option 3 (the owner's decision, 2026-10-08), with the budget stated in the pull request and in
`.github/dependabot.yml`: base images by version and digest, actions by commit SHA, monthly grouped updates; security
updates stay ungrouped (GitHub's default).

## Consequences

Image builds are reproducible up to the Debian packages installed with `apt-get` (security updates; the one
documented exception). uv is the same release in the image, in CI and locally. Someone has to run the update steps
(`make upgrade`, the digest lookup) only when Dependabot cannot: a new uv image also needs the same version in the
CI setup action (a test fails until it is set), and the Dockerfile's `# syntax=` frontend is updated by hand.
