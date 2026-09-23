"""LLM structuring step for US1.3 (PDF/DOCX/Visio text) and US1.5 (image
vision). Turns deterministically-extracted content -- or, for images, the
image itself -- into the canonical ProcessSchema. One call per document,
whole document at once -- no chunking (planning/document-ingestion-strategy.md).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.ids import new_id
from app.llm import ChatMessage, ImageContent, LLMClient, TextContent
from app.llm.types import ContentBlock
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema

from .extractors import ExtractedBlock

_TEXT_INSTRUCTIONS = """You are extracting a structured business process description \
from a source document for the Agentic Solution Generator project.

Read the document content below (each chunk is marked with a [SOURCE <location>] \
header showing where it came from in the original file) and identify the \
business process it describes: the sequence of activities/steps, the actors \
or systems that perform them, decision points, and the inputs/outputs/systems \
each step touches.

{schema_contract}

Before extracting entities, validate whether this document is actually a process definition. A process definition describes how work is performed: an ordered or connected sequence of activities, decisions, events, inputs/outputs, roles, or systems. Project backlogs, issue lists, roadmaps, meeting notes, status reports, and generic requirement inventories are not process definitions unless they also clearly describe how work flows from start to finish.

Rules:
- Every element must have at least one source_ref. Never invent an element \
that isn't evidenced in the document content.
- confidence "high": the element and its position in the sequence are \
explicitly and unambiguously stated in one clear passage. "medium": some \
detail (actor, trigger, exact position) was inferred rather than explicit. \
"low": inferred from indirect evidence, or the document is ambiguous about \
it. Do not default everything to "high".
- Include start/end events only if the sequencing is clear enough to \
identify them; omit them rather than guessing.
- If the document does not describe a business process at all, return \
{{"actors": [], "elements": [], "flows": []}}.
"""

_IMAGE_INSTRUCTIONS = """You are extracting a structured business process description \
from a photo or screenshot of a process flowchart/whiteboard for the Agentic \
Solution Generator project.

Look at the image and identify the business process it depicts: the \
sequence of activities/steps (boxes, diamonds, or other shapes), the actors \
or systems that perform them (if labeled), decision points, and any visible \
inputs/outputs/systems.

{schema_contract}

First validate whether the image actually depicts a process definition or flow. A project board, backlog, roadmap, status report, or generic list of work items is not a process definition unless it clearly shows how work flows from start to finish.

Rules:
- Every element must have at least one source_ref with location "image" \
and an excerpt describing what you saw (e.g. the shape's label text) --  \
not a document quote, since there's no text document, just this image.
- Never invent an element that isn't visibly depicted in the image.
- confidence should reflect how legible/clear the image was for that \
specific element -- "high" only for a clearly legible label with an \
unambiguous connection; "low" if text was blurry/ambiguous or a connection \
was guessed rather than following a visible arrow/line.
- Connections between shapes are only reliable evidence when there's a \
clear arrow or line visible -- do not infer flow purely from spatial \
proximity or reading order.
- If the image does not depict a business process at all, return \
{{"actors": [], "elements": [], "flows": []}}.
"""

_SCHEMA_CONTRACT = """Respond with a single JSON object matching exactly this shape (no prose, no \
markdown code fences, just the JSON object):
{
  "is_process_definition": true,
  "process_definition_confidence": 0,
  "validation_message": "One or two concise sentences explaining the evidence for the decision.",
  "actors": [
    {"id": "string, unique, e.g. actor-1", "name": "string", "type": "role" | "system" | "external_party"}
  ],
  "elements": [
    {
      "id": "string, unique, e.g. el-1",
      "type": "task" | "decision" | "start_event" | "end_event" | "intermediate_event",
      "label": "string, concise description of the step",
      "actor_id": "string matching an actors[].id, or null",
      "inputs": ["string"],
      "outputs": ["string"],
      "systems_touched": ["string"],
      "source_refs": [
        {"document_id": "<the document id given below, exactly>", "location": "<the location text only -- for text documents, copy what appears between 'SOURCE ' and the closing ']' in the matching marker, WITHOUT the brackets or the word SOURCE; for an image, just the word 'image'>", "excerpt": "short quote or description supporting this element"}
      ],
      "confidence": "high" | "medium" | "low"
    }
  ],
  "flows": [
    {"id": "string, unique, e.g. f-1", "from": "an elements[].id", "to": "an elements[].id", "condition": "string or null"}
  ]
}"""


class _ExtractedSchema(BaseModel):
    is_process_definition: bool | None = None
    # The prompt asks for an integer 0-100, but real responses (observed
    # against the live OpenRouter/Qwen3.7 Flash model, not just the mocked
    # test payloads) sometimes come back as a 0-1 fraction instead (e.g.
    # 0.9) -- accepting float here and normalizing in _run_structuring_call
    # avoids a strict-int ValidationError there turning into a StructuringError
    # that then gets misreported as "not a process document" for a document
    # that actually is one.
    process_definition_confidence: float | None = Field(default=None, ge=0)
    validation_message: str | None = None
    actors: list[Actor] = Field(default_factory=list)
    elements: list[ProcessElement] = Field(default_factory=list)
    flows: list[ProcessFlow] = Field(default_factory=list)


class StructuringError(Exception):
    """Raised when a document has nothing to extract, or the LLM's
    structured output can't be parsed into the canonical schema."""


class DocumentValidationError(StructuringError):
    """Raised when the uploaded file is not a process definition."""

    def __init__(self, message: str, confidence: int) -> None:
        super().__init__(message)
        self.confidence = confidence


def _render_blocks(blocks: list[ExtractedBlock]) -> str:
    return "\n\n".join(f"[SOURCE {block.location}]\n{block.content}" for block in blocks)


def _strip_source_marker(location: str) -> str:
    """The model sometimes copies the whole '[SOURCE <location>]' marker
    instead of just <location> despite being told not to -- strip it back
    to the bare location string rather than relying on prompt compliance."""
    stripped = location.strip()
    if stripped.startswith("[SOURCE ") and stripped.endswith("]"):
        return stripped[len("[SOURCE ") : -1]
    if stripped.startswith("SOURCE "):
        return stripped[len("SOURCE ") :]
    return stripped


# Live testing against the real OpenRouter/Qwen3.7 Flash model (not just
# mocked payloads) found it omits is_process_definition/confidence/message
# entirely in roughly half of calls on a real, larger document -- despite
# reliably classifying the same document correctly on other calls. Silently
# treating "omitted" the same as "explicitly not a process" (the old
# behavior) meant a real process document had a coin-flip chance of being
# wrongly rejected. Re-running the whole (large, slow) extraction call just
# to get a yes/no was tried first and measured worse than expected -- on a
# 24-element document it still came back unclassified 3 times in a row.
# Instead, keep the first (expensive) extraction's elements and retry only a
# small, focused classification-only follow-up call when it's missing --
# cheaper, faster, and a simpler task for the model to actually answer.
_MAX_CLASSIFICATION_ATTEMPTS = 3

_CLASSIFICATION_FOLLOWUP = """

Ignore the JSON schema above. Answer ONLY this question about the same document/image: does it describe \
a business process -- an ordered or connected sequence of activities, decisions, events, inputs/outputs, \
roles, or systems showing how work flows from start to finish? Project backlogs, issue lists, roadmaps, \
meeting notes, status reports, and generic requirement inventories are NOT process definitions unless \
they also clearly describe how work flows from start to finish.

Respond with a single JSON object matching exactly this shape (no prose, no markdown code fences). All \
three fields are required -- never omit any of them:
{
  "is_process_definition": true,
  "process_definition_confidence": 0,
  "validation_message": "One or two concise sentences explaining the evidence for the decision."
}"""


class _ClassificationResult(BaseModel):
    is_process_definition: bool
    process_definition_confidence: float = Field(ge=0)
    validation_message: str


def _with_classification_followup(content: str | list[ContentBlock]) -> str | list[ContentBlock]:
    if isinstance(content, str):
        return content + _CLASSIFICATION_FOLLOWUP
    return [
        TextContent(text=block.text + _CLASSIFICATION_FOLLOWUP) if isinstance(block, TextContent) else block
        for block in content
    ]


async def _classify_process_definition(
    llm: LLMClient, *, filename: str, content: str | list[ContentBlock]
) -> _ClassificationResult | None:
    """Best-effort fallback used only when the main extraction call omits
    the classification fields. Never raises -- any failure here just means
    the caller keeps waiting for a definitive answer (or, after exhausting
    attempts, falls back to the existing fail-closed default), the same as
    if the model had omitted the fields again."""
    try:
        result = await llm.complete(
            [ChatMessage(role="user", content=_with_classification_followup(content))],
            operation="document_extraction",
            response_format={"type": "json_object"},
            max_tokens=2000,
        )
        if not result.text:
            return None
        return _ClassificationResult.model_validate(json.loads(result.text))
    except (json.JSONDecodeError, ValidationError):
        return None


async def _complete_and_parse(llm: LLMClient, *, filename: str, content: str | list[ContentBlock]) -> _ExtractedSchema:
    result = await llm.complete(
        [ChatMessage(role="user", content=content)],
        operation="document_extraction",
        response_format={"type": "json_object"},
        # Qwen3.7 Flash spends part of this budget on internal reasoning
        # before the visible answer (observed even on trivial prompts) --
        # 8000 was measured hitting the cap on a real, moderately-sized
        # document with output_tokens=8000 and empty result.text. 16000
        # leaves headroom for reasoning + a sizable process JSON.
        max_tokens=16000,
    )

    if not result.text:
        if result.finish_reason == "length":
            raise StructuringError(
                f"LLM response for '{filename}' was truncated at the token limit before producing "
                "an answer (likely spent on internal reasoning) -- try a shorter/simpler document"
            )
        raise StructuringError(f"LLM returned no content while structuring '{filename}'")

    try:
        payload: Any = json.loads(result.text)
        return _ExtractedSchema.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise StructuringError(f"LLM returned invalid structured output for '{filename}': {exc}") from exc


async def _run_structuring_call(
    llm: LLMClient,
    *,
    document_id: str,
    filename: str,
    process_name: str,
    content: str | list[ContentBlock],
    require_classification: bool = False,
) -> tuple[ProcessSchema, int, str, bool]:
    extracted = await _complete_and_parse(llm, filename=filename, content=content)
    if require_classification and extracted.is_process_definition is None:
        for _attempt in range(_MAX_CLASSIFICATION_ATTEMPTS - 1):
            classification = await _classify_process_definition(llm, filename=filename, content=content)
            if classification is not None:
                extracted.is_process_definition = classification.is_process_definition
                extracted.process_definition_confidence = classification.process_definition_confidence
                extracted.validation_message = classification.validation_message
                break

    # Don't trust the model to follow the prompt's formatting instructions
    # exactly -- normalize deterministically instead of relying on it.
    for element in extracted.elements:
        for source_ref in element.source_refs:
            source_ref.document_id = document_id
            source_ref.location = _strip_source_marker(source_ref.location)

    _assign_globally_unique_ids(extracted)

    schema = ProcessSchema(
        process_name=process_name,
        actors=extracted.actors,
        elements=extracted.elements,
        flows=extracted.flows,
    )
    # Validation is per uploaded document, including when the process already
    # contains other documents. Do not infer a positive classification from
    # extracted elements: a backlog or requirements list can contain things
    # that look like steps without describing an executable process.
    is_process = extracted.is_process_definition
    confidence = extracted.process_definition_confidence
    message = extracted.validation_message
    if is_process is None:
        is_process = False
    if confidence is None:
        confidence = 70 if is_process else 0
    else:
        # Real responses from the live model sometimes use a 0-1 fraction
        # (e.g. 0.9) instead of the requested 0-100 integer -- normalize
        # rather than let a strict int field reject the whole response.
        if confidence <= 1:
            confidence *= 100
        confidence = round(min(100, confidence))
    if not message:
        message = (
            "The document contains process activities or flow evidence."
            if is_process
            else "The document did not receive an explicit process-document classification."
        )
    return schema, confidence, message, is_process


def _assign_globally_unique_ids(extracted: _ExtractedSchema) -> None:
    """The LLM assigns ids like "actor-1"/"el-1" that are only unique
    *within this one call's response* -- two different documents' extractions
    (or two images, or a Visio parse) can each produce "actor-1" for
    completely different people. Replace every id with a globally-unique one
    right here, once, so every ProcessSchema this module ever returns is
    already safe to merge (app/ingestion/merge.py) or persist (a real DB
    primary key would otherwise collide) without either of those layers
    having to know or care that the LLM's ids were only ever local labels.
    """
    actor_remap = {actor.id: new_id("actor") for actor in extracted.actors}
    element_remap = {element.id: new_id("el") for element in extracted.elements}

    for actor in extracted.actors:
        actor.id = actor_remap[actor.id]

    for element in extracted.elements:
        element.id = element_remap[element.id]
        if element.actor_id is not None:
            # The model sometimes references an actor id it never declared
            # in `actors` (real failure found against a real, complex
            # multi-actor document -- see the decision log) -- null it out
            # rather than leave the stale local id in place, which would
            # violate process_elements' actor_id FK the moment this schema
            # is persisted. actor_id is optional, so this degrades to "no
            # actor assigned" instead of a hard failure.
            element.actor_id = actor_remap.get(element.actor_id)

    for flow in extracted.flows:
        flow.id = new_id("flow")
        flow.from_ = element_remap.get(flow.from_, flow.from_)
        flow.to = element_remap.get(flow.to, flow.to)

    # Same trust issue, but from_/to are required fields on ProcessFlow --
    # can't null them out, so drop any flow that still references an
    # element id the model never declared (i.e. remapping above left it
    # untouched) rather than let a dangling foreign key reach the DB.
    valid_element_ids = {element.id for element in extracted.elements}
    extracted.flows = [
        flow for flow in extracted.flows if flow.from_ in valid_element_ids and flow.to in valid_element_ids
    ]


async def structure_process(
    llm: LLMClient,
    *,
    document_id: str,
    filename: str,
    blocks: list[ExtractedBlock],
    process_name: str,
) -> ProcessSchema:
    """US1.3/US1.4: structure deterministically-extracted text/table/shape
    blocks (PDF, DOCX, or Visio) into the canonical schema."""
    if not blocks:
        raise StructuringError(f"No extractable text or tables found in '{filename}'")

    instructions = _TEXT_INSTRUCTIONS.format(schema_contract=_SCHEMA_CONTRACT)
    prompt = (
        f"{instructions}\n\n"
        f'Document id: "{document_id}"\n'
        f'Document filename: "{filename}"\n\n'
        f"Document content:\n{_render_blocks(blocks)}"
    )

    schema, _confidence, _message, _is_process = await _run_structuring_call(
        llm, document_id=document_id, filename=filename, process_name=process_name, content=prompt
    )
    return schema


async def structure_process_with_validation(
    llm: LLMClient,
    *,
    document_id: str,
    filename: str,
    blocks: list[ExtractedBlock],
    process_name: str,
) -> tuple[ProcessSchema, int, str]:
    """Extract a process and reject documents classified as non-processes."""
    if not blocks:
        raise StructuringError(f"No extractable text or tables found in '{filename}'")
    instructions = _TEXT_INSTRUCTIONS.format(schema_contract=_SCHEMA_CONTRACT)
    prompt = (
        f"{instructions}\n\n"
        f'Document id: "{document_id}"\n'
        f'Document filename: "{filename}"\n\n'
        f"Document content:\n{_render_blocks(blocks)}"
    )
    schema, confidence, message, is_process = await _run_structuring_call(
        llm,
        document_id=document_id,
        filename=filename,
        process_name=process_name,
        content=prompt,
        require_classification=True,
    )
    if not is_process:
        raise DocumentValidationError(
            f"'{filename}' does not appear to be a process definition (confidence {confidence}%). {message}",
            confidence,
        )
    return schema, confidence, message


async def structure_process_from_image(
    llm: LLMClient,
    *,
    document_id: str,
    filename: str,
    image_bytes: bytes,
    mime_type: str,
    process_name: str,
) -> ProcessSchema:
    """US1.5: structure a photo/screenshot of a process diagram directly
    via the LLM's vision input -- no deterministic pre-extraction step
    exists for images (see the process-doc-ingestion skill)."""
    instructions = _IMAGE_INSTRUCTIONS.format(schema_contract=_SCHEMA_CONTRACT)
    prompt_text = f"{instructions}\n\nDocument id: \"{document_id}\"\nDocument filename: \"{filename}\""

    content: list[ContentBlock] = [
        TextContent(text=prompt_text),
        ImageContent.from_bytes(image_bytes, mime_type),
    ]

    schema, _confidence, _message, _is_process = await _run_structuring_call(
        llm, document_id=document_id, filename=filename, process_name=process_name, content=content
    )
    return schema


async def structure_process_from_image_with_validation(
    llm: LLMClient,
    *,
    document_id: str,
    filename: str,
    image_bytes: bytes,
    mime_type: str,
    process_name: str,
) -> tuple[ProcessSchema, int, str]:
    instructions = _IMAGE_INSTRUCTIONS.format(schema_contract=_SCHEMA_CONTRACT)
    prompt_text = f'{instructions}\n\nDocument id: "{document_id}"\nDocument filename: "{filename}"'
    content: list[ContentBlock] = [TextContent(text=prompt_text), ImageContent.from_bytes(image_bytes, mime_type)]
    schema, confidence, message, is_process = await _run_structuring_call(
        llm,
        document_id=document_id,
        filename=filename,
        process_name=process_name,
        content=content,
        require_classification=True,
    )
    if not is_process:
        raise DocumentValidationError(
            f"'{filename}' does not appear to be a process definition (confidence {confidence}%). {message}",
            confidence,
        )
    return schema, confidence, message
