# 2. Constraints

## Technical constraints

| Constraint | Background |
|---|---|
| Python ≥ 3.11, FastAPI on uvicorn, Pydantic models | `pyproject.toml`; the image uses Python 3.14. CI tests 3.14 on PRs and 3.11 and 3.13 weekly ([ADR-0006](../adr/0006-ci-minutes-single-job-and-milestone-branches.md)). |
| No frontend build step: native ES modules, vanilla JS | [ADR-0020](../adr/0020-frontend-native-es-modules.md); the wheel and image ship `arcviz/static/` as is. |
| Mermaid 11 vendored and pinned (`arcviz/static/vendor/`), `securityLevel: "strict"`, `htmlLabels: true` | Offline operation, no CDN; label escaping relies on Mermaid entity codes ([Rendering](../../spec/rendering.md)). Renderer choice per diagram type: [ADR-0038](../adr/0038-renderer-per-diagram-type.md). |
| The Docker image contains only `arcviz/`, `pyproject.toml`, `README.md`, `LICENSE` (plus the built docs site) | Every runtime file lives under `arcviz/` ([ADR-0042](../adr/0042-docs-site-built-into-the-package.md)). |
| No new runtime dependency without a stated reason; optional ones are extras (`anthropic`, `openai`, `cosmos`, …) and are imported lazily | CLAUDE.md rule 5; the configured provider's SDK is checked at start-up ([ADR-0107](../adr/0107-llm-sdks-as-extras-checked-at-start-up.md)). |
| LLM access through tool calling only | Anthropic Messages API or an OpenAI-compatible chat-completions endpoint ([LLM layer](../../spec/llm-layer.md)). |
| Tests run without network access and without API keys | `ScriptedProvider` replays model replies. The eval set against a real model runs outside CI ([ADR-0016](../adr/0016-eval-set-outside-ci.md)). |

## Organisational constraints

| Constraint | Background |
|---|---|
| All mutations go through `arcviz/operations.py` | CLAUDE.md rule 2; the basis of quality goal 1. |
| The as-implemented spec is updated in the same PR as the code | CLAUDE.md rule 1. Design specs and ADRs come before code (`/implement` workflow). |
| One branch and one squash-merged PR per milestone, Conventional Commits, release-please | CLAUDE.md rule 6, [Build, CI and release](../../spec/build-and-release.md). |
| GitHub Actions minutes are billed per started minute: one CI job, path-scoped steps, no CI on milestone branches | [ADR-0006](../adr/0006-ci-minutes-single-job-and-milestone-branches.md). |
| Documentation in English, UI in German | CLAUDE.md. |

## Conventions

- Architecture decisions as ADRs (append-only; [ADR-0001](../adr/0001-record-architecture-decisions.md)).
- Ruff (`E F I B UP SIM W`, line length 120) and mypy (`check_untyped_defs`) on every PR.
- Module dependencies follow the table in [Architecture](../index.md#modules-and-allowed-dependencies).
  The frontend layering is enforced by `tests/js/modules.test.mjs`, and the server component view
  (section 5) by `tests/test_architecture_docs.py`.
