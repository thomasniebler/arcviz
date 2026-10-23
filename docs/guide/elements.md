# Elements and edges

A diagram is a tree of **elements** plus **edges** between them. Every element has a stable
**id** (a lower-case slug derived from the name unless given explicitly), a display **name**, a
**kind**, an optional **parent** and free-form string **properties**.

Names, diagram titles and edge labels are one line: a line break or tab in a pasted name becomes a
space, other control characters are dropped (notes keep their line breaks). Everything else is shown
exactly as typed, markup and entity codes such as `#lt;` included.

## Container kinds

Containers can hold other elements and are drawn as nested boxes (Mermaid subgraphs).

| Kind | Typical use | Drawing |
|---|---|---|
| `deployment` | A deployment environment or landing zone | Light blue box, dark blue border |
| `region` | Cloud region | Near-white box, grey border |
| `vnet` | Virtual network | Light green box, green border |
| `subnet` | Subnet inside a vnet | Light green box, dashed green border |
| `node` | VM, host, Kubernetes node | Light orange box, orange border |
| `cluster` | Cluster of nodes | Light purple box, purple border |
| `group` | Any other grouping | Grey box, dashed grey border |

Rules:

- An element can only be moved into a container kind. Trying to put something into a `service`
  is rejected.
- A container cannot be changed to a leaf kind while it still has children.
- Moving a container into one of its own descendants is rejected (no cycles).
- Removing a container moves its children up to its parent by default; with cascade the whole
  subtree is removed.

## Leaf kinds

| Kind | Typical use | Shape |
|---|---|---|
| `service` | Deployable service, API | Rectangle, blue |
| `component` | Library, module, internal part | Rectangle, grey-violet |
| `database` | Relational or document database | Cylinder, orange |
| `storage` | Blob, file or object storage | Cylinder, yellow |
| `queue` | Queue or topic | Double-bordered rectangle, purple |
| `gateway` | API gateway, load balancer, ingress | Hexagon, teal |
| `external` | System outside your control | Stadium shape, dashed grey border |
| `artifact` | Build artifact, image, package | Parallelogram, grey |
| `function` | Serverless function | Flag shape, cyan |

## Edges

An edge connects two elements and has an optional label (protocol, verb) and a kind:

| Kind | Arrow | Meaning |
|---|---|---|
| `connection` (default) | solid arrow | Calls, traffic |
| `dependency` | dotted arrow | Uses, depends on |
| `deploys` | thick arrow | Deploys to, runs on |
| `association` | line without arrowhead | Related, no direction |
| `replication` | double-headed arrow | Replicates with |

Edges are idempotent: adding the same source, target, kind, label and direction twice results in
one edge. Removing an element removes every edge that touches it.

### Direction

The kind decides the line (solid, dotted, thick) and the usual arrowheads. An edge can override the
heads with a **direction**: `forward` (arrow at the target), `both` (arrows at both ends) or `none`
(plain line). A dotted `dependency` with direction `both` is drawn as a dotted double arrow. Without
a direction the edge looks as its kind says; `default` resets an explicit direction.

### Edge properties

Like elements, edges have free-form string properties. `protocol` and `port` are shown on the
edge next to the label, e.g. *orders (https:443)*; other properties (`tls`, `auth`, …) are kept in
the model and shown in the inspector. Ask for example: *"Die Verbindung von service-a zur orders-db
läuft über postgres auf Port 5432"*.

### Managed services and redundancy

A managed store that the platform replicates by design (Azure Cosmos DB, DynamoDB, Spanner) needs no replica
nodes. Draw it with the provider's kind from a [kind pack](#cloud-icons-and-kind-packs),
or as a `database` with the property `redundancy=built_in`; the review then counts it as replicated by the
platform instead of reporting a single point of failure. `redundancy=none` says the opposite for a tier that is not
redundant (for example a single-region account). Ask for example: *"Füge eine Cosmos DB orders-cosmos hinzu"*.
Databases of the packs whose redundancy is a configuration choice (Azure SQL, RDS, Cloud SQL, …) are still reported
until you set `redundancy=built_in` (or add replicas).

## Custom kinds

The built-in kinds fit Azure-style deployment diagrams. For other vocabularies (AWS, GCP,
Kubernetes, …) the operator can add kinds with a JSON file named by `ARCVIZ_KINDS_FILE` (see
[Configuration](../reference/configuration.md#element-kinds-optional)). Added kinds work like the
built-in ones everywhere: the model can use them, the inspector offers them, voice commands find
containers of them, and they get their own style.

```json
{
  "node_kinds": {
    "vpc": { "container": true, "style": "fill:#fff4e5,stroke:#ff9900,color:#232f3e" },
    "lambda": { "shape": "asymmetric", "style": "fill:#fff4e5,stroke:#ff9900,color:#232f3e" },
    "s3": { "shape": "cylinder" }
  },
  "edge_kinds": {
    "peering": { "line": "dotted", "direction": "both" },
    "invokes": {}
  }
}
```

| Field | Values |
|---|---|
| kind name | lower-case letters, digits and `_`, starting with a letter (max. 32); not a built-in kind and not a Mermaid keyword such as `end` or `class` |
| `container` (node) | `true` for a container kind, default `false` (leaf) |
| `style` (node) | Mermaid `classDef` body, e.g. `fill:#…,stroke:#…,color:#…`; without it the Mermaid theme default is used. Set fill **and** text colour so the element stays readable in light and dark mode. |
| `shape` (node, leaves only) | `rect` (default), `rounded`, `stadium`, `subroutine`, `cylinder`, `circle`, `hexagon`, `parallelogram`, `trapezoid`, `asymmetric`, `rhombus` |
| `icon` (node, leaves only) | `"<pack>:<icon>"` from the vendored icon packs `aws`, `azure`, `gcp`, e.g. `aws:lambda`; the element is drawn as the icon with its label below (see [Cloud icons](#cloud-icons-and-kind-packs)) |
| `role` (node, leaves only) | one of `database`, `storage`, `queue`, `service`, `function`, `component`, `gateway`, `external`: the built-in kind the review rules treat the kind as (so a pack database takes part in the replication rule). Kinds without a role are ignored by the rules. |
| `traits` (node, needs a `role`) | object of trait → value. Only `redundancy` exists, with the value `built_in` (the platform replicates the store, so no replica is needed). A node's property `redundancy` (`built_in` or `none`) overrides it. See [Managed services and redundancy](#managed-services-and-redundancy). |
| `line` (edge) | `solid` (default), `dotted`, `thick` |
| `direction` (edge) | default arrowheads: `forward` (default), `both`, `none` |

The file can only add kinds; built-in kinds cannot be changed or removed. If you later remove a kind
from the file, existing elements of that kind still load and are drawn with the default style (a
container with children stays a box), but no element can be given that kind any more and it
cannot receive new children.

A kind of the file may not take the name of a built-in kind of any diagram type (`interface`, `package`, `actor`, `state`,
`class`, `subsystem` and so on are taken); a file that does is refused at start-up. The kinds file extends **deployment** diagrams only; activity, sequence, state machine, class, use case and component diagrams have fixed UML
vocabularies.

## Cloud icons and kind packs

Arcviz ships three ready-made **kind packs** with cloud service kinds and their icons. The operator
switches them on with `ARCVIZ_KIND_PACKS` (for example `aws,azure`; see
[Configuration](../reference/configuration.md#element-kinds-optional)); they combine with each other and
with a kinds file.

| Pack | Kinds |
|---|---|
| `aws` | containers `aws_account`, `aws_vpc`; `aws_ec2`, `aws_lambda`, `aws_s3`, `aws_rds`, `aws_aurora`, `aws_dynamodb`, `aws_sqs`, `aws_sns`, `aws_api_gateway`, `aws_cloudfront`, `aws_ecs`, `aws_eks`, `aws_fargate`, `aws_elb`, `aws_route53`, `aws_cognito`, `aws_eventbridge`, `aws_kinesis`, `aws_elasticache`, `aws_step_functions`, `aws_secrets_manager`, `aws_cloudwatch` |
| `azure` | containers `azure_subscription`, `azure_resource_group`; `azure_functions`, `azure_sql`, `azure_aks`, `azure_cache_redis`, `azure_postgresql`, `azure_data_explorer`, `azure_devops`, `azure_pipelines`, `azure_vm`, `azure_app_service`, `azure_storage`, `azure_service_bus`, `azure_cosmos_db`, `azure_key_vault`, `azure_front_door` |
| `gcp` | containers `gcp_project`, `gcp_vpc`; `gcp_compute_engine`, `gcp_cloud_sql`, `gcp_cloud_run`, `gcp_cloud_functions`, `gcp_gke`, `gcp_bigquery`, `gcp_bigtable`, `gcp_cloud_storage`, `gcp_spanner`, `gcp_pubsub`, `gcp_dataflow`, `gcp_dataproc`, `gcp_composer` |

The icons are a subset of openly licensed (CC0) logo collections, bundled with Arcviz, so they work
offline. The vendors' official architecture icon sets are not included because their terms do not
allow redistributing them in software; where no openly licensed icon exists for a service, the
vendor's logo stands in (the Azure kinds `azure_vm`, `azure_app_service`, `azure_storage`,
`azure_service_bus`, `azure_cosmos_db`, `azure_key_vault`, `azure_front_door`, and the GCP kinds
`gcp_compute_engine`, `gcp_cloud_sql`). Sources, licences and sizes are listed in
`arcviz/static/vendor/icons/README.md`. Containers are drawn as boxes without an icon.

## Properties

Properties are key/value strings on an element, for example `cidr=10.0.0.0/16`, `region=westeurope`,
`runtime=container`, `engine=postgres`. They are not drawn in the diagram, but the model sees
them and can filter or change elements by them ("set runtime=function on all services in
app-subnet").

## Example model

The demo diagram as the model sees it (children are sorted by name):

```text
Diagram 'Demo deployment' (id: demo)
  - [idp] «external» Identity Provider
  - [prod-deployment] «deployment» prod-deployment {cloud=azure, region=westeurope}
    - [api-gateway] «gateway» api-gateway
    - [events] «queue» events
    - [orders-db] «database» orders-db {engine=postgres}
    - [service-a] «service» service-a {runtime=container}
    - [service-b] «service» service-b {runtime=container}
    - [service-c] «service» service-c {runtime=function}
Edges:
  - [e1] api-gateway -> service-a (connection) "https"
  - [e2] api-gateway -> service-b (connection) "https"
  - [e3] service-a -> orders-db (connection) "sql"
  - [e4] service-b -> events (connection) "publish"
  - [e5] service-c -> events (connection) "subscribe"
  - [e6] api-gateway -> idp (dependency) "oidc"
```
