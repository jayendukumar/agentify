"""Epic 39, US39.4: checking a case's input against the agent's input schema.

An input field's `format` is free text written when the blueprint was
generated ("string", "number", "date (YYYY-MM-DD)", "PDF", "list of line
items"). Only a format that names a type outright is checked as that type;
anything else ("currency code", "phone number", "PDF") takes any value,
because guessing a type from a description would refuse cases the twin
passed. Every field must be present and not null either way.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable

from app.schemas.agents import AgentDefinition


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_integer(v: Any) -> bool:
    return _is_number(v) and float(v).is_integer()


def _parses(parse: Callable[[str], Any]) -> Callable[[Any], bool]:
    def check(v: Any) -> bool:
        try:
            parse(v)
        except (TypeError, ValueError):
            return False
        return isinstance(v, str)
    return check


_TYPES: dict[str, tuple[str, Callable[[Any], bool]]] = {
    "string": ("text", lambda v: isinstance(v, str)),
    "text": ("text", lambda v: isinstance(v, str)),
    "number": ("a number", _is_number),
    "numeric": ("a number", _is_number),
    "decimal": ("a number", _is_number),
    "float": ("a number", _is_number),
    "integer": ("a whole number", _is_integer),
    "int": ("a whole number", _is_integer),
    "boolean": ("true or false", lambda v: isinstance(v, bool)),
    "bool": ("true or false", lambda v: isinstance(v, bool)),
    "date": ("an ISO date (YYYY-MM-DD)", _parses(date.fromisoformat)),
    "datetime": ("an ISO date and time", _parses(datetime.fromisoformat)),
    "date-time": ("an ISO date and time", _parses(datetime.fromisoformat)),
    "timestamp": ("an ISO date and time", _parses(datetime.fromisoformat)),
    "object": ("a JSON object", lambda v: isinstance(v, dict)),
    "json object": ("a JSON object", lambda v: isinstance(v, dict)),
    "list": ("a list", lambda v: isinstance(v, list)),
    "array": ("a list", lambda v: isinstance(v, list)),
}


def expected_type(fmt: str) -> tuple[str, Callable[[Any], bool]] | None:
    """The type a format names outright, or None to take any value."""
    normal = re.sub(r"\(.*?\)", "", fmt or "").strip().lower()
    if normal.startswith(("list of ", "array of ")):
        return _TYPES["list"]
    return _TYPES.get(normal)


def input_problems(definition: AgentDefinition, data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["the input must be a JSON object"]
    problems = []
    for field in definition.input_schema:
        if data.get(field.name) is None:
            problems.append(f"missing input field '{field.name}'")
            continue
        expected = expected_type(field.format)
        if expected is not None and not expected[1](data[field.name]):
            problems.append(f"input field '{field.name}' must be {expected[0]} ({field.format})")
    return problems
