"""Text helpers shared by the PlantUML writers (spec: docs/architecture/specs/diagram-exchange.md#plantuml).

Pure: no package imports, so `exchange` and `plantuml_graph` can both use it.
"""

from __future__ import annotations

# Characters PlantUML would interpret (creole markup, HTML-like tags, string end, line breaks, preprocessor
# functions such as `%date()`), checked against PlantUML 1.2026.8: some become numeric entities, doubled markup
# characters get the `~` escape.
ENTITIES = {"<": "&#60;", "&": "&#38;", '"': "&#34;", "~": "&#126;", "%": "&#37;", "\\": "\\\\"}
DOUBLED_MARKUP = "*/_-[="


def plain(text: str, reserved: str = "") -> str:
    """One-line text shown literally by PlantUML in a quoted name, a title or an edge label; the characters in
    `reserved` (syntax of the surrounding construct) become numeric entities too."""
    text = " ".join(text.split())
    out: list[str] = []
    for i, char in enumerate(text):
        doubled = text[i - 1 : i] == char or text[i + 1 : i + 2] == char
        if char in reserved:
            out.append(f"&#{ord(char)};")
        elif char in ENTITIES:
            out.append(ENTITIES[char])
        elif char in DOUBLED_MARKUP and doubled:
            out.append("&#61;" if char == "=" else "~" + char)
        else:
            out.append(char)
    return "".join(out)


# How deep PlantUML exports nest (containers, combined fragments, if/switch/fork structures); deeper diagrams are
# refused with an ExchangeError (HTTP 400) instead of running into Python's recursion limit.
MAX_PLANTUML_NESTING = 150


class TooDeep(ValueError):
    """A structure nested deeper than `MAX_PLANTUML_NESTING`; `what` names it, `element_id` where it was found."""

    def __init__(self, what: str, element_id: str):
        super().__init__(f"{what} nested too deeply at '{element_id}'")
        self.what = what
        self.element_id = element_id
