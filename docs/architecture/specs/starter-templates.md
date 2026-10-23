# More starter templates

- Status: Implemented (milestone M6)
- Issue: #73
- ADR: [0011 Starter templates as code, listed statically in the UI](../adr/0011-starter-templates-as-code.md)
- Depends on: [Operations](../../spec/operations.md), [Edge direction and properties](edge-attributes.md)

## Summary

A new diagram starts either empty or from the demo deployment. Three more starter diagrams cover
common Azure architectures: a hub-and-spoke network, an AKS cluster with a managed database, and
an event-driven processing pipeline. They are selectable in the **Neu** dialog and through
`POST /api/diagrams` (`template`), like the existing ones.

## Goals

- Templates `hub-spoke`, `aks-database` and `event-driven` next to `empty` and `demo`.
- Built through `operations` like the demo (single mutation path), so they always satisfy the
  model's invariants and use only built-in kinds.
- They show the modelling features: nesting several levels deep, properties such as `cidr`,
  edge `direction` and edge `protocol`/`port`.
- Listed in the UI dialog; every template the server offers is selectable there.

## Non-goals

- User-defined templates (saving a diagram as a template), templates in files or in the store.
- Templates for configured kinds (#72); templates use built-in kinds only.
- Localised template titles in the API.

## Design

### Templates (`templates.py`)

Each template is a function `(diagram_id, title) -> Diagram` registered in `TEMPLATES`:

| Id | Content |
|---|---|
| `hub-spoke` | Region `westeurope` with a hub VNet (`10.0.0.0/16`; firewall and VPN gateway subnets with their gateways), an app spoke (`10.1.0.0/16`, app subnet with a service) and a data spoke (`10.2.0.0/16`, data subnet with a database); on-premises network outside. Peering edges hub ↔ spokes (`direction: both`), site-to-site VPN on-premises ↔ VPN gateway (`protocol: ipsec`), egress through the firewall, app → database (`protocol: tds`, `port: 1433`). |
| `aks-database` | Deployment `prod` (`cloud: azure`, `region: westeurope`) with a VNet `aks-vnet` holding an Application Gateway subnet, an AKS subnet (cluster `aks-cluster` → node pool `user-pool` → services `frontend`, `orders-api`) and a database subnet (PostgreSQL flexible server); container registry outside the VNet; external users. Edges users → app gateway (`https`, 443), gateway → frontend → orders-api, orders-api → database (`postgres`, 5432), registry `deploys` to the node pool. |
| `event-driven` | Deployment `event-platform` with an API gateway, an order service and its database, a topic `order-events`, two functions and a notification service subscribed to the topic, and an event archive; external clients. Edges show publish/subscribe (`protocol: amqp`) and the archive capture as a `dependency`. |

Ids are given explicitly so the templates are stable (tests and documentation refer to them).

### HTTP

No change: `POST /api/diagrams` accepts the new ids, `GET /api/config` lists them in `templates`.

### Frontend

`index.html` lists the templates as static `<option>`s with German labels, the demo first (as
today). A test asserts that every id in `TEMPLATES` has an option, so a template cannot be added
on the server without becoming selectable.

## Testing

- `tests/test_templates.py`: every template builds, has no integrity errors, uses only built-in
  kinds, renders to Mermaid, and contains what the table promises (spot checks of nesting, edge
  direction, protocol/port); every `TEMPLATES` id is an option in `index.html`; creating each
  through `POST /api/diagrams` returns 201.
- `tests/e2e/smoke.mjs`: the **Neu** dialog offers the new templates and creating a diagram from
  `hub-spoke` renders nested subgraphs.

## Migration/compatibility

Additive. Existing diagrams are unaffected.

## Open questions

None.

## Decisions

- Templates stay Python functions built with operations; the UI lists them statically with a drift
  test instead of fetching labels from the API:
  [ADR-0011](../adr/0011-starter-templates-as-code.md) (Proposed).
