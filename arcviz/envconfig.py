"""Reading start-up variables: one helper that names the variable, the expected format and the value.

Spec: docs/architecture/specs/operations.md §1 (ADR-0106). `Env` records a problem instead of raising and returns the
default, so a `from_env` can report every malformed variable of its subsystem at once (`check()`); `create_app`
merges the `ConfigError`s of all subsystems into one. No package imports: `limits`, `live` and `llm/` use it.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
from collections.abc import Iterable, Mapping, Sequence


class ConfigError(ValueError):
    """Malformed configuration: one sentence per problem, each naming its variable."""

    def __init__(self, problems: Iterable[str]):
        self.problems: tuple[str, ...] = tuple(problems)
        count = len(self.problems)
        heading = "Invalid configuration:" if count == 1 else f"Invalid configuration ({count} problems):"
        super().__init__("\n".join([heading, *(f"  - {problem}" for problem in self.problems)]))

    @classmethod
    def merge(cls, errors: Iterable[ConfigError]) -> ConfigError:
        return cls(problem for error in errors for problem in error.problems)


class Env:
    """Typed reads of environment variables that collect their problems (default: `os.environ`)."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = os.environ if environ is None else environ
        self.problems: list[str] = []

    def problem(self, message: str) -> None:
        """A problem the caller found (for example a combination of variables)."""
        self.problems.append(message)

    def check(self) -> None:
        if self.problems:
            raise ConfigError(self.problems)

    def raw(self, name: str) -> str:
        return self.environ.get(name, "").strip()

    def text(self, name: str, default: str = "") -> str:
        return self.raw(name) or default

    def flag(self, name: str, default: bool = False) -> bool:
        raw = self.raw(name).lower()
        if not raw:
            return default
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        self.problem(f"{name} must be 1/true/yes or 0/false/no, got {raw!r}.")
        return default

    def choice(self, name: str, choices: Sequence[str], default: str) -> str:
        raw = self.raw(name)
        if not raw:
            return default
        value = raw.lower()
        if value not in choices:
            self.problem(f"{name} must be one of {', '.join(choices)}, got {raw!r}.")
            return default
        return value

    def integer(self, name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
        value = self.optional_integer(name, minimum=minimum, maximum=maximum)
        return default if value is None else value

    def optional_integer(self, name: str, *, minimum: int = 0, maximum: int | None = None) -> int | None:
        raw = self.raw(name)
        if not raw:
            return None
        digits = raw[1:] if raw[:1] in "+-" else raw
        value = int(raw) if digits.isascii() and digits.isdigit() else None
        if value is None or value < minimum or (maximum is not None and value > maximum):
            self.problem(f"{name} must be {_integer(minimum, maximum)}, got {raw!r}.")
            return None
        return value

    def number(self, name: str, default: float, *, minimum: float = 0.0, unit: str | None = None) -> float:
        value = self.optional_number(name, minimum=minimum, unit=unit)
        return default if value is None else value

    def optional_number(self, name: str, *, minimum: float = 0.0, unit: str | None = None) -> float | None:
        raw = self.raw(name)
        if not raw:
            return None
        try:
            value: float | None = float(raw)
        except ValueError:
            value = None
        if value is None or not math.isfinite(value) or value < minimum:
            self.problem(f"{name} must be {_number(minimum, unit)}, got {raw!r}.")
            return None
        return value


def _integer(minimum: int, maximum: int | None) -> str:
    if maximum is not None:
        return f"an integer from {minimum} to {maximum}"
    return "a non-negative integer" if minimum == 0 else f"an integer of at least {minimum}"


def _number(minimum: float, unit: str | None) -> str:
    noun = f"a number of {unit}" if unit else "a number"
    if minimum == 0:
        return f"a non-negative number of {unit}" if unit else "a non-negative number"
    return f"{noun} of at least {minimum:g}"


def installed(module: str) -> bool:
    """Whether `module` can be imported, without importing it (a missing parent package counts as not installed)."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # ValueError: imported without a spec (a stand-in module)
        return module in sys.modules
