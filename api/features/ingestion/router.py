"""
Ingestion API (feature router) - Document Upload and Real-time Processing

Business capability:
- Upload requirements documents (text, PDF)
- Stream real-time progress (SSE)
- Run Event Storming extraction workflow and persist to Neo4j
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request

from api.features.ingestion.ingestion_sessions import (
    active_session_count,
    add_event,
    create_session,
    delete_session,
    get_session,
    list_active_sessions,
)
from api.features.ingestion.ingestion_workflow_runner import run_ingestion_workflow
from api.features.ingestion.requirements_document_text import extract_text_from_pdf
from api.platform.observability.request_logging import (
    http_context,
    sha256_bytes,
    sha256_text,
    summarize_for_log,
)
from api.platform.observability.smart_logger import SmartLogger

# Keep a stable import root when running the API in varied contexts (dev/prod/tests).
# Historically this module was at `api/ingestion.py` and inserted the project root.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PROJECT_ROOT and _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])


@router.post("/upload")
async def upload_document(
    request: Request,
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
) -> dict[str, Any]:
    """
    Upload a requirements document (text or PDF) to start ingestion.
    Returns a session_id for SSE streaming of progress.
    """
    content = ""

    if file:
        file_content = await file.read()
        filename = file.filename or ""
        SmartLogger.log(
            "INFO",
            "Ingestion upload received (file): reading file bytes and extracting text.",
            category="ingestion.api.upload.inputs",
            params={
                **http_context(request),
                "inputs": {
                    "file": {
                        "filename": filename,
                        "content_type": getattr(file, "content_type", None),
                        "bytes": len(file_content),
                        "sha256": sha256_bytes(file_content),
                    },
                    "text_form_provided": bool(text),
                },
            },
        )
        SmartLogger.log(
            "INFO",
            "Upload received (file)",
            category="ingestion.api.upload",
            params={"filename": filename, "bytes": len(file_content)},
        )

        if filename.lower().endswith(".pdf"):
            content = extract_text_from_pdf(file_content)
        else:
            try:
                content = file_content.decode("utf-8")
            except UnicodeDecodeError:
                content = file_content.decode("latin-1")
    elif text:
        content = text
        SmartLogger.log(
            "INFO",
            "Ingestion upload received (text): starting ingestion session from raw text.",
            category="ingestion.api.upload.inputs",
            params={**http_context(request), "inputs": {"text": summarize_for_log(text), "text_sha256": sha256_text(text)}},
        )
        SmartLogger.log("INFO", "Upload received (text)", category="ingestion.api.upload", params={"chars": len(content)})
    else:
        SmartLogger.log(
            "WARNING",
            "Ingestion upload rejected: neither 'file' nor 'text' was provided.",
            category="ingestion.api.upload.invalid",
            params=http_context(request),
        )
        raise HTTPException(status_code=400, detail="Either 'file' or 'text' must be provided")

    if not content.strip():
        SmartLogger.log(
            "WARNING",
            "Ingestion upload rejected: extracted content is empty after parsing.",
            category="ingestion.api.upload.empty",
            params={**http_context(request), "content_len": len(content)},
        )
        raise HTTPException(status_code=400, detail="Document content is empty")

    SmartLogger.log(
        "INFO",
        "Ingestion content prepared: extracted text ready for workflow.",
        category="ingestion.api.upload.content",
        params={
            **http_context(request),
            "content": {"len": len(content), "sha256": sha256_text(content), "preview": summarize_for_log(content)},
        },
    )

    session = create_session()
    session.content = content
    SmartLogger.log(
        "INFO",
        "Ingestion session created",
        category="ingestion.api.upload",
        params={"session_id": session.id, "content_length": len(content)},
    )

    return {"session_id": session.id, "content_length": len(content), "preview": content[:500] + "..." if len(content) > 500 else content}


@router.get("/stream/{session_id}")
async def stream_progress(session_id: str, request: Request):
    """
    SSE endpoint for streaming ingestion progress.
    Client should connect after receiving session_id from /upload.
    """
    session = get_session(session_id)

    if not session:
        SmartLogger.log(
            "WARNING",
            "Ingestion stream requested for missing session: client may be using an expired/invalid session_id.",
            category="ingestion.api.stream.not_found",
            params={**http_context(request), "inputs": {"session_id": session_id}, "active_sessions": active_session_count()},
        )
        raise HTTPException(status_code=404, detail="Session not found")

    SmartLogger.log(
        "INFO",
        "Ingestion stream connected: starting SSE progress events for workflow execution.",
        category="ingestion.api.stream.connected",
        params={**http_context(request), "inputs": {"session_id": session_id}},
    )

    async def event_generator():
        SmartLogger.log(
            "INFO",
            "Ingestion stream generator started: emitting 'progress' SSE events.",
            category="ingestion.api.stream.generator_start",
            params={**http_context(request), "inputs": {"session_id": session_id}},
        )
        async for event in run_ingestion_workflow(session, session.content):
            add_event(session, event)
            yield {"event": "progress", "data": event.model_dump_json()}

        delete_session(session_id)
        SmartLogger.log(
            "INFO",
            "Ingestion session cleaned up: workflow completed and session removed from memory.",
            category="ingestion.api.stream.cleaned",
            params={**http_context(request), "inputs": {"session_id": session_id}},
        )

    return EventSourceResponse(event_generator())


@router.get("/sessions")
async def list_sessions(request: Request) -> list[dict[str, Any]]:
    """List all active ingestion sessions."""
    SmartLogger.log(
        "INFO",
        "List ingestion sessions: returning in-memory active sessions.",
        category="ingestion.api.sessions.request",
        params={**http_context(request), "active": active_session_count()},
    )
    return [
        {"id": s.id, "status": s.status.value, "progress": s.progress, "message": s.message}
        for s in list_active_sessions()
    ]


@router.delete("/clear-all")
async def clear_all_data(request: Request) -> dict[str, Any]:
    """
    Clear all nodes and relationships from Neo4j. Used before starting a fresh ingestion.
    """
    from api.features.ingestion.event_storming.neo4j_client import get_neo4j_client

    client = get_neo4j_client()

    try:
        SmartLogger.log(
            "WARNING",
            "Clear-all requested: deleting all nodes/relationships from Neo4j (destructive).",
            category="ingestion.api.clear_all.request",
            params=http_context(request),
        )
        with client.session() as session:
            count_query = """
            MATCH (n)
            WITH labels(n)[0] as label, count(n) as count
            RETURN collect({label: label, count: count}) as counts
            """
            result = session.run(count_query)
            record = result.single()
            before_counts = {item["label"]: item["count"] for item in record["counts"]} if record else {}

            delete_query = """
            MATCH (n)
            DETACH DELETE n
            """
            session.run(delete_query)
            SmartLogger.log(
                "INFO",
                "Clear-all completed: Neo4j graph wiped.",
                category="ingestion.api.clear_all.done",
                params={**http_context(request), "deleted": before_counts},
            )

            return {"success": True, "message": "모든 데이터가 삭제되었습니다", "deleted": before_counts}
    except Exception as e:
        SmartLogger.log(
            "ERROR",
            "Clear-all failed: Neo4j delete operation raised an exception.",
            category="ingestion.api.clear_all.error",
            params={**http_context(request), "error": {"type": type(e).__name__, "message": str(e)}},
        )
        return {"success": False, "message": f"삭제 실패: {str(e)}", "deleted": {}}


@router.get("/stats")
async def get_data_stats(request: Request) -> dict[str, Any]:
    """
    Get current data statistics from Neo4j.
    """
    from api.features.ingestion.event_storming.neo4j_client import get_neo4j_client

    client = get_neo4j_client()

    try:
        SmartLogger.log(
            "INFO",
            "Ingestion stats requested: counting Neo4j nodes by label.",
            category="ingestion.api.stats.request",
            params=http_context(request),
        )
        with client.session() as session:
            query = """
            MATCH (n)
            WITH labels(n)[0] as label, count(n) as count
            RETURN collect({label: label, count: count}) as counts
            """
            result = session.run(query)
            record = result.single()
            counts = {item["label"]: item["count"] for item in record["counts"]} if record else {}

            total = sum(counts.values())
            SmartLogger.log(
                "INFO",
                "Ingestion stats returned.",
                category="ingestion.api.stats.done",
                params={**http_context(request), "total": total, "counts": counts},
            )

            return {"total": total, "counts": counts, "hasData": total > 0}
    except Exception as e:
        SmartLogger.log(
            "ERROR",
            "Ingestion stats failed: Neo4j count query raised an exception.",
            category="ingestion.api.stats.error",
            params={**http_context(request), "error": {"type": type(e).__name__, "message": str(e)}},
        )
        return {"total": 0, "counts": {}, "hasData": False, "error": str(e)}


