"""Epic 33, US33.1/US33.2: the rule language and the rule oracle.

Expressions are a small typed JSON AST -- evaluated here in Python, never
`eval`'d -- so a rule can be shown, edited, and executed safely:

    {"all": [cond, ...]}   every condition holds
    {"any": [cond, ...]}   at least one holds
    {"not": cond}
    {"field": "amount", "op": "gt", "value": 10000}

Operators: eq ne gt gte lt lte in not_in contains missing present.
Numbers in strings ("10,000", "EUR 500") compare as numbers; text
comparisons are case-insensitive.

The oracle walks a process's gateways and, for each, takes the first
confirmed rule (by priority) whose expression holds, falling back to the
gateway's default branch. A rule with an `outcome` determines the case's
expected decision; the most severe determined outcome wins
(reject > escalate > approve), since a case any gate rejects is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "missing", "present"}
_SEVERITY = {"approve": 0, "escalate": 1, "reject": 2}


class RuleSyntaxError(Exception):
    pass


def validate_expression(node: Any, fields: set[str] | None = None, depth: int = 0) -> None:
    """Raises RuleSyntaxError for anything outside the language -- the
    LLM's draft and a person's edit go through the same check."""
    if depth > 8:
        raise RuleSyntaxError("Expression nests too deeply")
    if not isinstance(node, dict):
        raise RuleSyntaxError("Each condition must be an object")
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        items = node[key]
        if not isinstance(items, list) or not items:
            raise RuleSyntaxError(f"'{key}' needs a non-empty list")
        for item in items:
            validate_expression(item, fields, depth + 1)
        return
    if "not" in node:
        validate_expression(node["not"], fields, depth + 1)
        return
    if "field" not in node or "op" not in node:
        raise RuleSyntaxError("A condition needs 'field' and 'op'")
    if node["op"] not in OPS:
        raise RuleSyntaxError(f"Unknown operator '{node['op']}'")
    if fields is not None and node["field"] not in fields:
        raise RuleSyntaxError(f"Unknown field '{node['field']}'")
    if node["op"] in ("in", "not_in") and not isinstance(node.get("value"), list):
        raise RuleSyntaxError(f"'{node['op']}' needs a list value")
    if node["op"] not in ("missing", "present") and "value" not in node:
        raise RuleSyntaxError(f"'{node['op']}' needs a value")


_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _NUMBER.search(value.replace(" ", ""))
        if match:
            try:
                return float(match.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def _numeric_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and _NUMBER.fullmatch(value.replace(" ", "").strip()) is not None


def _eq(a: Any, b: Any) -> bool:
    """Numbers compare as numbers ("10,000" == 10000); everything else as
    case-insensitive text."""
    if _numeric_like(a) and _numeric_like(b):
        return _as_number(a) == _as_number(b)
    return str(a).strip().lower() == str(b).strip().lower()


def evaluate(node: dict[str, Any], record: dict[str, Any]) -> bool:
    if "all" in node:
        return all(evaluate(n, record) for n in node["all"])
    if "any" in node:
        return any(evaluate(n, record) for n in node["any"])
    if "not" in node:
        return not evaluate(node["not"], record)
    value = record.get(node["field"])
    op = node["op"]
    if op == "missing":
        return _missing(value)
    if op == "present":
        return not _missing(value)
    if _missing(value):
        return op in ("ne", "not_in")
    target = node.get("value")
    if op == "eq":
        return _eq(value, target)
    if op == "ne":
        return not _eq(value, target)
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _as_number(value), _as_number(target)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op == "in":
        return any(_eq(value, t) for t in target)
    if op == "not_in":
        return not any(_eq(value, t) for t in target)
    if op == "contains":
        return str(target).lower() in str(value).lower()
    return False


_OP_TEXT = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "in": "IN", "not_in": "NOT IN", "contains": "CONTAINS"}


def render(node: dict[str, Any] | None) -> str:
    """Human-readable form shown next to the original condition text."""
    if not node:
        return "(no condition)"
    if "all" in node:
        return " AND ".join(f"({render(n)})" if "any" in n else render(n) for n in node["all"])
    if "any" in node:
        return " OR ".join(render(n) for n in node["any"])
    if "not" in node:
        return f"NOT ({render(node['not'])})"
    if node["op"] == "missing":
        return f"{node['field']} IS MISSING"
    if node["op"] == "present":
        return f"{node['field']} IS PRESENT"
    return f"{node['field']} {_OP_TEXT[node['op']]} {node.get('value')!r}"


# ---- the oracle -----------------------------------------------------------


class RuleView(BaseModel):
    id: str
    gateway_id: str
    gateway_label: str = ""
    branch_target_id: str
    branch_label: str = ""
    condition_text: str = ""
    expression: dict[str, Any] | None = None
    rendered: str = ""
    outcome: str | None = None
    priority: int = 100
    is_default: bool = False
    status: str
    note: str | None = None
    control_id: str | None = None
    version: int = 1


@dataclass
class OracleVerdict:
    decision: str | None
    routes: dict[str, str] = field(default_factory=dict)  # gateway id -> branch label
    fired: list[str] = field(default_factory=list)  # rule ids
    undetermined: list[str] = field(default_factory=list)  # gateways with no matching rule


def run_oracle(rules: list[RuleView], record: dict[str, Any]) -> OracleVerdict:
    confirmed = [r for r in rules if r.status == "confirmed"]
    by_gateway: dict[str, list[RuleView]] = {}
    for rule in confirmed:
        by_gateway.setdefault(rule.gateway_id, []).append(rule)
    verdict = OracleVerdict(decision=None)
    outcomes: list[str] = []
    for gateway_id, gateway_rules in by_gateway.items():
        chosen = None
        for rule in sorted(gateway_rules, key=lambda r: (r.is_default, r.priority)):
            if rule.is_default:
                chosen = chosen or rule
                continue
            try:
                if rule.expression and evaluate(rule.expression, record):
                    chosen = rule
                    break
            except (KeyError, TypeError, ValidationError):
                continue
        if chosen is None:
            verdict.undetermined.append(gateway_id)
            continue
        verdict.routes[gateway_id] = chosen.branch_label
        verdict.fired.append(chosen.id)
        if chosen.outcome in _SEVERITY:
            outcomes.append(chosen.outcome)
    if outcomes:
        verdict.decision = max(outcomes, key=lambda o: _SEVERITY[o])
    return verdict
