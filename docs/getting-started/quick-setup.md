# Quick setup

This guide takes you from nothing to a rendered diagram that you can edit by chat. Pick one of the
three ways to run Arcviz; the UI and the API are identical.

## What you need

- An API key for an LLM provider. Anthropic is the default; any OpenAI-compatible endpoint works
  too (see [Use a different model](#use-a-different-model)). Without a key the UI starts, shows the
  demo diagram and lets you use versions, but the chat answers with an error.
- One of: Python 3.11 or newer, Docker, or Docker Compose.
- A current browser. Everything, including the Mermaid renderer, is served by Arcviz itself; no
  internet access is needed apart from the LLM API.

## 1. Run Arcviz

=== "Python (uv or pip)"

    ```bash
    git clone https://github.com/thomasniebler/arcviz.git
    cd arcviz

    # with uv (the SDK of your provider is an extra: [anthropic] or [openai])
    uv venv && uv pip install -e ".[anthropic]"
    # or with pip
    python -m venv .venv && .venv/bin/pip install -e ".[anthropic]"

    export ANTHROPIC_API_KEY=sk-ant-...
    .venv/bin/arcviz
    ```

    The server listens on <http://127.0.0.1:8000>. State is written to `./data`.

=== "Docker"

    ```bash
    docker run --rm -p 8000:8000 \
      -e ANTHROPIC_API_KEY=sk-ant-... \
      -v arcviz-data:/data \
      ghcr.io/thomasniebler/arcviz:latest
    ```

    The image binds to `0.0.0.0:8000` and stores state in the `/data` volume. Replace `latest` with
    a version tag to pin a release.

=== "Docker Compose"

    The repository ships a `docker-compose.yml`. Put the key into a `.env` file next to it:

    ```bash
    cat > .env <<'EOF'
    ANTHROPIC_API_KEY=sk-ant-...
    EOF
    docker compose up -d
    ```

    Compose builds the image locally if it is not available and keeps the data in a named volume
    `arcviz-data`.

## 2. Open the UI

Open <http://127.0.0.1:8000>. When the UI finds no diagram it creates the **demo diagram**: a deployment with
an API gateway, three services, a database, a queue and an external identity provider. The header
shows the active provider and model on the right.

!!! tip "Check the provider"
    `curl http://127.0.0.1:8000/api/config` returns `provider_ready` and, if the provider could
    not be created, `provider_error` with the reason (usually a missing key).

## 3. Make your first change

Type an instruction into the chat box and press ++enter++ (++shift+enter++ inserts a line break):

> Add a vnet called prod-vnet with CIDR 10.0.0.0/16 to the deployment and move service-a,
> service-b and service-c into it.

While the model works you see each tool call as a line in the chat, and the diagram updates after
every applied operation with the changed elements highlighted. When the turn is finished the model
replies with a short confirmation, and a new entry appears in the **Versions** list.

Try a few more:

- *Rename orders-db to Orders database and set engine=postgres 16.*
- *Connect service-c to orders-db with label sql.*
- *Remove the queue. What depends on it?* (the model asks back or explains before deleting)
- *Which services talk to the identity provider?* (read-only question, no change)

## 4. Undo, snapshot, restore

- **Undo** reverts to the version before the last change.
- **Version sichern** creates a named manual snapshot.
- Picking an entry in **Versionen** previews that state in the diagram; **Wiederherstellen** makes
  it the current state. Restores are recorded as new versions, so the history stays linear.

## Use a different model

Arcviz talks to Anthropic by default with the model `claude-opus-5-5`. Any OpenAI-compatible
endpoint works by switching the provider:

```bash
# Ollama on the same machine
ARCVIZ_PROVIDER=openai OPENAI_BASE_URL=http://localhost:11434/v1 ARCVIZ_MODEL=qwen2.5:14b .venv/bin/arcviz

# OpenAI
ARCVIZ_PROVIDER=openai OPENAI_API_KEY=sk-... ARCVIZ_MODEL=gpt-4o .venv/bin/arcviz
```

All variables are listed in the [configuration reference](../reference/configuration.md).

## Next steps

- Something does not work? See [Troubleshooting](troubleshooting.md).
- Want to run it for a team? The README has a section on deployment targets and on what to decide
  before exposing Arcviz publicly (authentication is off by default; enable it with `ARCVIZ_AUTH`, see
  the [configuration reference](../reference/configuration.md#authentication)).
