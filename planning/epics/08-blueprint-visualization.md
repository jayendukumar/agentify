# Epic 8 -- Agentic Blueprint Interactive Visualization

Goal: Present the blueprint overlay from Epic 7 to users in an explorable,
explainable way -- which steps become agents, what each agent needs, and
which steps stay manual.

Depends on: Epic 7 (blueprint evaluation results).

## User Stories

US8.1 -- Overlay visualization on the diagram.
As an Automation Architect, I want the finalized diagram redrawn with
automatable and non-automatable nodes visually distinguished (e.g. color
coding), so that I can see the shape of the automation opportunity at a
glance.

US8.2 -- Agent detail panel.
As an Automation Architect, I want to click an automatable node and see the
proposed agent's purpose, required inputs, expected outputs, tools/systems
needed, and the rationale for why this step was selected for automation, so
that I can evaluate whether the recommendation is sound.

US8.3 -- Blueprint summary dashboard.
As a Process Owner, I want a summary view showing the total number of agents
identified, the percentage of the process automatable, and a list of
non-automatable steps with reasons, so that I can quickly grasp the overall
automation potential without reading every node.

US8.4 -- Exportable blueprint report.
As an Automation Architect, I want to export the blueprint (agents, inputs/
outputs, rationale, non-automatable steps) as a PDF or markdown report, so
that I can share it with stakeholders outside the tool.

US8.5 -- Override a blueprint recommendation.
As an Automation Architect, I want to manually mark a node as
automatable/not-automatable (overriding the system's recommendation) and
record my justification, so that domain expertise can correct the system
where needed, with the override captured for audit.

## Notes / Open Questions

US8.5 output should feed back into Epic 7 as labeled feedback -- worth
capturing even if using it to improve future evaluations is a later
enhancement, not in scope for the initial version.
