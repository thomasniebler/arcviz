# Troubleshooting

## The server does not start: "Invalid configuration"

Start-up checks every variable and lists every problem at once, each naming the variable, the expected format and
the value (see [Configuration](../reference/configuration.md)). Fix all of them and start again. Two messages concern
the installation rather than a variable:

| Message | Fix |
|---|---|
| `ARCVIZ_PROVIDER=anthropic needs the Anthropic SDK: pip install "arcviz-agent[anthropic]".` | The LLM SDKs are optional extras. Install the one of your provider (`[anthropic]` or `[openai]`); the Docker image has both. |
| `ARCVIZ_PROVIDER must be one of anthropic, openai, openai-compatible, ollama, got '...'.` | A typo in `ARCVIZ_PROVIDER`. |

## The chat answers "LLM provider not available"

The server could not create the provider. The most common causes:

| Symptom in `provider_error` | Fix |
|---|---|
| `The api_key client option must be set …` (Anthropic) or a missing-key message | Set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` for the OpenAI provider) in the environment of the server process. |
| Connection errors to an OpenAI-compatible endpoint | Check `OPENAI_BASE_URL` (include `/v1` for Ollama and vLLM) and that the server is reachable from where Arcviz runs, not only from your browser. |

`GET /api/config` shows `provider_ready` and `provider_error`. The provider is created lazily on
the first chat message, so the error only appears after the first attempt; `GET /readyz` checks
the same without sending a message and also reports a missing Anthropic key. A key that is present
but invalid passes this stage: the provider is created, and the rejected API call shows up as a red
error bubble in the chat (`AuthenticationError`, `PermissionDeniedError` or similar) instead.

## The diagram area stays empty or shows "Rendering fehlgeschlagen"

- Mermaid is served by Arcviz itself (`/static/vendor/mermaid.min.js`), so no external host is
  needed. If the panel stays empty, check the browser console for a failed script load, for
  example a proxy that strips large responses.
- A rendering error with a message usually means Mermaid rejected the generated source. Click
  **Quelltext** to see it, and open an issue with the source attached. Operations are validated
  before they reach the renderer, so this should not happen.

## Port 8000 is already in use

Set `ARCVIZ_PORT` (and `ARCVIZ_HOST` if the server should be reachable from other machines):

```bash
ARCVIZ_HOST=0.0.0.0 ARCVIZ_PORT=8080 .venv/bin/arcviz
```

## The model keeps asking instead of changing things

The system prompt asks the model to clarify when a request is ambiguous in a way that changes the
result. Name the elements by their ids (shown in square brackets in the summary the model sees and
in the tool lines of the chat), or state the container explicitly: *"into prod-deployment"*.

## The assistant declines a request

The chat is limited to modelling: the diagram, software architecture, UML, cloud and deployment topics and
Arcviz itself (see [What the assistant helps with](../guide/chat.md#what-the-assistant-helps-with)).
Anything else gets a one-sentence refusal and changes nothing. If an architecture question is declined by
mistake, rephrase it with its architectural context ("für unser Deployment: …").

## A chat turn was interrupted

If the browser is closed or the connection drops mid-turn, every operation that was already applied
is kept, a version is still written, and the transcript records the turn with an error note. Reload
the page to see the current state; use **Undo** if the partial change is not what you wanted.

## The microphone button is greyed out

The browser has no Web Speech API (Firefox, some embedded browsers) or Arcviz is opened over plain
HTTP on a non-local host, where browsers refuse microphone access. Use Chrome, Edge or Safari and
open Arcviz via `https://` or `http://localhost`. The tooltip on the button states which of the
two applies. See [Voice input](../guide/voice.md#browser-support).

## Where is my data?

Everything lives under `ARCVIZ_DATA_DIR` (default `./data`, `/data` in the container):

```
data/diagrams/<id>/latest.json          current state
data/diagrams/<id>/versions/NNNN.json   immutable snapshots
data/diagrams/<id>/chat.json            transcript
```

Back it up by copying the directory. Run exactly one Arcviz instance per data directory.

## Resetting the demo diagram

Delete the diagram in the UI (**Löschen**) or remove `data/diagrams/demo/`. Arcviz recreates the
demo diagram on the next page load if no diagram exists.
