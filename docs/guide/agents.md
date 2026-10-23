# Coding agents (MCP)

Arcviz can be queried by agents, for example a coding agent that works on the code of the system you draw: *Which
components exist, what is each responsible for, what does it talk to, which files implement it?* The agent connects to
the **MCP endpoint** of the server (`/mcp`, [Model Context Protocol](https://modelcontextprotocol.io), Streamable HTTP).

!!! note "Off by default"
    The operator switches the endpoint on with `ARCVIZ_MCP=1` ([configuration](../reference/configuration.md#agent-access-mcp)).
    **Agenten-Zugang…** in the personal settings (user menu at the top right) says whether it is on and shows the address.

## What an agent can do

| Tool | Purpose |
|---|---|
| `list_projects`, `list_components`, `get_component` | Components with their **responsibilities** (the element description), parent and parts, relations in both directions (protocol, port, description), the diagram views that show them, their code links and the findings that name them |
| `find_component_by_path` | Which component owns a file or directory: the most specific code link wins |
| `get_architecture_context` | Architecture drivers (most important first), core scenarios, **accepted decisions** (they bind), stakeholders |
| `list_findings` | The deterministic architecture checks of the project (no model call) |
| `list_diagrams`, `get_diagram`, `list_operations` | What exists and which operations a proposal may contain |
| `add_code_ref`, `remove_code_ref` | Link a component to the code that implements it ([Code links](shared-model.md#code-links)) |
| `propose_change`, `list_proposals`, `get_proposal` | Propose operations on **one** diagram and see whether people applied them |
| `list_stakeholders`, `list_drivers`, `get_driver`, `list_scenarios`, `get_scenario`, `list_decisions`, `get_decision` | Read the stakeholders, drivers, quality scenarios and decisions (ADRs) with their links |
| `list_requirements`, `get_requirement`, `get_traceability` | Read the requirements (paged) and which drivers, scenarios and decisions cover them |
| `add_requirements`, `update_requirement`, `suggest_refinement`, `add_stakeholder`, `update_stakeholder` | Write the input side directly: add requirements as they were said, suggest how to normalise them, add stakeholders |
| `propose_architecture_items` | Propose drivers, scenarios and decisions as a proposal that people apply in part |

An agent **never changes a diagram itself**. `propose_change` checks the operations on a copy and stores them with their
diff; they appear under **Vorschläge** in the project navigator, where you apply (one new version) or discard them. A
proposal whose diagram changed meanwhile is marked *veraltet* and can only be discarded; while one is being applied it shows
*wird übernommen* and cannot be discarded. Code links are the one direct
write: small, reversible, and without effect on the diagrams.

Requirements and stakeholders are the one thing an agent writes itself, and even there it only **adds and edits**: a refinement
it suggests (`suggest_refinement`) has no effect until you accept it, and it can neither delete nor decide. Drivers, scenarios and
decisions come as a proposal of several items (**Vorschläge** shows each item with a checkbox): tick what you want, **Ausgewählte
übernehmen**, or take everything; an item that needs another one (a scenario for a new driver) pulls it in. A decision an agent
proposes is always a draft; you accept it in the **Architektur** page.

An agent sees what its user sees: another person's project does not exist for it, and a viewer's agent cannot write.
Names and descriptions come from people; agents are told to treat them as data, not as instructions.

## Connect with a personal access token

1. Open **Agenten-Zugang…** in the personal settings (user menu at the top right), give the token a name, choose **nur lesen** or **lesen und schreiben**
   (code links and proposals) and a lifetime (30 to 365 days), and press **Token anlegen**.
2. Copy what the dialog shows **now**; the token is stored only as a hash and is never shown again. The dialog also
   shows the matching command for Claude Code:

    ```bash
    claude mcp add --transport http arcviz https://<your-arcviz>/mcp \
      --header "Authorization: Bearer avp_…"
    ```

    and the same as `.mcp.json` for other clients.
3. Revoke a token in the same dialog; agents that use it lose access at once. At most 20 tokens per person.

A token acts as you, with the scope you gave it, **only on `/mcp`**: it does not open the web API, so it cannot apply its
own proposals or change sharing. On a server with a single shared access token (`ARCVIZ_AUTH=token`) every user is the
administrator, so every token acts as the administrator there.

## Connect with OAuth (Azure Easy Auth / Entra ID)

Behind a login proxy an agent cannot use the browser sign-in. If the operator configured an OpenID Connect issuer
(`ARCVIZ_MCP_OAUTH_ISSUER`, [Deploy to Azure](../deploy-azure.md#agents-and-mcp-behind-easy-auth)), Arcviz answers an
unauthenticated request with `401` and a pointer to its
[protected resource metadata](https://www.rfc-editor.org/rfc/rfc9728) (`/.well-known/oauth-protected-resource`). A client
with OAuth support follows it, signs you in at the identity provider and sends the access token; Arcviz validates the
token (issuer, audience, signature, expiry) and treats you as the same user as in the browser, with your projects and
shares. Microsoft Entra ID does not offer dynamic client registration, so the client needs the application (client) id
that the operator registered, for example:

```bash
claude mcp add --transport http arcviz https://<your-arcviz>/mcp --client-id <application id>
```

An access token for the same audience that you copied from the signed-in page also works as a bearer token. A personal
access token works everywhere and needs no setup at the identity provider.
