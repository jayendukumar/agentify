"""Epic 14 (core slice): synthesizes a callable tool schema for every
`tools_systems_needed` entry of an agent artifact -- Epic 12 left that
field as plain strings (e.g. "CRM system"), nothing callable. One LLM call
per artifact covers every system at once (not one call per system) to
keep cost down. Mirrors app/blueprint/service.py's evaluate_blueprint: a
single structured-JSON completion, parsed/validated into Pydantic,
reconciled against the known-valid system names (never trust the model's
name echo-back as-is), malformed or incomplete output raised as a domain
error the API layer turns into a 502.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm import ChatMessage, LLMClient
from app.schemas.agents import AgentDefinition
from app.schemas.twin import InferredToolSchema

from .errors import TwinServiceError


class _ParsedToolSchemas(BaseModel):
    tools: list[InferredToolSchema]


def _build_system_prompt(artifact: AgentDefinition) -> str:
    systems = "\n".join(f"- {name}" for name in artifact.tools_systems_needed)
    return (
        "You are designing a synthetic test double for a business-process automation agent, "
        "for testing purposes only -- nothing here will be called live.\n\n"
        f"The agent's purpose: {artifact.purpose}\n"
        f"Trigger: {artifact.trigger}\n\n"
        "For EACH of the following systems/tools the agent needs, propose ONE plausible callable "
        "tool schema it would use to interact with that system: a function name, a short "
        "description, JSON-schema parameters (an object schema with \"type\": \"object\", "
        "\"properties\", and \"required\"), and a short description of what a typical successful "
        "response looks like (field names and what they represent). Invent something plausible for "
        "a system with this name -- don't ask for clarification.\n\n"
        f"Systems:\n{systems}\n\n"
        'Respond as JSON: {"tools": [{"system_name": <exactly one of the system names above>, '
        '"tool_name": str, "description": str, "parameters": <json schema object>, '
        '"response_shape_description": str}, ...]} -- exactly one entry per system listed above.'
    )


async def infer_tool_schemas(llm: LLMClient, artifact: AgentDefinition) -> dict[str, InferredToolSchema]:
    if not artifact.tools_systems_needed:
        return {}

    result = await llm.complete(
        [ChatMessage(role="system", content=_build_system_prompt(artifact))],
        operation="twin_tool_schema_inference",
        response_format={"type": "json_object"},
        max_tokens=4000,
    )

    if not result.text:
        raise TwinServiceError("LLM returned no content while inferring tool schemas")

    try:
        payload: Any = json.loads(result.text)
        parsed = _ParsedToolSchemas.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise TwinServiceError(f"LLM returned invalid structured output for tool schemas: {exc}") from exc

    valid_names = set(artifact.tools_systems_needed)
    by_name = {tool.system_name: tool for tool in parsed.tools if tool.system_name in valid_names}
    missing = valid_names - set(by_name)
    if missing:
        raise TwinServiceError(f"LLM did not propose a tool schema for every system -- missing: {sorted(missing)}")

    return by_name
