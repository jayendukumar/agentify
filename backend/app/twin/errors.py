from __future__ import annotations

from app.runtime.executor import ExecutionError


class TwinServiceError(ExecutionError):
    """Raised anywhere in app/twin/* when a twin run can't proceed --
    malformed/incomplete LLM structured output (schema inference), a
    scenario's human-checkpoint rule referencing a field the agent never
    provided, or similar. The API layer (app/api/twin.py) turns this into
    an HTTP 502, mirroring BlueprintServiceError.

    Epic 39: an ExecutionError, so the shared executor ends a run FAILED
    with this message, as the twin loop always did."""
