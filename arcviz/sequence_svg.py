"""Render a sequence diagram (UML 2.5.1 Interaction) as an SVG document in UML notation.

Spec: docs/architecture/specs/sequence-diagrams.md, ADR-0032/ADR-0035. Every element is a `<g>` with
`data-id` (the model id) and `data-element` (its UML kind), so the UI maps clicks, highlights and marks
directly. All text is XML-escaped here; the document is self-contained (scoped style, markers).

Layout: lifelines are columns whose spacing comes from estimated text widths; fragments are rows top to
bottom; combined fragments and interaction uses are frames around the lifelines they cover (and the ones
their contents touch), inset by nesting depth. The `viewBox` covers everything drawn.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .model import Diagram, single_line
from .sequence import (
    CombinedFragment,
    DestructionOccurrenceSpecification,
    ExecutionSpecification,
    FragmentNode,
    Interaction,
    InteractionUse,
    Lifeline,
    Message,
    messages,
    walk,
)

MARKS = ("added", "removed", "changed")

HEAD_H = 34
ACTOR_H = 72  # stick figure (50) and the name below it
TOP = 10
ROW = 34  # height of a message row (label above the arrow)
FOUND_MIN = 70  # length of found/lost messages
BAR_W = 10
BAR_STEP = 6

STYLE = """
.arcviz-seq{--fg:#1f2933;--line:#7b8794;--head:#eef4ff;--head-stroke:#2b5fc7;--frame:#52606d;--bg:#ffffff;--bar:#f5f7fa}
@media (prefers-color-scheme: dark){.arcviz-seq{--fg:#e4e7eb;--line:#9aa5b1;--head:#1f2a44;--head-stroke:#7aa2f7;--frame:#9aa5b1;--bg:#151a21;--bar:#2a313c}}
.arcviz-seq text{fill:var(--fg);font:13px sans-serif}
.arcviz-seq .head{fill:var(--head);stroke:var(--head-stroke);stroke-width:1.2}
.arcviz-seq .lifeline-line{stroke:var(--line);stroke-dasharray:6 4}
.arcviz-seq .msg{stroke:var(--fg);fill:none;stroke-width:1.3}
.arcviz-seq .hit{stroke:transparent;stroke-width:12;fill:none}
.arcviz-seq .filled{fill:var(--fg)}
.arcviz-seq .open{fill:none;stroke:var(--fg);stroke-width:1.3}
.arcviz-seq .dot{fill:var(--fg)}
.arcviz-seq .frame{fill:none;stroke:var(--frame);stroke-width:1.2}
.arcviz-seq .pentagon{fill:var(--bg);stroke:var(--frame)}
.arcviz-seq .operand-area{fill:transparent}
.arcviz-seq .separator{stroke:var(--frame);stroke-dasharray:5 4}
.arcviz-seq .use{fill:var(--bg);stroke:var(--frame);stroke-width:1.2}
.arcviz-seq .bar{fill:var(--bar);stroke:var(--line)}
.arcviz-seq .destroy{stroke:var(--fg);stroke-width:2;fill:none}
.arcviz-seq .actor{fill:none;stroke:var(--fg);stroke-width:1.5}
.arcviz-seq .keyword{font-weight:bold}
.arcviz-seq .changed :is(.head,.msg,.frame,.use,.bar,.actor,.destroy){stroke:#e0a100;stroke-width:3}
.arcviz-seq .added :is(.head,.msg,.frame,.use,.bar,.actor,.destroy){stroke:#2e7d32;stroke-width:3}
.arcviz-seq .removed{opacity:.55}
.arcviz-seq .removed :is(.head,.msg,.frame,.use,.bar,.actor,.destroy){stroke:#c62828;stroke-dasharray:6 4}
"""

MARKERS = (
    '<marker id="arcviz-seq-filled" viewBox="0 0 10 10" refX="10" refY="5" markerUnits="userSpaceOnUse" markerWidth="11" markerHeight="11" '
    'orient="auto-start-reverse"><path class="filled" d="M0,0 L10,5 L0,10 z"/></marker>'
    '<marker id="arcviz-seq-open" viewBox="0 0 10 10" refX="10" refY="5" markerUnits="userSpaceOnUse" markerWidth="11" markerHeight="11" '
    'orient="auto-start-reverse"><path class="open" d="M0,0 L10,5 L0,10"/></marker>'
)

# MessageSort -> (dashed line, filled head) in UML notation (17.12.23).
_NOTATION = {
    "synchCall": (False, True),
    "asynchCall": (False, False),
    "asynchSignal": (False, False),
    "reply": (True, False),
    "createMessage": (True, False),
    "deleteMessage": (False, True),
}


def escape(text: str) -> str:
    """XML-escape text for element content and attribute values. Control characters are normalised first like the
    operations do (`single_line`, #103): XML 1.0 has no form for most of them, so stored or imported ones would make
    the document ill-formed."""
    return (
        single_line(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def text_width(text: str) -> float:
    """Generous estimate of the width of `text` at 13px sans-serif."""
    width = 0.0
    for char in text:
        if unicodedata.east_asian_width(char) in ("W", "F"):
            width += 13.5
        elif char in "il.,:;'|!" or char == " ":
            width += 4.2
        elif char.isupper() or char in "mwMW@%&":
            width += 9.6
        else:
            width += 7.4
    return width


def n(value: float) -> str:
    return f"{round(value, 1):g}"


def head_label(lifeline: Lifeline) -> str:
    return f"{lifeline.name} : {lifeline.type}" if lifeline.type else lifeline.name


def head_width(lifeline: Lifeline) -> float:
    return 24.0 if lifeline.actor else max(80.0, text_width(head_label(lifeline)) + 24)


@dataclass
class _Frame:
    fragment: CombinedFragment | InteractionUse
    top: float
    bottom: float
    depth: int
    operand_tops: list[float] = field(default_factory=list)  # combined fragments: top of each operand


@dataclass
class _Layout:
    x: dict[str, float]
    lifelines: dict[str, Lifeline]
    head_y: dict[str, float]  # top of each lifeline head
    end_y: dict[str, float]  # where a lifeline's line ends (destruction), if earlier than the bottom
    creates: dict[str, str] = field(default_factory=dict)  # createMessage id -> lifeline whose head it reaches
    msg_y: dict[str, float] = field(default_factory=dict)
    frames: list[_Frame] = field(default_factory=list)
    destructions: list[tuple[DestructionOccurrenceSpecification, float]] = field(default_factory=list)
    bottom: float = 0.0


class _Bounds:
    def __init__(self) -> None:
        self.min_x = self.min_y = float("inf")
        self.max_x = self.max_y = float("-inf")

    def see(self, x: float, y: float) -> None:
        self.min_x, self.max_x = min(self.min_x, x), max(self.max_x, x)
        self.min_y, self.max_y = min(self.min_y, y), max(self.max_y, y)

    def text(self, x: float, y: float, label: str, anchor: str = "middle") -> None:
        width = text_width(label)
        left = x - width / 2 if anchor == "middle" else x
        self.see(left, y - 13)
        self.see(left + width, y + 4)


def _columns(interaction: Interaction) -> dict[str, float]:
    lifelines = interaction.lifelines
    index = {lf.id: i for i, lf in enumerate(lifelines)}
    widths = [
        max(50.0, text_width(lf.name) + 10) if lf.actor else max(80.0, text_width(head_label(lf)) + 24)
        for lf in lifelines
    ]
    gaps = [max((widths[i] + widths[i + 1]) / 2 + 24, 110.0) for i in range(len(lifelines) - 1)]

    def need(a: int, b: int, width: float) -> None:
        if a >= b or b > len(gaps):
            return
        deficit = width - sum(gaps[a:b])
        if deficit > 0:
            for i in range(a, b):
                gaps[i] += deficit / (b - a)

    for message in messages(interaction):
        label = text_width(message.name)
        if message.sender and message.receiver:
            a, b = sorted((index[message.sender], index[message.receiver]))
            if a == b:
                need(a, a + 1, label + 64)
            else:
                need(a, b, label + 40)
        elif message.receiver:  # found: comes from the left
            j = index[message.receiver]
            if j > 0:
                need(j - 1, j, max(FOUND_MIN, label + 30) + 40)
        elif message.sender:  # lost: goes to the right
            i = index[message.sender]
            need(i, i + 1, max(FOUND_MIN, label + 30) + 40)
    x, positions = 0.0, {}
    for i, lifeline in enumerate(lifelines):
        positions[lifeline.id] = x
        if i < len(gaps):
            x += gaps[i]
    return positions


def _layout(interaction: Interaction) -> _Layout:
    created: dict[str, str] = {}  # lifeline -> id of the createMessage that creates it (if it is its first message)
    seen: set[str] = set()
    for message in messages(interaction):
        if message.sort == "createMessage" and message.receiver and message.receiver not in seen:
            created[message.receiver] = message.id
        seen.update(end for end in (message.sender, message.receiver) if end)
    head_h = ACTOR_H if any(lf.actor for lf in interaction.lifelines) else HEAD_H
    layout = _Layout(
        x=_columns(interaction), lifelines={lf.id: lf for lf in interaction.lifelines}, head_y={}, end_y={}
    )
    for lifeline in interaction.lifelines:
        layout.head_y[lifeline.id] = TOP
    creators = {mid: lifeline for lifeline, mid in created.items()}
    layout.creates = creators
    y = TOP + head_h + 20

    def place(fragments: list[FragmentNode], y: float, depth: int) -> float:
        for fragment in fragments:
            if isinstance(fragment, Message):
                if fragment.id in creators:
                    arrow = y + HEAD_H / 2 + 8
                    layout.head_y[creators[fragment.id]] = arrow - HEAD_H / 2
                    y = arrow + HEAD_H / 2 + 12
                elif fragment.sender is not None and fragment.sender == fragment.receiver:
                    arrow = y + 22
                    y = arrow + 18 + 14
                else:
                    arrow = y + 22
                    y = arrow + 12
                layout.msg_y[fragment.id] = arrow
                if fragment.sort == "deleteMessage" and fragment.receiver:
                    layout.end_y.setdefault(fragment.receiver, arrow)
            elif isinstance(fragment, CombinedFragment):
                frame = _Frame(fragment, top=y + 8, bottom=0, depth=depth)
                y = frame.top + 26
                for k, operand in enumerate(fragment.operands):
                    if k:
                        y += 6
                        frame.operand_tops.append(y)
                        y += 22
                    else:
                        frame.operand_tops.append(frame.top)
                    before = y
                    y = place(operand.fragments, y, depth + 1)
                    if y == before:
                        y += 14  # an empty operand keeps some height
                    y += 4
                frame.bottom = y + 4
                layout.frames.append(frame)
                y = frame.bottom + 6
            elif isinstance(fragment, InteractionUse):
                frame = _Frame(fragment, top=y + 10, bottom=y + 10 + 36, depth=depth)
                layout.frames.append(frame)
                y = frame.bottom + 6
            else:
                at = y + 16
                layout.destructions.append((fragment, at))
                layout.end_y.setdefault(fragment.lifeline, at)
                y = at + 10
        return y

    layout.bottom = place(interaction.fragments, y, 0) + 20
    return layout


def _nesting(fragment: CombinedFragment | InteractionUse) -> int:
    """Levels of frames from this one down to its deepest nested frame (1 = no nested frame)."""
    if isinstance(fragment, InteractionUse):
        return 1
    inner = [f for op in fragment.operands for f in op.fragments if isinstance(f, CombinedFragment | InteractionUse)]
    return 1 + max((_nesting(f) for f in inner), default=0)


def _frame_extent(fragment: CombinedFragment | InteractionUse, x: Mapping[str, float]) -> tuple[float, float]:
    xs = [x[c] for c in fragment.covered if c in x]
    if isinstance(fragment, CombinedFragment):
        for op in fragment.operands:
            for inner, _, _ in walk(op.fragments):
                if isinstance(inner, Message):
                    label = text_width(inner.name)
                    if inner.sender is not None and inner.sender in x:
                        xs.append(x[inner.sender])
                        if inner.receiver == inner.sender:
                            xs.append(x[inner.sender] + 44 + label)
                        elif inner.receiver is None:
                            xs.append(x[inner.sender] + max(FOUND_MIN, label + 30))
                    if inner.receiver is not None and inner.receiver in x:
                        xs.append(x[inner.receiver])
                        if inner.sender is None:
                            xs.append(x[inner.receiver] - max(FOUND_MIN, label + 30))
                elif isinstance(inner, CombinedFragment | InteractionUse):
                    xs.extend(x[c] for c in inner.covered if c in x)
    if not xs:
        return 0.0, 0.0
    inset = 14 + 10 * (_nesting(fragment) - 1)
    return min(xs) - inset - 6, max(xs) + inset + 6


def _bars(interaction: Interaction, layout: _Layout) -> list[tuple[ExecutionSpecification, float, float, int]]:
    """(execution, top, bottom, nesting level) for every execution specification that can be placed."""
    last: dict[str, float] = {}
    for message in messages(interaction):
        for end in (message.sender, message.receiver):
            if end and message.id in layout.msg_y:
                last[end] = max(last.get(end, 0.0), layout.msg_y[message.id])
    spans: list[tuple[ExecutionSpecification, float, float]] = []
    for execution in interaction.executions:
        if execution.start not in layout.msg_y or execution.lifeline not in layout.x:
            continue
        top = layout.msg_y[execution.start]
        bottom = layout.msg_y.get(execution.finish or "", last.get(execution.lifeline, top))
        spans.append((execution, top, max(bottom, top + 16)))

    def contains(j: int, i: int) -> bool:
        """Span j encloses span i (identical spans: the earlier one encloses the later)."""
        (other, o_top, o_bottom), (execution, top, bottom) = spans[j], spans[i]
        if j == i or other.lifeline != execution.lifeline or o_top > top or o_bottom < bottom:
            return False
        return o_top < top or o_bottom > bottom or j < i

    return [
        (e, top, bottom, sum(contains(j, i) for j in range(len(spans)))) for i, (e, top, bottom) in enumerate(spans)
    ]


def render(
    diagram: Diagram,
    highlight: Iterable[str] = (),
    marks: Mapping[str, str] | None = None,
) -> str:
    """SVG document of the diagram's interaction; `highlight` ids get class `changed`, `marks` their mark."""
    interaction = diagram.interaction or Interaction()
    classes: dict[str, str] = {h: "changed" for h in highlight}
    classes.update({k: v for k, v in (marks or {}).items() if v in MARKS})
    layout = _layout(interaction)
    bounds = _Bounds()
    x = layout.x
    bars = _bars(interaction, layout)
    head_h = {lf.id: (ACTOR_H if lf.actor else HEAD_H) for lf in interaction.lifelines}

    def group(element_id: str, element: str, cls: str, body: list[str]) -> str:
        extra = f" {classes[element_id]}" if element_id in classes else ""
        return (
            f'<g class="{escape(cls + extra)}" data-id="{escape(element_id)}" data-element="{element}">'
            + "".join(body)
            + "</g>"
        )

    def attach(lifeline: str, y: float, toward_right: bool) -> float:
        """x where a message end meets `lifeline` at `y`: the edge of its outermost active bar, if any."""
        active = [level for e, top, bottom, level in bars if e.lifeline == lifeline and top - 0.5 <= y <= bottom + 0.5]
        if not active:
            return x[lifeline]
        level = max(active)
        return x[lifeline] + level * BAR_STEP + (BAR_W / 2 if toward_right else -BAR_W / 2)

    # ---- lifelines ------------------------------------------------------------------------------
    lifeline_svg: list[str] = []
    for lifeline in interaction.lifelines:
        cx, top = x[lifeline.id], layout.head_y[lifeline.id]
        h = head_h[lifeline.id]
        body: list[str] = []
        end = layout.end_y.get(lifeline.id, layout.bottom)
        body.append(
            f'<line class="lifeline-line" x1="{n(cx)}" y1="{n(top + h)}" x2="{n(cx)}" y2="{n(max(end, top + h))}"/>'
        )
        if lifeline.actor:
            figure = (
                f"M{n(cx)},{n(top + 16)} m-8,0 a8,8 0 1,0 16,0 a8,8 0 1,0 -16,0 "
                f"M{n(cx)},{n(top + 16)} m0,8 v14 M{n(cx - 11)},{n(top + 30)} h22 "
                f"M{n(cx)},{n(top + 38)} l-9,12 M{n(cx)},{n(top + 38)} l9,12"
            )
            body.append(f'<path class="actor" d="{figure}"/>')
            body.append(
                f'<text x="{n(cx)}" y="{n(top + 66)}" text-anchor="middle">{escape(head_label(lifeline))}</text>'
            )
            bounds.text(cx, top + 66, head_label(lifeline))
            bounds.see(cx - 12, top + 4)
        else:
            width = head_width(lifeline)
            body.append(
                f'<rect class="head" x="{n(cx - width / 2)}" y="{n(top)}" width="{n(width)}" height="{n(h)}" rx="3"/>'
            )
            body.append(
                f'<text x="{n(cx)}" y="{n(top + h / 2 + 4.5)}" text-anchor="middle">{escape(head_label(lifeline))}</text>'
            )
            bounds.see(cx - width / 2, top)
            bounds.see(cx + width / 2, top + h)
        bounds.see(cx, end)
        lifeline_svg.append(group(lifeline.id, "lifeline", "lifeline", body))

    # ---- frames: combined fragments (with operands) and interaction uses ---------------------------
    frame_svg: list[str] = []
    for frame in sorted(layout.frames, key=lambda f: f.depth):
        fragment = frame.fragment
        left, right = _frame_extent(fragment, x)
        if isinstance(fragment, InteractionUse):
            label = fragment.refers_to
            right = max(right, left + text_width(label) + 70)
            height = frame.bottom - frame.top
            body = [
                f'<rect class="use" x="{n(left)}" y="{n(frame.top)}" width="{n(right - left)}" height="{n(height)}"/>',
                _pentagon(left, frame.top, "ref"),
                f'<text x="{n((left + right) / 2 + 14)}" y="{n(frame.top + height / 2 + 5)}" text-anchor="middle">{escape(label)}</text>',
            ]
            bounds.see(left, frame.top)
            bounds.see(right, frame.bottom)
            bounds.text((left + right) / 2 + 14, frame.top + height / 2 + 5, label)
            frame_svg.append(group(fragment.id, "interaction_use", "interaction-use", body))
            continue
        keyword = fragment.operator + (f" {{{', '.join(fragment.messages)}}}" if fragment.messages else "")
        first_guard = f"[{fragment.operands[0].guard}]" if fragment.operands and fragment.operands[0].guard else ""
        right = max(right, left + text_width(keyword) + 20 + text_width(first_guard) + 24)
        for operand in fragment.operands[1:]:  # later guards sit at the left edge of their operand
            if operand.guard:
                right = max(right, left + 10 + text_width(f"[{operand.guard}]") + 14)
        body = [
            f'<rect class="frame" x="{n(left)}" y="{n(frame.top)}" width="{n(right - left)}" height="{n(frame.bottom - frame.top)}"/>',
            _pentagon(left, frame.top, keyword),
        ]
        bounds.see(left, frame.top)
        bounds.see(right, frame.bottom)
        for k in range(1, len(fragment.operands)):
            y_sep = frame.operand_tops[k]
            body.append(f'<line class="separator" x1="{n(left)}" y1="{n(y_sep)}" x2="{n(right)}" y2="{n(y_sep)}"/>')
        for k, operand in enumerate(fragment.operands):
            top = frame.operand_tops[k] if k else frame.top + 22
            # A strip along the operand's top edge selects it; its interior stays free for the bars below.
            strip_left = left + (text_width(keyword) + 20 if k == 0 else 0)
            strip_top = frame.top if k == 0 else top - 4
            inner = [
                f'<rect class="operand-area" x="{n(strip_left)}" y="{n(strip_top)}" width="{n(max(right - strip_left, 1))}" height="20"/>'
            ]
            if operand.guard:
                guard = f"[{operand.guard}]"
                gx = left + text_width(keyword) + 30 if k == 0 else left + 10
                gy = frame.top + 15 if k == 0 else top + 16
                inner.append(f'<text class="guard" x="{n(gx)}" y="{n(gy)}">{escape(guard)}</text>')
                bounds.text(gx, gy, guard, anchor="start")
            body.append(group(operand.id, "interaction_operand", "operand", inner))
        frame_svg.append(group(fragment.id, "combined_fragment", "combined-fragment", body))

    # ---- execution specifications ---------------------------------------------------------------------
    bar_svg = []
    for execution, top, bottom, level in sorted(bars, key=lambda b: b[3]):
        cx = x[execution.lifeline] + level * BAR_STEP
        body = [f'<rect class="bar" x="{n(cx - BAR_W / 2)}" y="{n(top)}" width="{BAR_W}" height="{n(bottom - top)}"/>']
        bounds.see(cx + BAR_W, bottom)
        bar_svg.append(group(execution.id, "execution_specification", "execution", body))

    # ---- messages and destructions ------------------------------------------------------------------------
    message_svg: list[str] = []
    for message in messages(interaction):
        message_svg.append(_message(message, layout, attach, bounds, group))
    for destruction, at in layout.destructions:
        if destruction.lifeline in x:
            cx = x[destruction.lifeline]
            body = [
                f'<path class="destroy" d="{_cross(cx, at)}"/>',
                f'<rect class="hit" x="{n(cx - 9)}" y="{n(at - 9)}" width="18" height="18"/>',
            ]
            bounds.see(cx - 9, at - 9)
            bounds.see(cx + 9, at + 9)
            message_svg.append(group(destruction.id, "destruction_occurrence_specification", "destruction", body))

    if bounds.min_x == float("inf"):
        bounds.see(0, 0)
        bounds.see(200, 60)
    pad = 16
    vx, vy = bounds.min_x - pad, bounds.min_y - pad
    width, height = bounds.max_x - bounds.min_x + 2 * pad, bounds.max_y - bounds.min_y + 2 * pad
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="arcviz-seq" viewBox="{n(vx)} {n(vy)} {n(width)} {n(height)}" '
        f'width="{n(width)}" height="{n(height)}" role="img" aria-label="{escape(diagram.title)}">'
        f"<title>{escape(diagram.title)}</title><style>{STYLE}</style><defs>{MARKERS}</defs>"
        f'<g class="lifelines">{"".join(lifeline_svg)}</g>'
        f'<g class="executions">{"".join(bar_svg)}</g>'
        f'<g class="frames">{"".join(frame_svg)}</g>'
        f'<g class="messages">{"".join(message_svg)}</g>'
        "</svg>"
    )


def _pentagon(left: float, top: float, keyword: str) -> str:
    width, height = text_width(keyword) + 18, 20
    d = f"M{n(left)},{n(top)} h{n(width)} v{height - 6} l-6,6 H{n(left)} Z"
    return (
        f'<path class="pentagon" d="{d}"/>'
        f'<text class="keyword" x="{n(left + 6)}" y="{n(top + 14.5)}">{escape(keyword)}</text>'
    )


def _cross(cx: float, cy: float, r: float = 8) -> str:
    return f"M{n(cx - r)},{n(cy - r)} L{n(cx + r)},{n(cy + r)} M{n(cx + r)},{n(cy - r)} L{n(cx - r)},{n(cy + r)}"


def _message(message: Message, layout: _Layout, attach, bounds: _Bounds, group) -> str:
    y = layout.msg_y[message.id]
    dashed, filled = _NOTATION.get(message.sort, (False, True))
    marker = f'marker-end="url(#arcviz-seq-{"filled" if filled else "open"})"'
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    label = escape(message.name)
    label_w = text_width(message.name)
    x = layout.x
    body: list[str] = []
    cls = f"message sort-{message.sort}" + (f" kind-{message.kind}" if message.kind != "complete" else "")
    sender, receiver = message.sender, message.receiver
    if sender is not None and sender == receiver and sender in x:
        x1 = attach(sender, y, True)
        x2 = attach(sender, y + 18, True)
        d = f"M{n(x1)},{n(y)} H{n(x1 + 36)} V{n(y + 18)} H{n(x2)}"
        body.append(f'<path class="hit" d="{d}"/>')
        body.append(f'<path class="msg" d="{d}" {marker}{dash}/>')
        body.append(f'<text x="{n(x1 + 44)}" y="{n(y + 13)}">{label}</text>')
        bounds.text(x1 + 44, y + 13, message.name, anchor="start")
        bounds.see(x1 + 36, y + 18)
        return group(message.id, "message", cls, body)
    length = max(FOUND_MIN, label_w + 30)
    if sender in x and receiver in x:
        right = x[receiver] > x[sender]
        x1 = attach(sender, y, right)
        if layout.creates.get(message.id) == receiver:  # ends at the border of the created lifeline's head
            x2 = x[receiver] + (-1 if right else 1) * head_width(layout.lifelines[receiver]) / 2
        elif message.sort == "deleteMessage":  # the head stops short of the X
            x2 = x[receiver] + (-1 if right else 1) * 9
        else:
            x2 = attach(receiver, y, not right)
    elif receiver in x:  # found
        x2 = attach(receiver, y, False)
        x1 = x2 - length
        body.append(f'<circle class="dot" cx="{n(x1)}" cy="{n(y)}" r="4"/>')
    else:  # lost
        x1 = attach(sender, y, True)
        x2 = x1 + length
        body.append(f'<circle class="dot" cx="{n(x2 + 4)}" cy="{n(y)}" r="4"/>')
    body.insert(0, f'<line class="hit" x1="{n(x1)}" y1="{n(y)}" x2="{n(x2)}" y2="{n(y)}"/>')
    body.append(f'<line class="msg" x1="{n(x1)}" y1="{n(y)}" x2="{n(x2)}" y2="{n(y)}" {marker}{dash}/>')
    if message.sort == "deleteMessage" and receiver in x:
        body.append(f'<path class="destroy" d="{_cross(x[receiver], y)}"/>')
    mid = (x1 + x2) / 2
    body.append(f'<text x="{n(mid)}" y="{n(y - 6)}" text-anchor="middle">{label}</text>')
    bounds.text(mid, y - 6, message.name)
    bounds.see(min(x1, x2) - 6, y)
    bounds.see(max(x1, x2) + 10, y)
    return group(message.id, "message", cls, body)
