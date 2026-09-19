"""Background pipeline: extract -> structure -> merge into the process's
schema, updating document status along the way. Covers US1.3 (PDF/DOCX),
US1.4 (Visio), US1.5 (images), and US2.3 (embeddings).

Opens its own DB session rather than reusing the request's -- this runs as
a FastAPI BackgroundTask, which can outlive the request-scoped session's
teardown, so sharing it is a latent "session already closed" bug waiting
to happen rather than something to rely on.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.db import repository
from app.db.session import get_session_factory
from app.gap_analysis.service import run_gap_analysis
from app.llm import LLMClient

from . import storage
from .extractors import ExtractedBlock, extract_docx, extract_pdf, extract_vsdx
from .structuring import StructuringError, structure_process, structure_process_from_image

logger = logging.getLogger("app.ingestion.pipeline")

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
VSDX_CONTENT_TYPES = {"application/vnd.ms-visio.drawing", "application/vnd.ms-visio.drawing.main+xml"}
IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg"}

# Content types this pipeline can actually process. Kept as a set (not an
# inline check) so app/api/documents.py can decide whether to enqueue a
# background task without duplicating this list.
SUPPORTED_CONTENT_TYPES = {PDF_CONTENT_TYPE, DOCX_CONTENT_TYPE, *VSDX_CONTENT_TYPES, *IMAGE_CONTENT_TYPES}


async def process_document(
    process_id: str,
    document_id: str,
    llm: LLMClient,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    session = get_session_factory()()

    try:
        repository.update_document_status(session, process_id, document_id, "processing")
        session.commit()

        document = repository.get_document(session, process_id, document_id)
        process = repository.get_process(session, process_id)
        data = storage.read_uploaded_file(settings, process_id, document_id, document.filename)

        blocks: list[ExtractedBlock] = []
        if document.content_type in IMAGE_CONTENT_TYPES:
            # No deterministic pre-extraction exists for images -- go
            # straight to vision-based structuring (US1.5). Nothing to
            # embed either (US2.3 embeds extraction blocks, and there are
            # none for an image).
            schema = await structure_process_from_image(
                llm,
                document_id=document_id,
                filename=document.filename,
                image_bytes=data,
                mime_type=document.content_type,
                process_name=process.name,
            )
        else:
            if document.content_type == PDF_CONTENT_TYPE:
                blocks = extract_pdf(data)
            elif document.content_type == DOCX_CONTENT_TYPE:
                blocks = extract_docx(data)
            elif document.content_type in VSDX_CONTENT_TYPES:
                blocks = extract_vsdx(data)
            else:
                raise StructuringError(f"No extractor available yet for content type '{document.content_type}'")

            schema = await structure_process(
                llm,
                document_id=document_id,
                filename=document.filename,
                blocks=blocks,
                process_name=process.name,
            )

        repository.merge_process_schema(session, process_id, schema, document_id=document_id)
        if blocks:
            repository.add_document_embeddings(session, document_id, blocks)
        session.commit()

        # Epic 11: best-effort -- a failed gap-analysis run must not fail
        # ingestion. gap_analysis_completed_at simply stays unset, which
        # Finalize (app/api/versions.py) later catches with its own clear
        # message rather than silently letting a never-checked process
        # through.
        try:
            await run_gap_analysis(session, llm, process_id)
            session.commit()
        except Exception as gap_exc:
            session.rollback()
            logger.warning(
                "gap_analysis_failed",
                extra={"process_id": process_id, "document_id": document_id, "error": str(gap_exc)},
            )

        repository.update_document_status(session, process_id, document_id, "done")
        session.commit()
    except Exception as exc:
        # This is a background task -- there is no caller to propagate an
        # exception to, so this is the terminal error boundary. Recording it
        # as a failed document status is the only way the failure becomes
        # visible (US1.7).
        session.rollback()
        logger.warning(
            "document_processing_failed",
            extra={"process_id": process_id, "document_id": document_id, "error": str(exc)},
        )
        try:
            repository.update_document_status(session, process_id, document_id, "failed", error_message=str(exc))
            session.commit()
        except Exception:
            session.rollback()
            raise
    finally:
        session.close()
