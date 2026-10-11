"""Epic 27, US27.5/US27.9 and Epic 28's step 1: deterministic
applicability. Whether an entity applies to a process scope comes from
its structured `applies_to`, never from retrieval ranking (strategy doc,
design choice 2).

Rule per dimension: an empty list on the entity means "no restriction";
otherwise at least one value must match the scope. Where the *scope*
leaves a dimension empty, the dimensions split in two:

- Business unit and jurisdiction are strict. An organization-wide scope
  sees only organization-wide entities, so an unscoped (pre-Epic 27)
  process keeps working unchanged rather than suddenly receiving every
  jurisdiction's rules.
- Data classes, process type, and systems are lenient. They are often
  unknown when a process is first modelled, and a missed "this handles
  personal data" rule is worse than an extra one, so unknown does not
  exclude.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from app.schemas.context import AppliesTo, ProcessScope

from .jurisdictions import builtin_parent, normalize_code


def jurisdiction_ancestors(code: str, parent_of: Callable[[str], str | None]) -> list[str]:
    """`code` plus every ancestor ("US-CA" -> ["US-CA", "US"]). Cycle-safe."""
    chain: list[str] = []
    current: str | None = normalize_code(code)
    while current and current not in chain:
        chain.append(current)
        current = parent_of(current)
    return chain


def make_parent_lookup(declared: dict[str, str | None]) -> Callable[[str], str | None]:
    """Declared jurisdiction entities win over the built-in table, so a
    user can re-parent a code."""

    def parent_of(code: str) -> str | None:
        if code in declared:
            parent = declared[code]
            return normalize_code(parent) if parent else None
        return builtin_parent(code)

    return parent_of


def _dimension_matches(required: Iterable[str], present: Iterable[str]) -> bool:
    required = set(required)
    return not required or bool(required & set(present))


def applies(applies_to: AppliesTo, scope: ProcessScope, parent_of: Callable[[str], str | None], systems: Iterable[str] = ()) -> bool:
    if applies_to.business_units and (scope.business_unit_id is None or scope.business_unit_id not in applies_to.business_units):
        return False
    if applies_to.jurisdictions:
        expanded: set[str] = set()
        for code in scope.jurisdictions:
            expanded.update(jurisdiction_ancestors(code, parent_of))
        if not expanded & {normalize_code(c) for c in applies_to.jurisdictions}:
            return False
    if applies_to.process_types and scope.process_type and scope.process_type not in applies_to.process_types:
        return False
    if applies_to.data_classes and scope.data_classes and not _dimension_matches(
        applies_to.data_classes, scope.data_classes
    ):
        return False
    systems = list(systems)
    if applies_to.systems and systems and not _dimension_matches(
        [s.lower() for s in applies_to.systems], [s.lower() for s in systems]
    ):
        return False
    return True
