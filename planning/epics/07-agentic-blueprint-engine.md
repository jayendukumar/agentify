# Epic 7 -- Agentic Blueprint Generation Engine

Goal: Evaluate every node of the finalized as-is BPMN diagram and produce an
"agentic blueprint" overlay: which steps can be performed by an AI agent,
what that agent would need to do it, and which steps cannot be automated.

Depends on: Epic 6 (a finalized baseline diagram).
Feeds: Epic 8 (visualization of these results).

## User Stories

US7.1 -- Per-node automation feasibility evaluation.
As an Automation Architect, I want every node in the finalized diagram
evaluated for whether an AI agent could perform it (yes / partially / no),
with a stated rationale, so that I get a defensible, explainable assessment
rather than a black-box label.

US7.2 -- Step-type classification.
As an Automation Architect, I want each step classified by type (data
retrieval/transformation, rule-based decision, judgment-based decision,
document generation, communication/notification, physical/manual action,
approval/compliance sign-off, exception handling), so that the automation
verdict is grounded in a consistent taxonomy.

US7.3 -- Agent specification per automatable node.
As an Automation Architect, I want each automatable step to come with a
proposed agent spec (purpose, trigger, required inputs, expected outputs,
tools/systems it would need access to), so that I understand what building
that agent would actually require.

US7.4 -- Clear flagging of non-automatable steps.
As an Automation Architect, I want steps that cannot be automated clearly
marked with a specific reason (requires human judgment, requires physical
action, requires legal/compliance authority, etc.), so that the blueprint is
honest about the limits of automation, not just a list of proposed agents.

US7.5 -- Grouping/consolidation recommendations.
As an Automation Architect, I want the system to recommend when several
sequential steps should be handled by one agent versus separate agents, so
that the resulting blueprint is a practical automation design, not a
one-agent-per-node default.

US7.6 -- Non-destructive overlay.
As a Process Analyst, I want the blueprint evaluation to be generated as an
overlay layer on top of the finalized baseline diagram, without altering the
baseline itself, so that the as-is diagram remains an accurate historical
record.

US7.7 -- Re-run blueprint evaluation after baseline changes.
As an Automation Architect, I want to regenerate the blueprint when the
finalized baseline diagram changes (a new version), so that the automation
assessment never silently goes stale against an outdated diagram.

## Notes / Open Questions

See the agentic-blueprint-evaluator skill for the evaluation rubric, agent
spec schema, and overlay data format this epic should implement.
