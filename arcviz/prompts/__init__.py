"""System prompts of the diagram chat: shared building blocks plus one expert part per diagram type.

Spec: docs/architecture/specs/assistant-prompts.md (ADR-0065); the scope block: topic-scope.md (ADR-0066).
The texts live in `shared/*.txt` and `types/<type>.txt` next to this module so they can be reviewed as
prose. Placeholders are replaced with `str.replace`, never `str.format`, so the texts may contain literal
braces and the summary (user text) is appended verbatim after everything else. Pure text: this module
imports only `arcviz.i18n` (the language line, spec internationalization.md).
"""

from __future__ import annotations

from importlib.resources import files

from ..i18n import language

_ROOT = files(__name__)


def _read(path: str) -> str:
    return _ROOT.joinpath(path).read_text(encoding="utf-8").strip()


PERSONA = _read("shared/persona.txt")
TOPIC_SCOPE = _read("shared/scope.txt")
WORKING_RULES = _read("shared/working.txt")
MODELLING_HINTS = _read("shared/hints.txt")
READABILITY = _read("shared/readability.txt")  # spec assistant-prompts.md: structure the automatic layout can draw
REPLY_STYLE = _read("shared/reply.txt")

# In prompt order; other prompts (e.g. a project chat) reuse them through `compose`.
SHARED_BLOCKS: tuple[str, ...] = (PERSONA, TOPIC_SCOPE, WORKING_RULES, MODELLING_HINTS, READABILITY, REPLY_STYLE)

# diagram type -> expert part (notation, modelling guidance, guided start, examples); may contain {kinds}
EXPERT_PARTS: dict[str, str] = {
    name: _read(f"types/{name}.txt")
    for name in ("deployment", "activity", "sequence", "state_machine", "class", "use_case", "component")
}

# The project chat's own part (milestone M17, spec project-chat.md): after the shared blocks, before the project.
PROJECT_PART = _read("project/chat.txt")

# The requirements chat's own part (milestone M31, spec requirements-workshop.md): after a short persona, the reply style.
REQUIREMENTS_PERSONA = (
    "You are Arcviz, an experienced software architect and requirements engineer. You help the user to bring "
    "requirements into a clear, testable form and to derive architecture drivers and quality scenarios from them."
)
REQUIREMENTS_PART = _read("project/requirements.txt")

# How to work with the shared project model (milestone M18, spec shared-project-model.md): after the stable prefix,
# followed by the project's elements, when the diagram's project has any.
MODEL_PART = _read("shared/model.txt")
MODEL_HEADING = "Project elements:"

# The project's architecture drivers (spec architecture-drivers.md, ADR-0163): after the model, when it has any.
VISION_PART = _read("shared/vision.txt")  # spec project-vision.md, ADR-0203: before the drivers
DRIVERS_PART = _read("shared/drivers.txt")
DRIVERS_HEADING = "Architecture drivers (most important first):"

# Stakeholders and accepted decisions (spec decision-support.md, ADR-0168): after the drivers, when there are any.
STAKEHOLDERS_HEADING = "Stakeholders (most influential first):"
DECISIONS_PART = _read("shared/decisions.txt")
DECISIONS_HEADING = "Accepted architecture decisions (binding, newest first):"

STATE_HEADING = "Current state:"


def language_line() -> str:
    """The reply language of an English interface (empty for German: the reply style already follows the user)."""
    if language() == "en":
        return "The user's interface language is English: answer in English unless the user clearly writes in another language."
    return ""


def _language_blocks() -> tuple[str, ...]:
    line = language_line()
    return (line,) if line else ()


def compose(*blocks: str) -> str:
    """Join prompt blocks with one blank line between them."""
    return "\n\n".join(block.strip() for block in blocks)


def prompt_prefix(diagram_type: str, kinds: str) -> str:
    """The stable part of a diagram type's prompt: it depends only on the type and the kind list."""
    return compose(*SHARED_BLOCKS, EXPERT_PARTS[diagram_type].replace("{kinds}", kinds), *_language_blocks())


def project_prompt_prefix() -> str:
    """The stable part of the project chat's prompt: the shared blocks, then the project part."""
    return compose(*SHARED_BLOCKS, PROJECT_PART, *_language_blocks())


def requirements_prompt_prefix() -> str:
    """The stable part of the requirements chat's prompt (the requirements are read with tools, not listed)."""
    return compose(REQUIREMENTS_PERSONA, REPLY_STYLE, REQUIREMENTS_PART, *_language_blocks())


def diagram_part(diagram_type: str, kinds: str, summary: str) -> str:
    """A diagram type's expert part and the diagram's state, without the shared blocks: the active diagram in the
    project chat (its prompt already starts with the shared blocks)."""
    return f"{EXPERT_PARTS[diagram_type].replace('{kinds}', kinds).strip()}\n\n{STATE_HEADING}\n{summary}\n"


def model_part(elements: str) -> str:
    """The shared-model block and the project's elements (one line each), placed after the stable prefix."""
    return f"{MODEL_PART}\n\n{MODEL_HEADING}\n{elements}"


def drivers_part(drivers: str) -> str:
    """The drivers block and the project's drivers (one line each, most important first)."""
    return f"{DRIVERS_PART}\n\n{DRIVERS_HEADING}\n{drivers}"


def architecture_part(
    drivers: str | None,
    stakeholders: str | None = None,
    decisions: str | None = None,
    vision: str | None = None,
) -> str | None:
    """The vision, the drivers (with their core scenarios), the stakeholders and the accepted decisions, each block only
    when the project has some; None for none (spec: decision-support.md, project-vision.md)."""
    blocks = [f"{VISION_PART}\n\n{vision}"] if vision else []
    if drivers:
        blocks.append(drivers_part(drivers))
    if stakeholders:
        blocks.append(f"{STAKEHOLDERS_HEADING}\n{stakeholders}")
    if decisions:
        blocks.append(f"{DECISIONS_PART}\n\n{DECISIONS_HEADING}\n{decisions}")
    return compose(*blocks) if blocks else None


def project_part(model: str | None, architecture: str | None) -> str:
    """The project's model and its architecture block (`architecture_part`), each only when there is one."""
    return compose(*([model_part(model)] if model else []), *([architecture] if architecture else []))


def system_prompt(
    diagram_type: str, kinds: str, summary: str, model: str | None = None, architecture: str | None = None
) -> str:
    """The full system prompt: the stable prefix, the project's model and architecture block (if any), then the
    current diagram summary (kept last for caching)."""
    project = project_part(model, architecture)
    middle = f"{project}\n\n" if project else ""
    return f"{prompt_prefix(diagram_type, kinds)}\n\n{middle}{STATE_HEADING}\n{summary}\n"
