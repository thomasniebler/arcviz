# Class diagrams

A class diagram (type **Klassendiagramm**) shows the **things of a domain or a design** and how they relate: classes with
their attributes and operations, interfaces, enumerations and the relationships between them. Requirements engineers use it
for the vocabulary of the domain, architects for the logical data and service model. It follows the *Classification* chapter
of UML 2.5.1. Create one with **Neu → Typ: Klassendiagramm**, either empty or from the template **Webshop-Fachmodell**
(`webshop-domain`).

## Elements

| Kind | Drawn as | Use it for |
|---|---|---|
| `class` | box: name, attributes, operations (`«abstract»` above the name when abstract) | a noun of the domain or design ("Bestellung") |
| `interface` | box with `«interface»` and the operations | a contract that classes realize |
| `enumeration` | box with `«enumeration»` and the values | a fixed set of named values |
| `data_type` | box with `«dataType»` | a value without identity (Money, Adresse) |
| `package` | box around its contents | groups classifiers; may contain packages |

**Members** are lines of text in the UML notation, one per line: `- id: UUID`, `+ aufgeben(): Bestellung`. Start a member
with its visibility: `+` public, `-` private, `#` protected, `~` package. Edit them in the inspector (a text field per
compartment, one member per line) or ask in the chat. A box shows up to eight members per compartment, then `… (+3)`; the
inspector, the exports and the assistant have all of them.

## Relationships

An edge always points **from the element that refers to the other to the element it refers to**:

| Kind | Drawn as | Direction of the edge |
|---|---|---|
| `association` | plain line (an arrow when you make it navigable) | either way; give it a name, **multiplicities** (`1`, `0..*`, `1..*`) and **role names** for both ends |
| `aggregation` | line with a circle at the whole | **part → whole** |
| `composition` | thick line with a circle at the whole | **part → whole** (the part cannot live without its whole) |
| `generalization` | line with an arrow at the general class | specific → general ("Medienartikel" → "Artikel") |
| `realization` | dashed line `«realizes»` | class → interface |
| `dependency` | dashed arrow | client → supplier; the only relationship a package may have |

The circle for aggregation and composition stands in for UML's hollow and filled diamond, and the arrow for
generalization for UML's hollow triangle: the diagram is drawn as a flow chart, which has no such symbols. So that the circle
sits at the whole, the edge is stored **from the part to the whole**.

Arcviz keeps the diagram well-formed: a generalization joins two classes (or two interfaces, or two data types) and never
forms a cycle, a realization goes from a class to an interface, aggregation and composition join classes, and a package takes
part only in dependencies. A request that breaks a rule is refused with the reason.

## Review

**Prüfen** looks for packages that depend on each other in a cycle, parts with several wholes, duplicate members, interfaces
nobody realizes, classes without relationships or members, and associations without multiplicities.

## Project model

A class can be linked to a *data* element of the project and an interface to a *component* element (inspector, section
**Projektelement**). They are **not** taken over into the project automatically, because a domain model has many classes; use
**Unverknüpfte übernehmen…** to adopt the ones you want.

## What works as for other diagrams

Packages can be focused, versions compared, the layout direction changed, and the diagram exported as SVG, PNG, Mermaid
source, JSON and PlantUML. PlantUML has no syntax for role names: they follow the association's name in parentheses.
