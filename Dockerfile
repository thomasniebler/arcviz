# syntax=docker/dockerfile:1.7.1@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

# Reproducible builds (ADR-0109, ADR-0110): every image is pinned by version and digest, every Python package comes
# from uv.lock. Update the pins with the steps in docs/spec/build-and-release.md (*Updating dependencies*).
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# Stage 1: the documentation site the app serves under /docs (ADR-0042, ADR-0116), from the `docs` dependency group.
FROM python:3.14.7-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS docs
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/docs
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --only-group docs --no-install-project --no-cache
ENV PATH=/opt/docs/bin:$PATH
COPY zensical.toml CHANGELOG.md CONTRIBUTING.md ./
COPY docs ./docs
COPY scripts/build_docs_bundle.py ./scripts/
RUN python scripts/build_docs_bundle.py

FROM python:3.14.7-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# git is only needed for the optional Git mirror (ARCVIZ_GIT_DIR); small enough to always include. Debian packages
# come from the current archive (security fixes), the one input that is not pinned (ADR-0110).
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app
# Every runtime extra (ADR-0108): both LLM SDKs, Entra ID for Azure OpenAI, the Cosmos DB store and the telemetry export,
# so one image runs every configuration. Build with --build-arg ARCVIZ_EXTRAS=anthropic for a slimmer image.
ARG ARCVIZ_EXTRAS="anthropic,openai,azure-openai,cosmos,telemetry,mcp,docx"
COPY pyproject.toml uv.lock README.md LICENSE hatch_build.py ./
# The locked dependencies first (a layer that survives code changes), then the project itself (not editable).
RUN uv sync --locked --no-dev --no-install-project --no-cache $(printf -- '--extra %s ' $(echo "$ARCVIZ_EXTRAS" | tr ',' ' '))
COPY arcviz ./arcviz
COPY --from=docs /src/arcviz/docs_site ./arcviz/docs_site
RUN uv sync --locked --no-dev --no-editable --no-cache $(printf -- '--extra %s ' $(echo "$ARCVIZ_EXTRAS" | tr ',' ' '))
ENV PATH=/opt/venv/bin:$PATH

# Runtime: unprivileged user, state on a volume
RUN useradd --system --uid 10001 --create-home arcviz \
    && mkdir -p /data && chown arcviz:arcviz /data
USER arcviz
VOLUME ["/data"]

ENV ARCVIZ_DATA_DIR=/data \
    ARCVIZ_HOST=0.0.0.0 \
    ARCVIZ_PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["arcviz"]
