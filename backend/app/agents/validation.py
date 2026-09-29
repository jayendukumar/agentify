"""Epic 23, US23.2: the mandatory validation checklist an artifact must
satisfy before it can reach `SIMULATION_READY` (US23.3 enforces this as a
real precondition on starting a run, not just a display). Almost every
item reads a field Epic 20 already added to `AgentDefinition` -- this
module is that epic's completeness gate made visible and checkable, per
the epic's own Notes.
"""

from __future__ import annotations

from app.schemas.agents import AgentDefinition, ChecklistItem


def compute_checklist(definition: AgentDefinition, *, has_scenarios: bool) -> list[ChecklistItem]:
    needs_tools = bool(definition.tools_systems_needed)
    return [
        ChecklistItem(
            key="purpose_and_trigger", label="Clear purpose and trigger",
            satisfied=bool(definition.purpose.strip()) and bool(definition.trigger.strip()),
        ),
        ChecklistItem(
            key="bounded_responsibility", label="Bounded responsibility (system prompt)",
            satisfied=bool(definition.system_prompt.strip()),
        ),
        ChecklistItem(
            key="typed_input_output", label="Typed output schema",
            satisfied=bool(definition.output_schema),
            detail="Input schema may legitimately be empty for a self-contained trigger; output must be typed.",
        ),
        ChecklistItem(
            key="success_criteria", label="Success criteria", satisfied=bool(definition.success_criteria.strip()),
        ),
        ChecklistItem(
            key="completion_conditions", label="Completion/stop conditions",
            satisfied=bool(definition.completion_conditions.strip()),
        ),
        ChecklistItem(
            key="tool_contracts", label="Tool contracts",
            satisfied=(not needs_tools) or bool(definition.tool_contracts),
        ),
        ChecklistItem(
            key="permissions", label="Permissions",
            satisfied=(not needs_tools) or bool(definition.permissions),
        ),
        ChecklistItem(key="guardrails", label="Runtime guardrails", satisfied=True),
        ChecklistItem(key="retry_policy", label="Retry policy", satisfied=True),
        ChecklistItem(
            key="escalation_rules", label="Escalation rules",
            satisfied=bool(definition.escalation_policy.escalate_when),
        ),
        ChecklistItem(key="human_approval_policy", label="Human approval policy", satisfied=True),
        ChecklistItem(
            key="knowledge_sources", label="Knowledge sources", satisfied=True,
            detail=(
                "Non-blocking: nothing in this system auto-populates knowledge sources yet "
                "(Epic 20, US20.5) -- an empty list isn't treated as a gap."
            ),
        ),
        ChecklistItem(key="model_policy", label="Model policy", satisfied=definition.model_policy is not None),
        ChecklistItem(key="runtime_limits", label="Runtime limits", satisfied=True),
        ChecklistItem(
            key="audit_configuration", label="Audit configuration", satisfied=True,
            detail="Structural: every run's step trace is always recorded (Epic 17).",
        ),
        ChecklistItem(key="test_scenarios", label="Test scenarios", satisfied=has_scenarios),
    ]
