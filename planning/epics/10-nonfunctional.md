# Epic 10 -- Cross-Cutting / Non-Functional

Goal: Quality, observability, cost, and security concerns that thread
through every other epic rather than belonging to one feature.

Depends on: Epic 9 (foundations to attach logging/config to).
Feeds: all epics (should be seeded early, not bolted on at the end).

## User Stories

US10.1 -- Structured logging and tracing.
As a Platform Engineer, I want structured logging and a request trace ID
that follows a request from ingestion through generation to blueprint
evaluation, so that I can debug failures across the pipeline.

US10.2 -- Consistent UI error handling.
As a Process Analyst, I want clear, consistent error states in the UI
(failed ingestion, failed generation, failed chat edit), so that I always
know what happened and what to do next, rather than a silent failure or
raw error dump.

US10.3 -- LLM API cost/usage tracking.
As a Platform Engineer, I want token usage (input/output) logged per LLM
API call and attributable to an operation type (ingestion, BPMN generation,
chat edit, blueprint evaluation), so that I can see where API spend is going
and optimize the costliest call sites first -- especially useful for
watching whether the current low-cost provider (OpenRouter/Qwen3.7 Flash)
stays cheap enough as volume grows, or whether specific call sites need a
different model.

US10.4 -- Basic access control.
As a Platform Engineer, I want to restrict who can edit and finalize a
diagram (vs. view-only), so that changes to an approved baseline are
intentional and attributable.

US10.5 -- Data privacy for uploaded documents.
As a Platform Engineer, I want uploaded documents (which may contain
sensitive business information) handled with appropriate storage and access
restrictions, so that process documentation isn't exposed beyond the
intended users of the tool.

## Notes / Open Questions

US10.3 should be designed alongside Epic 9 / US9.3 (the shared LLM API
integration layer) since usage tracking is easiest to add at that single
call site rather than retrofitted per feature.

US10.5 is worth revisiting specifically now that the default LLM provider
is OpenRouter routing to Qwen3.7 Flash (an Alibaba Cloud model) -- see the
data-handling caveat in `planning/claude-api-access-notes.md`'s market scan.
If real (non-synthetic) business process documents will be ingested, this
story should include a concrete decision on whether that's acceptable, or
whether a different provider is warranted for sensitive documents even if
the default stays cheap for general use.
