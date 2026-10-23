# ADR-0109: `uv.lock` is the single lock for CI, the image, the docs and the release

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#116)

## Context

`pyproject.toml` has lower bounds only; CI, the Docker build and the release resolved the newest versions at build
time, and the docs toolchain (`requirements-docs.txt`) included a Git branch of `mike`. Two builds of one commit
could differ, and an upstream release could break `main` without a code change. The repository already uses uv
everywhere.

## Options

1. **`uv.lock`** (universal, hashes for every distribution, extras and dependency groups, Git sources locked to a
   commit), consumed with `uv sync --locked` (fails when stale) and exported with hashes for pip users.
2. **`pip-compile --generate-hashes` requirements files**, one per environment (runtime, dev, docs). Familiar, but
   one file per platform and set of extras, and a second tool next to uv.
3. **Constraints files without hashes.** Pins versions but not artefacts.

## Decision

Option 1. The docs toolchain moves into dependency groups (`docs`, `docs-publish`), which are not package metadata
and may use the Git source; `requirements-docs.txt` is removed.

## Consequences

Every Python install of the project is reproducible and checked for staleness. Updating is `make lock` /
`make upgrade`. The wheel keeps ranges (it is installed into other environments); the release attaches a hashed
requirements file with the tested set. Dependabot (`uv` ecosystem) and Renovate read `uv.lock`.
