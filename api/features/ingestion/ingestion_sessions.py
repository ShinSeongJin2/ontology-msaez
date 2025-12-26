"""
Ingestion Sessions (in-memory)

Business capability: track an ingestion run across upload -> streaming workflow execution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from api.features.ingestion.ingestion_contracts import CreatedObject, IngestionPhase, ProgressEvent


@dataclass
class IngestionSession:
    """Tracks state of an ingestion session."""

    id: str
    status: IngestionPhase = IngestionPhase.UPLOAD
    progress: int = 0
    message: str = ""
    events: list[dict] = field(default_factory=list)
    created_objects: list[CreatedObject] = field(default_factory=list)
    error: Optional[str] = None
    content: str = ""


# Active sessions (feature-local, in-memory)
_sessions: dict[str, IngestionSession] = {}


def get_session(session_id: str) -> Optional[IngestionSession]:
    return _sessions.get(session_id)


def create_session() -> IngestionSession:
    session_id = str(uuid.uuid4())[:8]
    session = IngestionSession(id=session_id)
    _sessions[session_id] = session
    return session


def add_event(session: IngestionSession, event: ProgressEvent) -> None:
    """Add event to session and update status."""
    session.events.append(event.model_dump())
    session.status = event.phase
    session.progress = event.progress
    session.message = event.message


def delete_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


def list_active_sessions() -> list[IngestionSession]:
    return list(_sessions.values())


def active_session_count() -> int:
    return len(_sessions)


