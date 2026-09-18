from fastapi import APIRouter, HTTPException, status

from app.db import repository
from app.schemas.chat import ChatApplyRequest, ChatMessageRequest, ChatMessageResult

from .deps import DbDep, LLMDep, StoreDep

router = APIRouter(prefix="/api/processes/{process_id}/chat", tags=["chat"])


@router.post("/messages", response_model=ChatMessageResult)
async def send_chat_message(process_id: str, body: ChatMessageRequest, db: DbDep, llm: LLMDep) -> ChatMessageResult:
    repository.get_process(db, process_id)  # 404s if missing

    # TODO(Epic 5 / bpmn-chat-ops skill): classify intent, build a
    # DiagramDiff, and return it unapplied for confirmation. `llm` and the
    # chat-history store methods are already wired for that implementation.
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        detail="Chat-driven diagram editing is not implemented yet (Epic 5). This endpoint is scaffolded and wired to the LLM client.",
    )


@router.get("/messages", response_model=list[ChatMessageResult])
def list_chat_messages(process_id: str, db: DbDep, store: StoreDep) -> list[ChatMessageResult]:
    repository.get_process(db, process_id)  # 404s if missing
    return store.list_chat_messages(process_id)


@router.post("/messages/{message_id}/apply", response_model=ChatMessageResult)
def apply_chat_message(process_id: str, message_id: str, body: ChatApplyRequest, db: DbDep) -> ChatMessageResult:
    repository.get_process(db, process_id)  # 404s if missing

    # TODO(Epic 5, US5.3): apply the message's proposed_diff to the draft
    # BPMN (bpmn-authoring skill validation checklist), then mark applied.
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        detail="Applying a chat-proposed diagram diff is not implemented yet (Epic 5).",
    )
