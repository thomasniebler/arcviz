# Component diagrams

A component diagram (type **Komponentendiagramm**) shows the **building blocks of a system**: the components, the interfaces
they provide and require, and who depends on whom. It sits between the use case diagram (what the system is for) and the
deployment diagram (where it runs). It follows the *Components* chapter of UML 2.5.1. Create one with **Neu → Typ:
Komponentendiagramm**, either empty or from the template **Webshop-Bausteine** (`webshop-components`). A new component diagram
reads left to right.

Use the **deployment diagram** instead when the question is *where* things run (nodes, networks, cloud resources); the
component diagram answers *which* building blocks exist and how they depend on each other. Both use the same project
elements, so a component named *Bestellservice* is one element in both.

## Elements

| Kind | Drawn as | Use it for |
|---|---|---|
| `component` | box `«component»` with the name and one line `port …` per port | a building block with one responsibility |
| `subsystem` | box around its contents, `«subsystem»` | a larger block that contains components, interfaces and subsystems |
| `interface` | circle with the name (a long name: pill with `«interface»`) | a contract, an API |
| `package` | box around its contents | groups blocks; takes part only in dependencies |

**Ports** are the named interaction points of a component. Write them into the component's *ports* field (inspector: one per
line, optionally with a type: `orders-in: OrderApi`; at most ten). Connectors and interface edges can name the port they
leave from or arrive at.

## Edges

An edge points from the element that refers to the other to the element it refers to.

| Kind | Drawn as | Use it for |
|---|---|---|
| `connection` | solid arrow | a component uses another one: from the requirer to the provider; may name a port at either end |
| `realization` | dashed `«realizes»` | a component **provides** an interface (component → interface); may name the providing port |
| `usage` | dashed `«use»` | a component **requires** an interface (component → interface) |
| `dependency` | dashed arrow | a looser "uses" between any two elements |

The named ports show after the edge's text: `zahlt (https) (payments-out →)`. UML draws provided and required interfaces as
lollipops and sockets, which a flow chart cannot; here they are circles with edges from the components.

Arcviz keeps the diagram well-formed: a connection joins two different components or subsystems, a realization and a usage go
from a component to an interface, no edge joins a block with the subsystem or package that contains it, a package takes part
only in dependencies, and the ports an edge names exist (a port that an edge names cannot be removed). A request that breaks
a rule is refused with the reason.

## Review

**Prüfen** looks for interfaces that are required and not provided (or provided and never used), components that depend on
each other in a cycle (also through interfaces), components with very many dependencies, unconnected ports and empty
subsystems.

## Project model

Components and subsystems become *component* and *subsystem* elements of the project when you create them (or are linked to the
element of the same name); interfaces and packages are not linked.

Connections (*connection*) and dependencies can show a relation of the project model: *Kommunikation* and, for connections,
*Datenfluss*. Usages and realizations end at an interface, which is no element, so they are never linked. Like in deployment
diagrams the **Vollständigkeit** menu declares a diagram complete for a kind, and **Prüfen** reports a relation that no
deployment or component diagram shows ([shared model](shared-model.md)).

## What works as for other diagrams

Subsystems and packages can be focused, versions compared, the layout direction changed, and the diagram exported as SVG, PNG,
Mermaid source, JSON and PlantUML. PlantUML cannot tell an incoming from an outgoing port, so every port is written as an input
port.
