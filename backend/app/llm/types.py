from __future__ import annotations

import base64
from typing import Any, Literal

from pydantic import BaseModel


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    type: Literal["image_url"] = "image_url"
    image_url: dict[str, str]

    @classmethod
    def from_data_uri(cls, data_uri: str, detail: Literal["auto", "low", "high"] = "auto") -> "ImageContent":
        return cls(image_url={"url": data_uri, "detail": detail})

    @classmethod
    def from_bytes(
        cls, data: bytes, mime_type: str, detail: Literal["auto", "low", "high"] = "auto"
    ) -> "ImageContent":
        encoded = base64.b64encode(data).decode("ascii")
        return cls.from_data_uri(f"data:{mime_type};base64,{encoded}", detail=detail)


ContentBlock = TextContent | ImageContent


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentBlock]
    tool_call_id: str | None = None
    name: str | None = None
    # Set on an assistant message that requested tool call(s), so a
    # multi-turn tool-calling conversation can be replayed back to the
    # provider -- every OpenAI-compatible API requires the assistant
    # message that triggered tool_calls to carry them, or the following
    # role="tool" messages are rejected as orphaned. Nothing needed this
    # until Epic 14's twin execution loop (app/twin/engine.py), the first
    # caller to actually continue a tool-calling conversation past one turn.
    tool_calls: list[ToolCall] | None = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ChatCompletionResult(BaseModel):
    text: str | None
    tool_calls: list[ToolCall] = []
    finish_reason: str | None
    usage: Usage
    model: str
