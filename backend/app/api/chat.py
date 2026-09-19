import logging

from fastapi import APIRouter, HTTPException, status

from app.bpmn.chat_ops import DiagramApplyRegressionError, DiagramDiffError, apply_diff_and_persist
from app.chat.service import ChatServiceError, handle_chat_message
from app.db import repository
from app.gap_analysis.service import run_gap_analysis
from app.schemas.chat import ChatApplyRequest, ChatMessageRequest, ChatMessageResult, DiagramDiff

from .deps import DbDep, LLMDep

logger = logging.getLogger("app.api.chat")

router = APIRouter(prefix="/api/processes/{process_id}/chat", tags=["chat"])


@router.post("/messages", response_model=ChatMessageResult)
async def send_chat_message(process_id: str, body: ChatMessageRequest, db: DbDep, llm: LLMDep) -> ChatMessageResult:
    repository.get_process(db, process_id)  # 404s if missing

    schema = repository.get_process_schema(db, process_id)
    if schema is None or not schema.elements:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="No extracted process data yet -- upload and wait for at least one document to finish "
            "processing, or generate a draft BPMN, before using chat",
        )

    history = repository.list_chat_messages(db, process_id)

    try:
        reply = await handle_chat_message(
            llm,
            schema=schema,
            history=history,
            selected_element_id=body.selected_element_id,
            text=body.text,
        )
    except ChatServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return repository.add_chat_message(
        db,
        process_id,
        request_text=body.text,
        selected_element_id=body.selected_element_id,
        kind=reply.kind,
        reply_text=reply.reply_text,
        proposed_diff=reply.diff.model_dump() if reply.diff is not None else None,
        needs_confirmation=reply.needs_confirmation,
    )


@router.get("/messages", response_model=list[ChatMessageResult])
def list_chat_messages(process_id: str, db: DbDep) -> list[ChatMessageResult]:
    return repository.list_chat_messages(db, process_id)


@router.post("/messages/{message_id}/apply", response_model=ChatMessageResult)
async def apply_chat_message(
    process_id: str, message_id: str, body: ChatApplyRequest, db: DbDep, llm: LLMDep
) -> ChatMessageResult:
    message = repository.get_chat_message(db, process_id, message_id)

    if not message.needs_confirmation:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This chat message has no pending diagram change")
    if message.applied or message.declined:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This chat message has already been decided")

    if not body.confirm:
        return repository.mark_chat_message_decided(db, message, applied=False)

    diff = message.proposed_diff
    if diff is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This chat message has no diff to apply")

    # bpmn-chat-ops hard rule: parse intent -> build diff (already done,
    # POST /messages) -> present -> confirm (here) -> apply diff -> validate
    # result -> log to audit trail. Nothing is persisted unless the
    # regenerated diagram validates clean (apply_diff_and_persist).
    try:
        apply_diff_and_persist(db, process_id, DiagramDiff.model_validate(diff))
    except DiagramApplyRegressionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except DiagramDiffError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Could not apply diagram diff: {exc}") from exc

    result = repository.mark_chat_message_decided(db, message, applied=True)
    # Commit the successful edit on its own before attempting gap analysis
    # below -- best-effort must mean best-effort: if run_gap_analysis
    # fails partway through a flush and we rolled back the shared session,
    # that rollback would silently undo this already-successful apply too.
    # Committing first makes the edit durable regardless of what happens next.
    db.commit()

    # Epic 11, US11.5: the schema just changed, so re-run gap analysis --
    # best-effort, must not fail an otherwise-successful chat edit.
    try:
        await run_gap_analysis(db, llm, process_id)
        db.commit()
    except Exception as gap_exc:
        db.rollback()
        logger.warning("gap_analysis_failed", extra={"process_id": process_id, "error": str(gap_exc)})

    return result
