# ADR-0112: Log records carry a keyed pseudonym and route templates, never raw ids, paths, messages or content

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#83)

## Context

The identity id comes from the authenticating proxy. With Easy Auth it is an Entra object id; with oauth2-proxy
`X-Forwarded-User` is often an e-mail address. Request paths contain diagram and project ids, which are slugs of
titles (content), and the revoke route contains a user id. Exception messages may contain any of these (an id from
the path, a name, an e-mail address). Spans of the FastAPI instrumentation carry the client address, the user agent
and the host. The requirement is a user reference in log records, never e-mail addresses or diagram content.

## Options

1. **The raw identity id.** Directly readable, but an e-mail address with oauth2-proxy.
2. **An unsalted hash** (the user directory key, `u-` + SHA-256). Stable, but a guessed id (an e-mail address, an
   object id from a directory) is confirmed by hashing it: reversible for anyone with candidates.
3. **A keyed HMAC** with a deployment secret (`ARCVIZ_LOG_PSEUDONYM_KEY`). Stable across replicas and restarts while
   the key stays; an operator holding the key can compute a user's pseudonym, nobody else can reverse it.
4. **No user at all.** Safe, but a support request ("my chat failed at 10:03") cannot be found.

## Decision

Option 3 for the `user` field; without the key the field is omitted (option 4), never the plain hash. Requests are
logged by route template, never by raw path or query string; turn records carry counts, never messages. Exceptions
in JSON and exported records carry the type, a stable error code and the frames (`file:line:function`), never the
message, unless `ARCVIZ_LOG_EXCEPTION_DETAILS` opts in. Spans keep an allow-list of attributes (method, route,
status, scheme, protocol, the redacted path), also against attributes set after the request hook; span events
and the span status keep only the exception type. Exported log records are scrubbed copies, so the local text
traceback stays. Warnings name an exception's type, never its message (a test checks every `except … as exc` block); URLs with
credentials (the Git remote) are redacted. An exception is logged once, scrubbed, by the request middleware and
never re-raised to uvicorn. The instrumentation records no metrics.

## Consequences

Correlating a person with records needs the key (an operator computes the pseudonym), which is the intended level
of protection; rotating the key breaks correlation across the rotation. Debugging a defect works from the type,
the frames and the error code; the opt-in brings messages back for a while. Existing free-text warnings (migration,
repair, user directory) keep naming ids; documented, not changed in this milestone. Removing span attributes and
scrubbing the status at the end use the SDK span's private fields (no public API does), covered by tests against
the pinned SDK.
