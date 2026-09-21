from __future__ import annotations


class TwinServiceError(Exception):
    """Raised anywhere in app/twin/* when a twin run can't proceed --
    malformed/incomplete LLM structured output (schema inference), a
    scenario's human-checkpoint rule referencing a field the agent never
    provided, or similar. The API layer (app/api/twin.py) turns this into
    an HTTP 502, mirroring BlueprintServiceError."""
