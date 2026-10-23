# Deploying to Azure Container Apps

This guide runs Arcviz agent as a single Azure Container Apps (ACA) container with

- the published image from GHCR (`ghcr.io/thomasniebler/arcviz`),
- an **Azure Files** share mounted at `/data` so diagrams, versions and transcripts survive restarts,
- the LLM API key stored as a Container Apps secret (optionally backed by Key Vault),
- exactly **one replica** (the JSON store is single-writer),
- **Entra ID login** in front of the app via the built-in authentication ("Easy Auth"), which passes each user's
  identity to the app (every user has their own projects and shares them, the login itself is Easy Auth's).


For a reproducible environment, describe the same setup as code (Bicep, Terraform or Pulumi) in a separate repository that
pulls the published image `ghcr.io/thomasniebler/arcviz:<version>`; this repository ships only the image and this guide
([ADR-0200](architecture/adr/0200-deployment-lives-in-a-separate-repository.md)).

Everything below uses the Azure CLI (`az`, 2.60 or newer) with the `containerapp` extension. The Portal works
too, but the CLI keeps the steps reproducible.

> Commands were written against the ACA CLI surface as of late 2025. Flag names occasionally change; if a
> flag is rejected, `az containerapp <command> --help` is authoritative.

## 0. Variables

Adjust the names once and paste the rest as-is.

```bash
export RG=arcviz-rg
export LOC=germanywestcentral          # any region that offers Container Apps
export ENV=arcviz-env
export APP=arcviz
export STORAGE=arcvizdata$RANDOM        # storage account names must be globally unique, 3-24 lowercase chars
export SHARE=arcviz-data
export IMAGE=ghcr.io/thomasniebler/arcviz:latest   # pin to a version tag for production

az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
az group create --name $RG --location $LOC
```

## 1. Container Apps environment

```bash
az containerapp env create --name $ENV --resource-group $RG --location $LOC
```

This creates a Consumption environment with a Log Analytics workspace. That is enough: the app needs well under
1 vCPU / 2 GiB.

## 2. Persistent storage

Two options. **Azure Files** (this section) keeps the default `file` store and needs exactly one replica.
**Cosmos DB** (section 2b) needs no volume and allows several replicas; it is the better fit for anything
beyond a personal instance. Pick one; the rest of the guide notes where they differ.

### 2a. Azure Files (default `file` store)

```bash
az storage account create --name $STORAGE --resource-group $RG --location $LOC \
  --sku Standard_LRS --kind StorageV2 --allow-blob-public-access false

az storage share-rm create --storage-account $STORAGE --resource-group $RG --name $SHARE --quota 5

STORAGE_KEY=$(az storage account keys list --account-name $STORAGE --resource-group $RG --query '[0].value' -o tsv)

az containerapp env storage set --name $ENV --resource-group $RG \
  --storage-name arcviz-data \
  --azure-file-account-name $STORAGE \
  --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name $SHARE \
  --access-mode ReadWrite
```

The share is now known to the environment under the name `arcviz-data`; step 4 mounts it into the container.

Why Azure Files and not Blob storage: the store relies on POSIX semantics (`os.replace` for atomic writes,
`O_EXCL` to reserve version numbers). SMB file shares provide both; blob/FUSE mounts do not.

### 2b. Cosmos DB (`ARCVIZ_STORE=cosmos`)

A serverless Cosmos DB account (NoSQL API) holds diagrams, versions and transcripts; see
[Persistence](architecture/specs/persistence.md) for the data model. The app authenticates with its
managed identity, so no key is stored anywhere.

```bash
export COSMOS=arcviz-cosmos-$RANDOM      # globally unique, lowercase

az cosmosdb create --name $COSMOS --resource-group $RG \
  --locations regionName=$LOC --capabilities EnableServerless --default-consistency-level Session

# Data-plane RBAC cannot create databases or containers, so create them up front
az cosmosdb sql database create --account-name $COSMOS --resource-group $RG --name arcviz
az cosmosdb sql container create --account-name $COSMOS --resource-group $RG \
  --database-name arcviz --name diagrams --partition-key-path /diagram_id

COSMOS_URL=$(az cosmosdb show --name $COSMOS --resource-group $RG --query documentEndpoint -o tsv)
```

Skip the Azure Files share and the volume mount (step 4) in this variant. After the app exists (step 3),
give its identity the *Cosmos DB Built-in Data Contributor* role and point it at the account:

```bash
az containerapp identity assign --name $APP --resource-group $RG --system-assigned
PRINCIPAL=$(az containerapp identity show --name $APP --resource-group $RG --query principalId -o tsv)
az cosmosdb sql role assignment create --account-name $COSMOS --resource-group $RG \
  --role-definition-id 00000000-0000-0000-0000-000000000002 --principal-id $PRINCIPAL --scope "/"

az containerapp update --name $APP --resource-group $RG \
  --set-env-vars ARCVIZ_STORE=cosmos ARCVIZ_STORE_URL=$COSMOS_URL \
  --min-replicas 1 --max-replicas 3
```

The image needs the `cosmos` extra; the published image includes it from version 0.1.2 on. Several
replicas are safe with this store (version numbers are allocated atomically in Cosmos DB); the
`ARCVIZ_DATA_DIR` volume is unused. To move an existing file-based `/data` into the account, run
`arcviz-migrate --from file:/data --to cosmos:$COSMOS_URL` once from a shell in the container
(`az containerapp exec`), with `ARCVIZ_STORE_KEY` or the managed identity available.

Presence (who views a diagram, what they selected, who chats) is shared across the replicas through short-lived
items in the same container (one per project and replica, refreshed every few seconds). The app turns the
container's time-to-live on at start-up when it may (an account key, or control-plane rights); with managed identity
and data-plane roles only it cannot, and logs that. Then enable it once (*Settings → Time to Live → On (no
default)*, `az cosmosdb sql container update … --ttl -1`, or `defaultTtl: -1` in Bicep/ARM, `default_ttl = -1` in
Terraform) so Cosmos DB removes the items of replicas that stopped. Without it the app cleans them up itself, a
little later.

## 3. Create the app

Create the app with the image, secret, ingress and replica settings. The volume is attached in the next step
because the CLI only exposes volume mounts through YAML.

```bash
az containerapp create --name $APP --resource-group $RG --environment $ENV \
  --image $IMAGE \
  --target-port 8000 --ingress external \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.5 --memory 1.0Gi \
  --secrets anthropic-api-key="sk-ant-..." \
  --env-vars \
    ARCVIZ_PROVIDER=anthropic \
    ARCVIZ_DATA_DIR=/data \
    ANTHROPIC_API_KEY=secretref:anthropic-api-key
```

Notes:

- `--min-replicas 1` keeps one instance warm so the first request after a quiet period does not wait for a
  cold start, and `--max-replicas 1` enforces the single-writer rule of the `file` store. **Do not raise
  `max-replicas`** while the file store is in use; with the Cosmos DB store (2b) you can.
- For an OpenAI-compatible provider replace the secret and env vars with
  `OPENAI_API_KEY=secretref:openai-api-key`, `ARCVIZ_PROVIDER=openai`, `OPENAI_BASE_URL=…`, `ARCVIZ_MODEL=…`.
- The image is public on GHCR, so no registry credentials are needed. If you mirror it into Azure Container
  Registry, add `--registry-server <acr>.azurecr.io --registry-identity system`.
- ACA ignores the `HEALTHCHECK` in the Dockerfile. The YAML in step 4 adds a liveness probe on
  `GET /healthz` and a readiness probe on `GET /readyz` (store reachable, provider configured; no model
  call). Probes go to the container directly, not through the ingress, so the login of step 6 does not
  block them.

### Keyless Azure OpenAI (Entra ID)

With Azure OpenAI the app needs no key at all: it authenticates with its managed identity
([ADR-0115](architecture/adr/0115-entra-id-tokens-for-azure-openai.md)). The image includes the `azure-openai` extra.

```bash
export AOAI=<your Azure OpenAI account>      # an existing account with a model deployment
export AOAI_DEPLOYMENT=<deployment name>       # used as the model id

az containerapp identity assign --name $APP --resource-group $RG --system-assigned
PRINCIPAL=$(az containerapp identity show --name $APP --resource-group $RG --query principalId -o tsv)
AOAI_ID=$(az cognitiveservices account show --name $AOAI --resource-group $RG --query id -o tsv)
az role assignment create --assignee $PRINCIPAL --role "Cognitive Services OpenAI User" --scope $AOAI_ID

az containerapp update --name $APP --resource-group $RG \
  --set-env-vars ARCVIZ_PROVIDER=openai ARCVIZ_OPENAI_AUTH=entra ARCVIZ_MODEL=$AOAI_DEPLOYMENT \
    OPENAI_BASE_URL=https://$AOAI.openai.azure.com/openai/v1/ \
  --remove-env-vars OPENAI_API_KEY ANTHROPIC_API_KEY
```

The role assignment can take a few minutes to apply; until then chat turns fail with `PermissionDeniedError`. With a
user-assigned identity, also set `AZURE_CLIENT_ID` to its client id. Once a chat works, delete the key secret
(`az containerapp secret remove … --secret-names openai-api-key`) and switch off key authentication on the account,
so that no key can be used any more:

```bash
az resource update --ids $AOAI_ID --set properties.disableLocalAuth=true
```

The token is fetched on the first model call and refreshed five minutes before it expires; a failed refresh shows
up as an error in the chat (and in the logs, without the token). `/readyz` does not fetch a token.

### Optional: keep the key in Key Vault

If the key must live in Key Vault (audit, rotation), reference it instead of pasting the value:

```bash
az containerapp identity assign --name $APP --resource-group $RG --system-assigned
PRINCIPAL=$(az containerapp identity show --name $APP --resource-group $RG --query principalId -o tsv)

az keyvault create --name arcviz-kv-$RANDOM --resource-group $RG --location $LOC --enable-rbac-authorization true
KV=$(az keyvault list --resource-group $RG --query '[0].name' -o tsv)
KV_ID=$(az keyvault show --name $KV --query id -o tsv)

# The app identity may read secrets; your own account needs write access to create them.
# With RBAC enabled on the vault, a resource-group Owner/Contributor role is *not* enough for the data plane.
az role assignment create --assignee $PRINCIPAL --role "Key Vault Secrets User" --scope $KV_ID
az role assignment create --assignee $(az ad signed-in-user show --query id -o tsv) \
  --role "Key Vault Secrets Officer" --scope $KV_ID
# Role assignments take a minute or two to propagate; retry the next command on "Forbidden".

az keyvault secret set --vault-name $KV --name anthropic-api-key --value "sk-ant-..."

# Versionless URI (no /<version> suffix) so rotations are picked up without touching the app
SECRET_URI="https://$KV.vault.azure.net/secrets/anthropic-api-key"

az containerapp secret set --name $APP --resource-group $RG \
  --secrets "anthropic-api-key=keyvaultref:$SECRET_URI,identityref:system"
```

Rotating the key is then just `az keyvault secret set …` with the new value. Because the reference is
versionless, Container Apps refreshes it automatically (it polls roughly every 30 minutes); to apply it
immediately, create a new revision with `az containerapp update`. A versioned URI (as returned by
`az keyvault secret show --query id`) would stay pinned to the old value.

## 4. Mount the share at `/data`

Export the current app definition, add the volume and the mount, apply it:

```bash
az containerapp show --name $APP --resource-group $RG -o yaml > app.yaml
```

Edit `app.yaml` and change the `template:` section so it contains the volume and the mount. Everything else in
the file stays as exported. The relevant part should look like this:

```yaml
properties:
  template:
    containers:
      - name: arcviz
        image: ghcr.io/thomasniebler/arcviz:latest
        env:
          - name: ARCVIZ_PROVIDER
            value: anthropic
          - name: ARCVIZ_DATA_DIR
            value: /data
          - name: ANTHROPIC_API_KEY
            secretRef: anthropic-api-key
        resources:
          cpu: 0.5
          memory: 1Gi
        volumeMounts:
          - volumeName: data
            mountPath: /data
        probes:
          - type: Liveness  # process serves requests; no dependencies
            httpGet:
              path: /healthz
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          - type: Readiness  # store reachable and LLM provider configured
            httpGet:
              path: /readyz
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
            failureThreshold: 3
    scale:
      minReplicas: 1
      maxReplicas: 1
    volumes:
      - name: data
        storageType: AzureFile
        storageName: arcviz-data
        mountOptions: "uid=10001,gid=10001,dir_mode=0770,file_mode=0660,nobrl"
```

```bash
az containerapp update --name $APP --resource-group $RG --yaml app.yaml
```

About the mount options: the container runs as the unprivileged user `arcviz` (uid/gid 10001, see the
Dockerfile). SMB shares have no POSIX ownership of their own, so the mount decides who the files belong to;
without `uid=10001,gid=10001` the mount may be owned by root and the app cannot write. `nobrl` disables
byte-range locks, which SMB does not need here and which otherwise can make `os.replace` fail on some kernels.
Check the first start:

```bash
az containerapp logs show --name $APP --resource-group $RG --follow
```

You should see uvicorn start and, on first run, the demo diagram being created under `/data/diagrams/…`.

## 5. Open it

```bash
FQDN=$(az containerapp show --name $APP --resource-group $RG --query properties.configuration.ingress.fqdn -o tsv)
echo https://$FQDN
curl -s https://$FQDN/api/config
curl -s https://$FQDN/readyz
```

`/api/config` tells you which provider and model are configured; `/readyz` whether the share is writable and
the provider has credentials. With the readiness probe from step 4, an instance whose `/readyz` fails gets no
traffic, so if the URL does not answer, check the probe result in the revision's system logs
(`az containerapp logs show --name $APP --resource-group $RG --type system`) or call `/readyz` from the
console. `/readyz` cannot tell whether the key is *accepted*; to verify that, open the URL in a
browser and send a chat message on the demo diagram. If that fails, `GET /api/config` now carries the reason
in `provider_error` (for example a missing or rejected API key).

The URL is public at this point; do step 6 before sharing it.

Server-sent events: ACA ingress keeps HTTP connections open for up to 240 seconds, well above a chat turn
(usually 10–60 s), so streaming works without further settings.

## 6. Put a login in front (Entra ID Easy Auth)

Easy Auth terminates authentication at the ingress; unauthenticated requests are redirected to the Microsoft
login page and never reach the app. No code change is needed.

```bash
TENANT=$(az account show --query tenantId -o tsv)

# App registration for the login
CLIENT_ID=$(az ad app create --display-name "Arcviz agent" \
  --sign-in-audience AzureADMyOrg \
  --web-redirect-uris "https://$FQDN/.auth/login/aad/callback" \
  --enable-id-token-issuance true \
  --query appId -o tsv)
CLIENT_SECRET=$(az ad app credential reset --id $CLIENT_ID --display-name easyauth --years 1 --query password -o tsv)

# A registration alone cannot sign anyone in: create its service principal (the "Enterprise Application")
az ad sp create --id $CLIENT_ID

# Store the client secret in the app and configure the provider
az containerapp secret set --name $APP --resource-group $RG --secrets "aad-client-secret=$CLIENT_SECRET"
az containerapp auth microsoft update --name $APP --resource-group $RG \
  --client-id $CLIENT_ID --client-secret-name aad-client-secret \
  --tenant-id $TENANT --issuer "https://login.microsoftonline.com/$TENANT/v2.0" --yes

# Require login for every request
az containerapp auth update --name $APP --resource-group $RG \
  --unauthenticated-client-action RedirectToLoginPage \
  --redirect-provider azureactivedirectory
```

`AzureADMyOrg` restricts sign-in to accounts in your tenant. To limit it further to specific people, enable
*User assignment required* on the Enterprise Application that Azure created for the registration and assign
users or groups there.

The `/api/*` endpoints are protected the same way; API calls from scripts need an Entra ID token for the
registration.

**Let the app enforce it.** Easy Auth adds the signed-in user to every request it forwards
(`X-MS-CLIENT-PRINCIPAL-ID` with the Entra object id, `X-MS-CLIENT-PRINCIPAL-NAME`, and the claims in
`X-MS-CLIENT-PRINCIPAL`) and removes such headers from the client's request. Tell Arcviz to take the identity
from there and to refuse anything without it, so that a misconfigured or disabled Easy Auth does not silently
open the app. `ARCVIZ_ADMIN_USER` is the object id of the person who owns the data written before workspaces existed (and the
default project); find it with `az ad signed-in-user show --query id -o tsv`:

```bash
az containerapp update --name $APP --resource-group $RG \
  --set-env-vars ARCVIZ_AUTH=proxy ARCVIZ_AUTH_PROXY_PRESET=easyauth ARCVIZ_ADMIN_USER=<object id>
```

**The identity headers are trusted blindly.** Proxy mode is safe only when the container is reachable through
the ingress alone (the default for a Container App with Easy Auth; probes go to the container directly but
`/healthz` and `/readyz` need no authentication) and the proxy overwrites the identity headers on every request.
Anyone who can reach the container directly can claim to be anyone, including the owners of projects. With proxy
mode the user id also keys the optional rate limits (`ARCVIZ_RATE_LIMIT_*`, see
[Configuration](reference/configuration.md#request-limits)).

**Upgrading a deployment that used `ARCVIZ_AUTH_PROXY_HEADER=X-MS-CLIENT-PRINCIPAL-NAME`:** that header keeps
working as the user id (the sign-in name). Either keep it and set `ARCVIZ_ADMIN_USER` to your sign-in name, or
switch to the preset (stable object ids; set `ARCVIZ_ADMIN_USER` to your object id). Without `ARCVIZ_ADMIN_USER`
the existing diagrams stay hidden until it is set (nothing is lost); see
[Own and shared projects](guide/workspaces.md).

### Agents and MCP behind Easy Auth

Coding agents reach Arcviz through the [MCP endpoint](guide/agents.md) (`ARCVIZ_MCP=1`). Easy Auth would redirect an
agent to the browser login, so the endpoint has its own credentials, checked by the app itself:

- **Personal access tokens** need nothing else, except that Easy Auth must let `/mcp` through (below). Each user creates
  them under **Agenten-Zugang…**; they work only on `/mcp`.
- **OAuth with Entra ID**, so that an agent signs the user in at Entra and acts as the same user as in the browser. The app
  registration of step 6 needs an API (**Expose an API**: an application ID URI such as `api://<client id>` and a scope such
  as `mcp.use`), and the MCP client's redirect URIs as a *public client* (mobile and desktop platform; the client documents
  its redirect URI). Then set

    ```bash
    az containerapp update --name $APP --resource-group $RG --set-env-vars       ARCVIZ_MCP=1 ARCVIZ_PUBLIC_URL=https://<your app's FQDN>       ARCVIZ_MCP_OAUTH_ISSUER=https://login.microsoftonline.com/$TENANT/v2.0       ARCVIZ_MCP_OAUTH_AUDIENCE=$CLIENT_ID ARCVIZ_MCP_OAUTH_REQUIRED_SCOPE=mcp.use
    ```

    (use the audience your access tokens carry: `aud` is the client id for v2 tokens of an app registration that accepts
    them, the application ID URI otherwise; the app refuses tokens of any other audience). The identity is the token's `oid`
    claim, the same value as `X-MS-CLIENT-PRINCIPAL-ID`. Entra does not offer dynamic client registration, so the client is told
    the application id, for example `claude mcp add --transport http arcviz https://<fqdn>/mcp --client-id $CLIENT_ID`.
- **Exclude the endpoint from Easy Auth.** `/mcp` and `/.well-known/oauth-protected-resource*` must reach the app without the
  browser redirect, so add them to Easy Auth's *excluded paths* (`az containerapp auth update --excluded-paths …`; the exact
  option depends on your CLI version, see `az containerapp auth update --help`). The app does not trust identity headers on
  these paths: it validates the token itself (signature, issuer, audience, expiry), so excluding them does not open anything
  that a valid token does not open. Everything else stays behind Easy Auth.

The Entra/Easy Auth side of this setup follows the documented behaviour of the services and the unit tests of the
validator (a local key and a fake issuer); it has not been verified against a real tenant in this repository.

**Without Easy Auth** (for example to call the API from scripts with a simple secret), use the app's own
token instead: store a long random token as a secret and enable token mode. The browser then shows a
login page once; scripts send `Authorization: Bearer <token>`.

```bash
az containerapp secret set --name $APP --resource-group $RG --secrets "arcviz-token=$(openssl rand -hex 32)"
az containerapp update --name $APP --resource-group $RG \
  --set-env-vars ARCVIZ_AUTH=token ARCVIZ_AUTH_TOKEN=secretref:arcviz-token ARCVIZ_COOKIE_SECURE=true
```

`ARCVIZ_COOKIE_SECURE=true` is needed because TLS ends at the ingress, so the app itself sees plain HTTP.

## Logs and Application Insights

Container Apps sends the container's output to the environment's Log Analytics workspace. Switch the app to JSON
logs so that every field can be queried, with one record per request and per chat turn (token usage, duration,
outcome) and the request id the ingress assigned:

```bash
az containerapp secret set --name $APP --resource-group $RG --secrets "log-pseudonym-key=$(openssl rand -hex 32)"
az containerapp update --name $APP --resource-group $RG \
  --set-env-vars ARCVIZ_LOG_FORMAT=json ARCVIZ_LOG_PSEUDONYM_KEY=secretref:log-pseudonym-key
```

```kusto
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "arcviz"
| extend r = parse_json(Log_s)
| where r.event == "llm_turn"
| project TimeGenerated, r.request_id, r.user, r.outcome, r.error_kind, r.rounds, r.input_tokens, r.output_tokens, r.duration_ms
```

`user` is a keyed pseudonym (`u-…`, an HMAC with the secret above), never the user's id, name or e-mail address;
`client_request_id` is the ingress's request id; see
[Configuration: logs and telemetry](reference/configuration.md#logs-and-telemetry).

For request traces in **Application Insights**, create a workspace-based Application Insights resource and pass its
connection string (the image includes the `telemetry` extra):

```bash
# the Log Analytics workspace that `az containerapp env create` made in step 1
LAW_ID=$(az monitor log-analytics workspace list --resource-group $RG --query '[0].id' -o tsv)
az monitor app-insights component create --app arcviz-ai --resource-group $RG --location $LOC --workspace "$LAW_ID"
AI_CONN=$(az monitor app-insights component show --app arcviz-ai --resource-group $RG --query connectionString -o tsv)
az containerapp secret set --name $APP --resource-group $RG --secrets "ai-connection=$AI_CONN"
az containerapp update --name $APP --resource-group $RG \
  --set-env-vars ARCVIZ_TELEMETRY=azure-monitor APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:ai-connection \
    OTEL_SERVICE_NAME=arcviz
```

Requests appear with their route templates (ids are replaced by `{id}`), the app's log records as traces correlated
with them; a chat turn shows its model calls, tool executions and store writes as child spans, and the custom metrics
`arcviz.http.server.request.duration`, `arcviz.llm.duration`, `arcviz.llm.tokens`, `arcviz.live.streams` and, with the
Cosmos DB store, `arcviz.store.request_units` (the request charge per request, by operation and document kind, for
example the RU cost of the presence relay or the writer heartbeat) arrive in `customMetrics`. `ARCVIZ_TELEMETRY=otlp` sends the same to any OpenTelemetry collector (`OTEL_EXPORTER_OTLP_ENDPOINT`).

**Keyless export (managed identity).** To send telemetry with the app's managed identity instead of the connection
string's key, give the identity the role *Monitoring Metrics Publisher* on the Application Insights resource, set
`ARCVIZ_TELEMETRY_AUTH=entra` and, if the identity is user-assigned, `AZURE_CLIENT_ID`. The connection string is still
needed (it names the resource and the ingestion endpoint); once local authentication is disabled on the resource, its
key grants nothing:

```bash
AI_ID=$(az monitor app-insights component show --app arcviz-ai --resource-group $RG --query id -o tsv)
PRINCIPAL=$(az containerapp show --name $APP --resource-group $RG --query identity.principalId -o tsv)
az role assignment create --assignee "$PRINCIPAL" --role "Monitoring Metrics Publisher" --scope "$AI_ID"
az monitor app-insights component update --app arcviz-ai --resource-group $RG --set disableLocalAuth=true
az containerapp update --name $APP --resource-group $RG --set-env-vars ARCVIZ_TELEMETRY_AUTH=entra
```
If the workspace query above shows no `Log_s` column, your environment uses the newer schema: query
`ContainerAppConsoleLogs` and its `Log` column instead.

## 7. Updates, backups, costs

**Updating the image.** With the `:latest` tag ACA does not pull automatically. Pin a version and update
explicitly:

```bash
az containerapp update --name $APP --resource-group $RG --image ghcr.io/thomasniebler/arcviz:0.1.1
```

Each update creates a new revision; with single-revision mode (the default) the old one is stopped once the
new one is ready. Because both briefly exist, the file store sees two writers for a few seconds. In practice
this is harmless (the new revision only reads until a user acts), but if you want to rule it out, scale to zero
first (`--min-replicas 0 --max-replicas 0`), update, then scale back.

**Backups.** Enable Azure Backup for the file share, or snapshot it:

```bash
az storage share snapshot --account-name $STORAGE --name $SHARE
```

**Costs.** Consumption plan, billed per vCPU-second and GiB-second. With `min-replicas 1` the instance is
always on, so expect the "always on, 0.5 vCPU / 1 GiB" line of the pricing calculator plus a few cents for the
file share and Log Analytics ingestion. LLM usage is billed by the provider and typically dominates.

**Version history.** Every chat turn and edit adds a version, and by default nothing is ever removed. With Cosmos DB
each version is an item, so storage and request units grow with a diagram's lifetime. To bound them, opt in to the
retention policy (see [configuration](reference/configuration.md#version-history)), for example keep the newest 200
versions per diagram:

```bash
az containerapp update --name $APP --resource-group $RG --set-env-vars ARCVIZ_VERSIONS_KEEP=200
```

The first version, everything undo and redo still need and manual snapshots are always kept (set
`ARCVIZ_VERSIONS_PRUNE_MANUAL=1` to let manual snapshots go too). Pruned versions are deleted for good; export or
mirror to Git (`ARCVIZ_GIT_DIR`) first if you need the full history.

## Tear down

```bash
az group delete --name $RG --yes --no-wait
az ad app delete --id $CLIENT_ID
```

## Alternatives on Azure

- **App Service for Containers (Linux Web App).** Also works: mount the Azure Files share via *Path mappings*,
  set `WEBSITES_PORT=8000`, enable Easy Auth under *Authentication*. Choose it if you already pay for an App
  Service plan. Keep *Always On* enabled and do not scale out.
- **Azure Container Instances.** Supports Azure Files volumes and is cheaper for very light use, but has no
  built-in TLS, custom domains or authentication. Only reasonable on a private VNet.
- **A VM** with Docker Compose, Caddy and the repo's `docker-compose.yml`. Most flexible, most maintenance.
