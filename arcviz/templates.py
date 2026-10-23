"""Starter diagrams.

The German names, notes and titles of the German templates are translated per request with `arcviz.i18n` when a
diagram is created (ADR-0199); the ids stay stable.
"""

from __future__ import annotations

from . import operations as ops
from .i18n import _
from .model import Diagram


def demo_diagram(diagram_id: str = "demo", title: str = "Demo deployment") -> Diagram:
    d = Diagram(id=diagram_id, title=title)
    ops.add_node(
        d,
        "prod-deployment",
        kind="deployment",
        id="prod-deployment",
        properties={"cloud": "azure", "region": "westeurope"},
    )
    ops.add_node(d, "api-gateway", kind="gateway", id="api-gateway", parent="prod-deployment")
    ops.add_node(
        d, "service-a", kind="service", id="service-a", parent="prod-deployment", properties={"runtime": "container"}
    )
    ops.add_node(
        d, "service-b", kind="service", id="service-b", parent="prod-deployment", properties={"runtime": "container"}
    )
    ops.add_node(
        d, "service-c", kind="service", id="service-c", parent="prod-deployment", properties={"runtime": "function"}
    )
    ops.add_node(
        d, "orders-db", kind="database", id="orders-db", parent="prod-deployment", properties={"engine": "postgres"}
    )
    ops.add_node(d, "events", kind="queue", id="events", parent="prod-deployment")
    ops.add_node(d, "Identity Provider", kind="external", id="idp")
    ops.add_edge(d, "api-gateway", "service-a", label="https")
    ops.add_edge(d, "api-gateway", "service-b", label="https")
    ops.add_edge(d, "service-a", "orders-db", label="sql")
    ops.add_edge(d, "service-b", "events", label="publish")
    ops.add_edge(d, "service-c", "events", label="subscribe")
    ops.add_edge(d, "api-gateway", "idp", label="oidc", kind="dependency")
    return d


def hub_spoke_diagram(diagram_id: str = "hub-spoke", title: str = "Hub-and-spoke network") -> Diagram:
    d = Diagram(id=diagram_id, title=title)
    ops.add_node(d, "westeurope", kind="region", id="westeurope", properties={"cloud": "azure"})
    ops.add_node(d, "hub-vnet", kind="vnet", id="hub-vnet", parent="westeurope", properties={"cidr": "10.0.0.0/16"})
    ops.add_node(
        d,
        "AzureFirewallSubnet",
        kind="subnet",
        id="firewall-subnet",
        parent="hub-vnet",
        properties={"cidr": "10.0.1.0/26"},
    )
    ops.add_node(d, "azure-firewall", kind="gateway", id="azure-firewall", parent="firewall-subnet")
    ops.add_node(
        d, "GatewaySubnet", kind="subnet", id="gateway-subnet", parent="hub-vnet", properties={"cidr": "10.0.2.0/27"}
    )
    ops.add_node(d, "vpn-gateway", kind="gateway", id="vpn-gateway", parent="gateway-subnet")
    ops.add_node(
        d, "spoke-app-vnet", kind="vnet", id="spoke-app-vnet", parent="westeurope", properties={"cidr": "10.1.0.0/16"}
    )
    ops.add_node(
        d, "app-subnet", kind="subnet", id="app-subnet", parent="spoke-app-vnet", properties={"cidr": "10.1.1.0/24"}
    )
    ops.add_node(
        d, "app-service", kind="service", id="app-service", parent="app-subnet", properties={"runtime": "container"}
    )
    ops.add_node(
        d, "spoke-data-vnet", kind="vnet", id="spoke-data-vnet", parent="westeurope", properties={"cidr": "10.2.0.0/16"}
    )
    ops.add_node(
        d, "data-subnet", kind="subnet", id="data-subnet", parent="spoke-data-vnet", properties={"cidr": "10.2.1.0/24"}
    )
    ops.add_node(d, "sql-db", kind="database", id="sql-db", parent="data-subnet", properties={"engine": "azure sql"})
    ops.add_node(d, "on-premises", kind="external", id="on-premises", properties={"cidr": "192.168.0.0/16"})
    ops.add_edge(d, "hub-vnet", "spoke-app-vnet", label="peering", direction="both")
    ops.add_edge(d, "hub-vnet", "spoke-data-vnet", label="peering", direction="both")
    ops.add_edge(
        d, "on-premises", "vpn-gateway", label="site-to-site VPN", direction="both", properties={"protocol": "ipsec"}
    )
    ops.add_edge(d, "app-service", "azure-firewall", label="egress")
    ops.add_edge(d, "app-service", "sql-db", label="sql", properties={"protocol": "tds", "port": "1433"})
    return d


def aks_database_diagram(diagram_id: str = "aks-database", title: str = "AKS with database") -> Diagram:
    d = Diagram(id=diagram_id, title=title)
    ops.add_node(d, "prod", kind="deployment", id="prod", properties={"cloud": "azure", "region": "westeurope"})
    ops.add_node(d, "aks-vnet", kind="vnet", id="aks-vnet", parent="prod", properties={"cidr": "10.10.0.0/16"})
    ops.add_node(
        d, "appgw-subnet", kind="subnet", id="appgw-subnet", parent="aks-vnet", properties={"cidr": "10.10.4.0/24"}
    )
    ops.add_node(
        d, "app-gateway", kind="gateway", id="app-gateway", parent="appgw-subnet", properties={"sku": "WAF_v2"}
    )
    ops.add_node(
        d, "aks-subnet", kind="subnet", id="aks-subnet", parent="aks-vnet", properties={"cidr": "10.10.0.0/22"}
    )
    ops.add_node(
        d, "aks-cluster", kind="cluster", id="aks-cluster", parent="aks-subnet", properties={"kubernetes": "1.30"}
    )
    ops.add_node(
        d,
        "user-pool",
        kind="node",
        id="user-pool",
        parent="aks-cluster",
        properties={"vm_size": "Standard_D4s_v5", "count": "3"},
    )
    ops.add_node(d, "frontend", kind="service", id="frontend", parent="user-pool", properties={"replicas": "2"})
    ops.add_node(d, "orders-api", kind="service", id="orders-api", parent="user-pool", properties={"replicas": "3"})
    ops.add_node(d, "db-subnet", kind="subnet", id="db-subnet", parent="aks-vnet", properties={"cidr": "10.10.8.0/24"})
    ops.add_node(
        d,
        "postgres",
        kind="database",
        id="postgres",
        parent="db-subnet",
        properties={"engine": "postgres 16", "sku": "GP_Standard_D2s_v3"},
    )
    ops.add_node(d, "container-registry", kind="storage", id="acr", parent="prod", properties={"sku": "premium"})
    ops.add_node(d, "users", kind="external", id="users")
    ops.add_edge(d, "users", "app-gateway", properties={"protocol": "https", "port": "443"})
    ops.add_edge(d, "app-gateway", "frontend", label="ingress")
    ops.add_edge(d, "frontend", "orders-api", label="REST")
    ops.add_edge(d, "orders-api", "postgres", label="sql", properties={"protocol": "postgres", "port": "5432"})
    ops.add_edge(d, "acr", "user-pool", label="images", kind="deploys")
    return d


def event_driven_diagram(diagram_id: str = "event-driven", title: str = "Event-driven processing") -> Diagram:
    d = Diagram(id=diagram_id, title=title)
    ops.add_node(d, "event-platform", kind="deployment", id="event-platform", properties={"cloud": "azure"})
    ops.add_node(d, "api-gateway", kind="gateway", id="api-gateway", parent="event-platform")
    ops.add_node(d, "order-service", kind="service", id="order-service", parent="event-platform")
    ops.add_node(
        d, "orders-db", kind="database", id="orders-db", parent="event-platform", properties={"engine": "cosmos db"}
    )
    ops.add_node(
        d, "order-events", kind="queue", id="order-events", parent="event-platform", properties={"type": "topic"}
    )
    ops.add_node(d, "billing-handler", kind="function", id="billing-handler", parent="event-platform")
    ops.add_node(d, "shipping-handler", kind="function", id="shipping-handler", parent="event-platform")
    ops.add_node(d, "notification-service", kind="service", id="notification-service", parent="event-platform")
    ops.add_node(d, "event-archive", kind="storage", id="event-archive", parent="event-platform")
    ops.add_node(d, "clients", kind="external", id="clients")
    ops.add_edge(d, "clients", "api-gateway", properties={"protocol": "https", "port": "443"})
    ops.add_edge(d, "api-gateway", "order-service", label="orders")
    ops.add_edge(d, "order-service", "orders-db", label="store")
    ops.add_edge(d, "order-service", "order-events", label="publish", properties={"protocol": "amqp"})
    for subscriber in ("billing-handler", "shipping-handler", "notification-service"):
        ops.add_edge(d, "order-events", subscriber, label="subscribe", properties={"protocol": "amqp"})
    ops.add_edge(d, "order-events", "event-archive", label="capture", kind="dependency")
    return d


def order_process_diagram(diagram_id: str = "order-process", title: str | None = None) -> Diagram:
    """Activity diagram: partitions, a decision with guards, fork/join and an object flow."""
    title = title or _("Bestellprozess")
    d = Diagram(id=diagram_id, title=title, type="activity")
    for name, pid in ((_("Kunde"), "kunde"), ("Shop", "shop"), (_("Lager"), "lager")):
        ops.add_node(d, name, kind="activity_partition", id=pid)
    ops.add_node(d, "Start", kind="initial_node", id="start", parent="kunde")
    ops.add_node(d, _("Bestellung aufgeben"), kind="opaque_action", id="bestellen", parent="kunde")
    ops.add_node(d, _("Bestellung prüfen"), kind="opaque_action", id="pruefen", parent="shop")
    ops.add_node(d, _("Artikel verfügbar?"), kind="decision_node", id="verfuegbar", parent="shop")
    ops.add_node(d, "Fork", kind="fork_node", id="fork", parent="shop")
    ops.add_node(d, _("Zahlung abwickeln"), kind="opaque_action", id="zahlung", parent="shop")
    ops.add_node(d, _("Ware kommissionieren"), kind="opaque_action", id="kommissionieren", parent="lager")
    ops.add_node(d, "Join", kind="join_node", id="join", parent="lager")
    ops.add_node(d, _("Ware versenden"), kind="opaque_action", id="versenden", parent="lager")
    ops.add_node(d, _("Ende"), kind="activity_final_node", id="ende", parent="lager")
    ops.add_node(d, _("Kunde informieren"), kind="opaque_action", id="informieren", parent="shop")
    ops.add_node(d, _("Abbruch"), kind="flow_final_node", id="abbruch", parent="shop")
    ops.add_node(d, _("Bestellungen"), kind="data_store_node", id="bestellungen", parent="shop")
    for source, target in (
        ("start", "bestellen"),
        ("bestellen", "pruefen"),
        ("pruefen", "verfuegbar"),
        ("fork", "zahlung"),
        ("fork", "kommissionieren"),
        ("zahlung", "join"),
        ("kommissionieren", "join"),
        ("join", "versenden"),
        ("versenden", "ende"),
        ("informieren", "abbruch"),
    ):
        ops.add_edge(d, source, target, kind="control_flow")
    ops.add_edge(d, "verfuegbar", "fork", kind="control_flow", guard=_("ja"))
    ops.add_edge(d, "verfuegbar", "informieren", kind="control_flow", guard=_("nein"))
    ops.add_edge(d, "pruefen", "bestellungen", kind="object_flow", label=_("Bestellung"))
    return d


def order_lifecycle_diagram(diagram_id: str = "order-lifecycle", title: str | None = None) -> Diagram:
    """State machine diagram: a composite state with two parallel regions joined by a join, a guard with an effect,
    entry behaviour, a final state."""
    title = title or _("Lebenszyklus einer Bestellung")
    d = Diagram(id=diagram_id, title=title, type="state_machine")
    ops.add_node(d, "Start", kind="initial_pseudostate", id="start")
    ops.add_node(d, _("Neu"), id="neu")
    ops.add_node(d, _("In Bearbeitung"), kind="composite_state", id="bearbeitung")
    ops.add_node(d, _("Zahlung"), kind="state_region", id="zahlung", parent="bearbeitung")
    ops.add_node(d, _("Kommissionierung"), kind="state_region", id="kommissionierung", parent="bearbeitung")
    ops.add_node(d, _("Start Zahlung"), kind="initial_pseudostate", id="zahlung-start", parent="zahlung")
    ops.add_node(d, _("Zahlung offen"), id="zahlung-offen", parent="zahlung")
    ops.add_node(d, _("Bezahlt"), id="bezahlt", parent="zahlung", fields={"entry": _("Rechnung senden")})
    ops.add_node(
        d,
        _("Start Kommissionierung"),
        kind="initial_pseudostate",
        id="kommissionierung-start",
        parent="kommissionierung",
    )
    ops.add_node(d, _("Ware wird gepackt"), id="ware-gepackt", parent="kommissionierung")
    ops.add_node(d, _("Versandbereit"), id="versandbereit", parent="kommissionierung")
    ops.add_node(d, _("Zusammenführen"), kind="join_pseudostate", id="zusammenfuehren")
    ops.add_node(d, _("Versandt"), id="versandt")
    ops.add_node(d, _("Zugestellt"), id="zugestellt", fields={"do": _("Zustellung bestätigen")})
    ops.add_node(d, _("Storniert"), id="storniert")
    ops.add_node(d, _("Ende"), kind="final_state", id="ende")
    for source, target in (
        ("start", "neu"),
        ("zahlung-start", "zahlung-offen"),
        ("kommissionierung-start", "ware-gepackt"),
        ("zusammenfuehren", "versandt"),
        ("zugestellt", "ende"),
        ("storniert", "ende"),
    ):
        ops.add_edge(d, source, target)
    ops.add_edge(d, "neu", "bearbeitung", label=_("bearbeiten"))
    ops.add_edge(d, "neu", "storniert", label=_("stornieren"))
    ops.add_edge(d, "zahlung-offen", "bezahlt", label=_("Zahlung eingegangen"))
    ops.add_edge(d, "ware-gepackt", "versandbereit", label=_("Paket bereit"))
    ops.add_edge(d, "bezahlt", "zusammenfuehren")
    ops.add_edge(d, "versandbereit", "zusammenfuehren")
    ops.add_edge(d, "versandt", "zugestellt", label=_("zustellen"))
    ops.add_edge(
        d,
        "bearbeitung",
        "storniert",
        label=_("stornieren"),
        guard=_("Frist nicht abgelaufen"),
        fields={"effect": _("Erstattung")},
    )
    return d


def webshop_domain_diagram(diagram_id: str = "webshop-domain", title: str | None = None) -> Diagram:
    """Class diagram: packages, members, multiplicities and roles, a composition, a specialisation, an interface with its
    realisation and an enumeration."""
    title = title or _("Webshop: Fachmodell")
    d = Diagram(id=diagram_id, title=title, type="class")
    for name, pid in ((_("Bestellwesen"), "bestellwesen"), (_("Zahlung"), "zahlung")):
        ops.add_node(d, name, kind="package", id=pid)
    ops.add_node(
        d, _("Kunde"), id="kunde", parent="bestellwesen", fields={"attributes": ["- name: String", "- email: String"]}
    )
    ops.add_node(
        d,
        _("Bestellung"),
        id="bestellung",
        parent="bestellwesen",
        fields={
            "attributes": ["- id: UUID", _("- status: Bestellstatus")],
            "operations": [_("+ aufgeben()"), _("+ stornieren()")],
        },
    )
    ops.add_node(d, _("Position"), id="position", parent="bestellwesen", fields={"attributes": [_("- menge: int")]})
    ops.add_node(
        d,
        _("Artikel"),
        id="artikel",
        parent="bestellwesen",
        fields={"attributes": [_("- bezeichnung: String"), _("- preis: Money")], "abstract": True},
    )
    ops.add_node(
        d, _("Medienartikel"), id="medienartikel", parent="bestellwesen", fields={"attributes": ["- format: String"]}
    )
    ops.add_node(
        d,
        _("Bestellstatus"),
        kind="enumeration",
        id="bestellstatus",
        parent="bestellwesen",
        fields={"literals": [_("OFFEN"), _("BEZAHLT"), _("VERSANDT")]},
    )
    ops.add_node(
        d,
        _("Zahlungsverfahren"),
        kind="interface",
        id="zahlungsverfahren",
        parent="zahlung",
        fields={"operations": [_("+ zahlen(summe: Money)")]},
    )
    ops.add_node(
        d,
        _("Kreditkarte"),
        id="kreditkarte",
        parent="zahlung",
        fields={"attributes": [_("- nummer: String")], "operations": [_("+ zahlen(summe: Money)")]},
    )
    ops.add_edge(
        d,
        "kunde",
        "bestellung",
        label=_("bestellt"),
        fields={
            "source_multiplicity": "1",
            "target_multiplicity": "0..*",
            "source_role": _("kunde"),
            "target_role": _("bestellungen"),
        },
    )
    ops.add_edge(
        d,
        "position",
        "bestellung",
        kind="composition",
        fields={"source_multiplicity": "1..*", "target_multiplicity": "1"},
    )
    ops.add_edge(d, "position", "artikel", fields={"source_multiplicity": "*", "target_multiplicity": "1"})
    ops.add_edge(d, "medienartikel", "artikel", kind="generalization")
    ops.add_edge(d, "kreditkarte", "zahlungsverfahren", kind="realization")
    ops.add_edge(d, "bestellung", "zahlungsverfahren", kind="dependency", label=_("bezahlt über"))
    ops.add_edge(d, "bestellung", "bestellstatus", kind="dependency")
    return d


def webshop_use_cases_diagram(diagram_id: str = "webshop-use-cases", title: str | None = None) -> Diagram:
    """Use case diagram: a subject, actors with a generalization, an include, an extend with a condition, notes."""
    title = title or _("Webshop: Anwendungsfälle")
    d = Diagram(id=diagram_id, title=title, type="use_case")
    ops.add_node(d, _("Webshop"), kind="subject", id="webshop")
    for name, aid in ((_("Kunde"), "kunde"), (_("Stammkunde"), "stammkunde"), (_("Zahlungsdienst"), "zahlungsdienst")):
        ops.add_node(d, name, kind="actor", id=aid)
    specs = {
        "artikel-suchen": (
            _("Artikel suchen"),
            _("Ziel: Der Kunde findet einen Artikel.\nHauptablauf: 1. Suchbegriff eingeben 2. Treffer ansehen."),
        ),
        "bestellen": (
            _("Bestellen"),
            _(
                "Ziel: Der Kunde kauft die Artikel im Warenkorb.\nVorbedingung: Warenkorb nicht leer.\nHauptablauf: 1. Adresse prüfen 2. Zahlung abwickeln 3. Bestellung bestätigen.\nAlternativen: Gutschein einlösen."
            ),
        ),
        "abo-verlaengern": (
            _("Abo verlängern"),
            _("Ziel: Das Abo des Kunden läuft weiter.\nHauptablauf: 1. Abo wählen 2. Zahlung abwickeln."),
        ),
        "zahlung-abwickeln": (
            _("Zahlung abwickeln"),
            _("Ziel: Der Betrag ist eingezogen.\nHauptablauf: 1. Zahlungsart prüfen 2. Betrag buchen."),
        ),
        "gutschein-einloesen": (
            _("Gutschein einlösen"),
            _("Ziel: Der Gutschein senkt den Preis.\nHauptablauf: 1. Code eingeben 2. Rabatt berechnen."),
        ),
        "bestellung-verfolgen": (_("Bestellung verfolgen"), _("Ziel: Der Kunde sieht den Stand seiner Bestellung.")),
    }
    for uid, (name, note) in specs.items():
        ops.add_node(d, name, kind="use_case", id=uid, parent="webshop", description=note)
    for actor, use_case in (
        ("kunde", "artikel-suchen"),
        ("kunde", "bestellen"),
        ("kunde", "abo-verlaengern"),
        ("kunde", "bestellung-verfolgen"),
        ("zahlungsdienst", "zahlung-abwickeln"),
    ):
        ops.add_edge(d, actor, use_case)
    ops.add_edge(d, "stammkunde", "kunde", kind="generalization")
    ops.add_edge(d, "bestellen", "zahlung-abwickeln", kind="include")
    ops.add_edge(d, "abo-verlaengern", "zahlung-abwickeln", kind="include")
    ops.add_edge(
        d,
        "gutschein-einloesen",
        "bestellen",
        kind="extend",
        fields={"condition": _("Gutschein vorhanden"), "extension_point": _("Zahlung")},
    )
    return d


def webshop_components_diagram(diagram_id: str = "webshop-components", title: str | None = None) -> Diagram:
    """Component diagram: a subsystem, components with ports, provided and required interfaces, an external component."""
    title = title or _("Webshop: Bausteine")
    d = Diagram(id=diagram_id, title=title, type="component")
    ops.add_node(d, _("Webshop"), kind="subsystem", id="webshop")
    ops.add_node(d, "Web-UI", id="web-ui", parent="webshop")
    ops.add_node(
        d,
        _("Bestellservice"),
        id="bestellservice",
        parent="webshop",
        fields={"ports": ["orders-in: OrderApi", "payments-out"]},
    )
    ops.add_node(d, _("Katalogservice"), id="katalogservice", parent="webshop")
    ops.add_node(d, _("Lagerservice"), id="lagerservice", parent="webshop")
    ops.add_node(d, "OrderApi", kind="interface", id="orderapi", parent="webshop")
    ops.add_node(d, "CatalogApi", kind="interface", id="catalogapi", parent="webshop")
    ops.add_node(d, _("Zahlungsdienst"), id="zahlungsdienst")
    ops.add_edge(d, "web-ui", "orderapi", kind="usage")
    ops.add_edge(d, "bestellservice", "orderapi", kind="realization", fields={"source_port": "orders-in"})
    ops.add_edge(d, "bestellservice", "catalogapi", kind="usage")
    ops.add_edge(d, "katalogservice", "catalogapi", kind="realization")
    ops.add_edge(d, "bestellservice", "lagerservice", kind="connection", label=_("reserviert"))
    ops.add_edge(
        d,
        "bestellservice",
        "zahlungsdienst",
        kind="connection",
        label=_("zahlt"),
        properties={"protocol": "https"},
        fields={"source_port": "payments-out"},
    )
    return d


def checkout_diagram(diagram_id: str = "checkout", title: str = "Checkout") -> Diagram:
    """Sequence diagram: actor, alt with else, loop, ref, activations, create/delete, found and lost messages.

    Built through `operations.apply`, the only way sequence content is changed (CLAUDE.md rule 2)."""
    d = Diagram(id=diagram_id, title=title, type="sequence")

    def apply(op: str, **args: object) -> None:
        ops.apply(d, op, args)

    apply("add_lifeline", name=_("Kunde"), actor=True, id="kunde")
    apply("add_lifeline", name="Shop", type="WebApp", id="shop")
    apply("add_lifeline", name=_("Zahlung"), type="PaymentService", id="zahlung")
    apply("add_lifeline", name=_("Bestellung"), type="Order", id="bestellung")
    apply("add_message", sender="kunde", receiver="shop", name=_("checkout(warenkorb)"), id="m1")
    apply("add_message", sender="shop", receiver="shop", name=_("validieren()"), id="m2")
    apply("add_message", sender="shop", receiver="zahlung", name=_("bezahlen(betrag)"), id="m3")
    apply("add_message", sender="zahlung", receiver="shop", name=_("bestätigt"), sort="reply", id="m4")
    apply("add_message", sender="shop", receiver="bestellung", name=_("anlegen"), sort="createMessage", id="m5")
    apply("add_combined_fragment", operator="alt", guard=_("Zahlung ok"), contents=["m3", "m4", "m5"], id="cf1")
    apply("add_interaction_operand", fragment="cf1", guard="else")
    apply(
        "add_message",
        sender="shop",
        receiver="kunde",
        name=_("Fehler anzeigen"),
        sort="asynchSignal",
        operand="op2",
        id="m6",
    )
    apply(
        "add_combined_fragment", operator="loop", guard=_("für jede Position"), covered=["shop", "bestellung"], id="cf2"
    )
    apply(
        "add_message",
        sender="shop",
        receiver="bestellung",
        name=_("position(artikel)"),
        sort="asynchCall",
        operand="op3",
        id="m7",
    )
    apply("add_interaction_use", refers_to=_("Versand beauftragen"), covered=["shop", "bestellung"], id="ref1")
    apply("add_message", sender="shop", receiver="kunde", name=_("Bestellbestätigung"), sort="reply", id="m8")
    apply("add_message", receiver="shop", name=_("Sitzung abgelaufen"), sort="asynchSignal", id="m9")
    apply("add_message", sender="shop", name=_("Audit-Ereignis"), sort="asynchSignal", id="m10")
    apply("add_message", sender="shop", receiver="bestellung", name=_("verwerfen"), sort="deleteMessage", id="m11")
    apply("add_execution_specification", lifeline="shop", start="m1", finish="m8")
    apply("add_execution_specification", lifeline="zahlung", start="m3", finish="m4")
    return d


TEMPLATES = {
    "empty": lambda diagram_id, title: Diagram(id=diagram_id, title=title),
    "demo": demo_diagram,
    "hub-spoke": hub_spoke_diagram,
    "aks-database": aks_database_diagram,
    "event-driven": event_driven_diagram,
    "order-process": order_process_diagram,
    "order-lifecycle": order_lifecycle_diagram,
    "webshop-domain": webshop_domain_diagram,
    "webshop-use-cases": webshop_use_cases_diagram,
    "webshop-components": webshop_components_diagram,
    "checkout": checkout_diagram,
}

# Diagram type of each template; None = any type (`empty` creates an empty diagram of the requested type).
TEMPLATE_TYPES: dict[str, str | None] = {
    "empty": None,
    "demo": "deployment",
    "hub-spoke": "deployment",
    "aks-database": "deployment",
    "event-driven": "deployment",
    "order-process": "activity",
    "order-lifecycle": "state_machine",
    "webshop-domain": "class",
    "webshop-use-cases": "use_case",
    "webshop-components": "component",
    "checkout": "sequence",
}


def create_from_template(template: str, diagram_id: str, title: str, diagram_type: str | None = None) -> Diagram:
    """A new diagram from `template`; KeyError for an unknown template, ValueError for a type mismatch."""
    factory = TEMPLATES[template]
    template_type = TEMPLATE_TYPES[template]
    if template_type is None:
        return Diagram(id=diagram_id, title=title, type=diagram_type or "deployment")
    if diagram_type is not None and diagram_type != template_type:
        raise ValueError(f"Template '{template}' creates a {template_type} diagram, not a {diagram_type} diagram.")
    return factory(diagram_id, title)


def templates_of(diagram_type: str) -> list[str]:
    """Templates offered for a diagram type, `empty` last."""
    names = sorted(n for n, t in TEMPLATE_TYPES.items() if t == diagram_type)
    return [*names, "empty"]
