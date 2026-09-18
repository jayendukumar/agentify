from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

ChatIntent = Literal[
    "add_node",
    "delete_node",
    "rename_node",
    "reassign_actor",
    "change_type",
    "add_flow",
    "delete_flow",
    "reroute_flow",
]

ChatReplyKind = Literal["edit", "explain", "clarify"]


class DiagramDiffOperation(BaseModel):
    op: Literal["add_element", "remove_element", "update_element", "add_flow", "remove_flow", "update_flow"]
    element_id: str | None = None
    flow_id: str | None = None
    element: dict[str, Any] | None = None
    flow: dict[str, Any] | None = None
    fields: dict[str, Any] | None = None


class DiagramDiff(BaseModel):
    """See the bpmn-chat-ops skill -- this mirrors that diff schema."""

    intent: ChatIntent
    summary: str
    target_element_ids: list[str] = []
    operations: list[DiagramDiffOperation] = []


class ChatMessageRequest(BaseModel):
    text: str
    selected_element_id: str | None = None


class ChatMessageResult(BaseModel):
    id: str
    process_id: str
    request_text: str
    selected_element_id: str | None = None
    kind: ChatReplyKind
    reply_text: str
    proposed_diff: DiagramDiff | None = None
    needs_confirmation: bool
    applied: bool
    declined: bool
    created_at: datetime
    decided_at: datetime | None = None


class ChatApplyRequest(BaseModel):
    confirm: bool = True
