# Diagrams

A diagram is one model with its own version history and chat transcript. Arcviz can hold any
number of diagrams, grouped into [projects and sections](projects.md); the project navigator on the
left switches between them. The last opened project and diagram are remembered in the browser.

## Diagram types

Every diagram has a type, chosen when it is created and fixed afterwards. All types follow UML 2.5.1:

| Type (UI) | Shows |
|---|---|
| **Verteilungsdiagramm** (`deployment`) | Infrastructure: deployments, networks, clusters and the services, databases and queues in them, connected by edges (see [Elements and edges](elements.md)). |
| **Aktivitätsdiagramm** (`activity`) | A process: actions, decisions, parallel flows and swimlanes (see [Activity diagrams](activity-diagrams.md)). |
| **Sequenzdiagramm** (`sequence`) | An interaction over time: participants and the messages they exchange, with alternatives, loops and references (see [Sequence diagrams](sequence-diagrams.md)). |

Controls that a type does not support yet are disabled and say so in their tooltip.

## Creating a diagram

**Neu** opens a dialog with a title, the diagram **Typ**, a template and the **Abschnitt** of the open
project the diagram goes into; the template list shows the templates of the chosen type. The deployment templates are:

| Template | Content |
|---|---|
| **Demo (Deployment mit Services)** | A deployment `prod-deployment` (cloud=azure, region=westeurope) containing an API gateway, `service-a`, `service-b`, `service-c`, the database `orders-db` and the queue `events`, plus an external identity provider `idp`. Six edges connect them. |
| **Hub-and-Spoke-Netzwerk** (`hub-spoke`) | Region `westeurope` with a hub VNet (firewall and VPN gateway in their own subnets), an app spoke with `app-service` and a data spoke with the database `sql-db`, plus the external `on-premises` network. Peerings hub ↔ spokes, a site-to-site VPN, egress through the firewall and the SQL connection (`tds:1433`). |
| **AKS mit Datenbank** (`aks-database`) | Deployment `prod` with VNet `aks-vnet`: an Application Gateway subnet, an AKS subnet with `aks-cluster` → node pool `user-pool` → services `frontend` and `orders-api`, and a database subnet with `postgres`; the container registry `acr` deploys images to the node pool; external `users` come in via HTTPS. |
| **Ereignisgesteuert** (`event-driven`) | Deployment `event-platform`: clients call `api-gateway` → `order-service`, which stores in `orders-db` and publishes to the topic `order-events`; `billing-handler`, `shipping-handler` (functions) and `notification-service` subscribe; `event-archive` captures the events. |
| **Leer** | An empty diagram with just the title. |

The diagram id is derived from the title (lower-case, non-alphanumerics become `-`). Creating a
diagram whose id already exists fails with a message; choose a different title.

When no diagram exists at all, the UI creates the demo diagram automatically on load (in the project
*Standardprojekt*), so a fresh installation always has something to try.

## Importing a diagram

**Importieren** reads a JSON file exported with *Export → JSON herunterladen* (from this or another
Arcviz installation) and creates a **new** diagram from it in the open project; an existing diagram is never
overwritten. The new diagram gets a new id that starts with the exported one's, with a new random suffix in place
of the old one (importing the same file again and again never makes the id longer), and
starts with a single version *Initial state*. Chat and version history are not part
of the file.

The file is checked completely before anything is stored: unknown element or edge kinds, an
element inside a leaf, a missing parent, an edge to an unknown element, duplicate edge ids or an
unknown direction are all listed in one message, and nothing is created. A file written by a newer
Arcviz version is rejected with a hint to update. A diagram file copied straight from the data
directory (`latest.json`) is accepted as well.

## Deleting a diagram

**Löschen** (or **Diagramm löschen** in the navigator) asks for confirmation and removes the diagram
with all versions and the transcript; it also disappears from its project. If somebody changed the diagram while
you were reading the question, nothing is deleted: the diagram is shown again and you are asked once more, naming who
changed it.
There is no recycle bin; if you want to keep a copy, back up the directory
`<ARCVIZ_DATA_DIR>/diagrams/<id>/` first (`./data` by default, `/data` inside the container; with
Docker Compose that is the `arcviz-data` volume, so copy it with `docker cp` or a volume backup).

## What the statistics line shows

Below the canvas toolbar the title is followed by the number of elements, the number of edges and
the time of the last change.
