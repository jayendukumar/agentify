"""Epic 5: orchestrates one chat turn -- build the prompt (prompts.py),
call the LLM, parse+validate its JSON reply, and translate the reply's
element/flow ids from bare schema ids (what the LLM reasons in, since
that's what the schema context it's given uses) to rendered BPMN ids (what
the canvas/diff-preview UI highlights) -- see app/bpmn/chat_ops.py and the
"Diff id convention" decision this mirrors.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from app.bpmn.chat_ops import bpmn_id_for_element, bpmn_id_for_flow
from app.llm import ChatMessage, LLMClient
from app.schemas.chat import ChatMessageResult, ChatReplyKind, DiagramDiff
from app.schemas.common import ProcessSchema

from .prompts import build_system_prompt

_HISTORY_TURNS = 10


class ChatServiceError(Exception):
    """Raised when the LLM's reply can't be parsed into the expected
    shape -- the API layer turns this into a clean error response rather
    than a 500 crash (mirrors app/ingestion/structuring.py's
    StructuringError)."""


class _ParsedReply(BaseModel):
    kind: ChatReplyKind
    reply_text: str
    diff: DiagramDiff | None = None


class ParsedChatReply(BaseModel):
    kind: ChatReplyKind
    reply_text: str
    diff: DiagramDiff | None = None
    needs_confirmation: bool


def _build_history_messages(history: list[ChatMessageResult]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for past in history[-_HISTORY_TURNS:]:
        messages.append(ChatMessage(role="user", content=past.request_text))
        messages.append(ChatMessage(role="assistant", content=past.reply_text))
    return messages


def _element_label(schema: ProcessSchema, element_id: str) -> str | None:
    element = next((e for e in schema.elements if e.id == element_id), None)
    return element.label if element else None


def _translate_diff_ids(schema: ProcessSchema, diff: DiagramDiff) -> DiagramDiff:
    """The LLM emits bare schema ids (el-3, f-4, ...); translate every
    element_id/flow_id reference to its rendered BPMN id so what gets
    stored/returned already matches what BpmnCanvas highlights. Only
    references to *existing* elements/flows can be translated this way --
    ids inside an add_element/add_flow payload don't exist yet (the
    backend mints them on apply) and are left as-is."""
    element_type_by_id = {e.id: e.type for e in schema.elements}
    flow_ids = {f.id for f in schema.flows}

    def translate(raw_id: str) -> str:
        if raw_id in element_type_by_id:
            return bpmn_id_for_element(raw_id, element_type_by_id[raw_id])
        if raw_id in flow_ids:
            return bpmn_id_for_flow(raw_id)
        return raw_id

    translated = diff.model_copy(deep=True)
    translated.target_element_ids = [translate(i) for i in diff.target_element_ids]
    for op in translated.operations:
        if op.element_id is not None:
            op.element_id = translate(op.element_id)
        if op.flow_id is not None:
            op.flow_id = translate(op.flow_id)
    return translated


async def handle_chat_message(
    llm: LLMClient,
    *,
    schema: ProcessSchema,
    history: list[ChatMessageResult],
    selected_element_id: str | None,
    text: str,
) -> ParsedChatReply:
    selected_label = _element_label(schema, selected_element_id) if selected_element_id else None
    system_prompt = build_system_prompt(schema, selected_label)

    messages = [
        ChatMessage(role="system", content=system_prompt),
        *_build_history_messages(history),
        ChatMessage(role="user", content=text),
    ]

    result = await llm.complete(
        messages,
        operation="chat_edit",
        response_format={"type": "json_object"},
        # Same headroom as app/ingestion/structuring.py, and for the same
        # reason -- Qwen3.7 Flash spends part of this budget on internal
        # reasoning before the visible JSON reply; confirmed hitting a
        # smaller (4000) cap with finish_reason="length" and empty
        # result.text on a real chat_edit call during manual testing.
        max_tokens=16000,
    )

    if not result.text:
        raise ChatServiceError("LLM returned no content for chat message")

    try:
        payload: Any = json.loads(result.text)
        parsed = _ParsedReply.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ChatServiceError(f"LLM returned invalid structured output for chat message: {exc}") from exc

    diff = _translate_diff_ids(schema, parsed.diff) if parsed.kind == "edit" and parsed.diff is not None else None
    if parsed.kind == "edit" and diff is None:
        raise ChatServiceError("LLM classified the message as an edit but returned no diff")

    return ParsedChatReply(
        kind=parsed.kind,
        reply_text=parsed.reply_text,
        diff=diff,
        needs_confirmation=parsed.kind == "edit",
    )
