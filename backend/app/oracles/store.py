"""Epic 33: reading decision rules as RuleViews -- kept apart from
service.py (which imports the repository) so the repository and the twin
can read confirmed rules without an import cycle (Epic 36, US36.3)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DecisionRuleModel

from .rules import RuleView, render


def to_view(rule: DecisionRuleModel) -> RuleView:
    return RuleView(
        id=rule.id,
        gateway_id=rule.gateway_id,
        gateway_label=rule.gateway_label,
        branch_target_id=rule.branch_target_id,
        branch_label=rule.branch_label,
        condition_text=rule.condition_text,
        expression=rule.expression,
        rendered=render(rule.expression) if rule.expression else "",
        outcome=rule.outcome,
        priority=rule.priority,
        is_default=rule.is_default,
        status=rule.status,
        note=rule.note,
        control_id=rule.control_id,
        version=rule.version,
    )


def list_rules(session: Session, process_id: str) -> list[DecisionRuleModel]:
    return list(
        session.scalars(
            select(DecisionRuleModel)
            .where(DecisionRuleModel.process_id == process_id)
            .order_by(DecisionRuleModel.gateway_id, DecisionRuleModel.is_default, DecisionRuleModel.priority)
        )
    )


def confirmed_views(session: Session, process_id: str) -> list[RuleView]:
    return [to_view(r) for r in list_rules(session, process_id) if r.status == "confirmed"]
