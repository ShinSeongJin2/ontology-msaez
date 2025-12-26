"""
Ingestion API - Document Upload and Real-time Processing

Provides:
- File upload endpoint (text, PDF)
- SSE streaming for real-time progress updates
- Integration with Event Storming workflow
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.requests import Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from api.smart_logger import SmartLogger
from api.request_logging import http_context, summarize_for_log, sha256_bytes, sha256_text

# Add parent directory to path for agent imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])


# =============================================================================
# LLM Audit Logging (prompt/output + performance)
# =============================================================================


def _env_flag(key: str, default: bool = False) -> bool:
    val = (os.getenv(key) or "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


AI_AUDIT_LOG_ENABLED = _env_flag("AI_AUDIT_LOG_ENABLED", True)
AI_AUDIT_LOG_FULL_PROMPT = _env_flag("AI_AUDIT_LOG_FULL_PROMPT", False)
AI_AUDIT_LOG_FULL_OUTPUT = _env_flag("AI_AUDIT_LOG_FULL_OUTPUT", False)


# =============================================================================
# Models
# =============================================================================


class IngestionPhase(str, Enum):
    UPLOAD = "upload"
    PARSING = "parsing"
    EXTRACTING_USER_STORIES = "extracting_user_stories"
    IDENTIFYING_BC = "identifying_bc"
    EXTRACTING_AGGREGATES = "extracting_aggregates"
    EXTRACTING_COMMANDS = "extracting_commands"
    EXTRACTING_EVENTS = "extracting_events"
    IDENTIFYING_POLICIES = "identifying_policies"
    SAVING = "saving"
    COMPLETE = "complete"
    ERROR = "error"


class ProgressEvent(BaseModel):
    """Progress event sent via SSE."""
    phase: IngestionPhase
    message: str
    progress: int  # 0-100
    data: Optional[dict] = None  # Created objects


class CreatedObject(BaseModel):
    """Information about a created DDD object."""
    id: str
    name: str
    type: str  # BoundedContext, Aggregate, Command, Event, Policy
    parent_id: Optional[str] = None
    description: Optional[str] = None


# =============================================================================
# Session Storage (In-memory for demo)
# =============================================================================


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


# Active sessions
_sessions: dict[str, IngestionSession] = {}


def get_session(session_id: str) -> Optional[IngestionSession]:
    return _sessions.get(session_id)


def create_session() -> IngestionSession:
    session_id = str(uuid.uuid4())[:8]
    session = IngestionSession(id=session_id)
    _sessions[session_id] = session
    return session


def add_event(session: IngestionSession, event: ProgressEvent):
    """Add event to session and update status."""
    session.events.append(event.model_dump())
    session.status = event.phase
    session.progress = event.progress
    session.message = event.message


# =============================================================================
# PDF Extraction
# =============================================================================


def extract_text_from_pdf(file_content: bytes) -> str:
    """Extract text from PDF file using PyMuPDF."""
    try:
        import fitz  # PyMuPDF
        
        doc = fitz.open(stream=file_content, filetype="pdf")
        text_parts = []
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text_parts.append(page.get_text())
        
        doc.close()
        return "\n".join(text_parts)
    except ImportError:
        SmartLogger.log(
            "ERROR",
            "PDF processing requires PyMuPDF (fitz import failed)",
            category="ingestion.pdf",
        )
        raise HTTPException(
            status_code=500,
            detail="PDF processing requires PyMuPDF. Install with: pip install PyMuPDF"
        )
    except Exception as e:
        SmartLogger.log("ERROR", "Failed to parse PDF", category="ingestion.pdf", params={"error": str(e)})
        raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {str(e)}")


# =============================================================================
# LLM Integration for User Story Extraction
# =============================================================================


def get_llm():
    """Get configured LLM instance."""
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    SmartLogger.log("INFO", "LLM configured", category="ingestion.llm", params={"provider": provider, "model": model})
    
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=0)
    else:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=0)


EXTRACT_USER_STORIES_PROMPT = """분석할 요구사항 문서:

{requirements}

---

위 요구사항을 분석하여 User Story 목록을 추출하세요.

지침:
1. 각 기능/요구사항을 독립적인 User Story로 변환
2. "As a [role], I want to [action], so that [benefit]" 형식 사용
3. 역할(role)은 구체적으로 (customer, seller, admin, system 등)
4. 액션(action)은 명확한 동사로 시작
5. 이점(benefit)은 비즈니스 가치 설명
6. 우선순위는 핵심 기능은 high, 부가 기능은 medium, 선택 기능은 low

User Story ID는 US-001, US-002 형식으로 순차적으로 부여하세요.
모든 주요 기능을 빠짐없이 User Story로 추출하세요.
"""


class GeneratedUserStory(BaseModel):
    """Generated User Story from requirements."""
    id: str
    role: str
    action: str
    benefit: str
    priority: str = "medium"


class UserStoryList(BaseModel):
    """List of generated user stories."""
    user_stories: list[GeneratedUserStory]


def extract_user_stories_from_text(text: str) -> list[GeneratedUserStory]:
    """Extract user stories from text using LLM."""
    from langchain_core.messages import HumanMessage, SystemMessage
    
    llm = get_llm()
    structured_llm = llm.with_structured_output(UserStoryList)
    
    system_prompt = """당신은 도메인 주도 설계(DDD) 전문가입니다. 
요구사항을 User Story로 변환하는 작업을 수행합니다.
User Story는 명확하고 테스트 가능해야 합니다."""
    
    prompt = EXTRACT_USER_STORIES_PROMPT.format(requirements=text[:8000])  # Limit context

    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    if AI_AUDIT_LOG_ENABLED:
        SmartLogger.log(
            "INFO",
            "Ingestion: extract user stories - LLM invoke starting.",
            category="ingestion.llm.user_stories.start",
            params={
                "llm": {"provider": provider, "model": model},
                "inputs": {
                    "requirements_len": len(text),
                    "requirements_sha256": sha256_text(text),
                    "requirements_truncated_len": min(len(text), 8000),
                },
                "system_len": len(system_prompt),
                "system_sha256": sha256_text(system_prompt),
                "prompt_len": len(prompt),
                "prompt_sha256": sha256_text(prompt),
                "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
            },
            max_inline_chars=1800,
        )

    t_llm0 = time.perf_counter()
    response = structured_llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=prompt)])
    llm_ms = int((time.perf_counter() - t_llm0) * 1000)

    if AI_AUDIT_LOG_ENABLED:
        try:
            resp_dump = response.model_dump() if hasattr(response, "model_dump") else response.dict()
        except Exception:
            resp_dump = {"__type__": type(response).__name__, "__repr__": repr(response)[:1000]}
        stories = getattr(response, "user_stories", []) or []
        SmartLogger.log(
            "INFO",
            "Ingestion: extract user stories - LLM invoke completed.",
            category="ingestion.llm.user_stories.done",
            params={
                "llm": {"provider": provider, "model": model},
                "llm_ms": llm_ms,
                "result": {
                    "user_stories_count": len(stories),
                    "user_story_ids": summarize_for_log([getattr(s, "id", None) for s in stories]),
                    "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                },
            },
            max_inline_chars=1800,
        )
    
    return response.user_stories


# =============================================================================
# Workflow Runner with Streaming
# =============================================================================


async def run_ingestion_workflow(
    session: IngestionSession,
    content: str
) -> AsyncGenerator[ProgressEvent, None]:
    """
    Run the full ingestion workflow with streaming progress updates.
    
    Yields ProgressEvent objects at each significant step.
    """
    from api.features.ingestion.event_storming.neo4j_client import get_neo4j_client
    
    client = get_neo4j_client()
    
    try:
        SmartLogger.log(
            "INFO",
            "Ingestion workflow started",
            category="ingestion.workflow",
            params={"session_id": session.id, "content_length": len(content)},
        )
        # Phase 1: Parsing
        yield ProgressEvent(
            phase=IngestionPhase.PARSING,
            message="문서 파싱 중...",
            progress=5
        )
        await asyncio.sleep(0.3)  # Small delay for UI feedback
        
        # Phase 2: Extract User Stories
        yield ProgressEvent(
            phase=IngestionPhase.EXTRACTING_USER_STORIES,
            message="User Story 추출 중...",
            progress=10
        )
        
        user_stories = extract_user_stories_from_text(content)
        SmartLogger.log(
            "INFO",
            "User stories extracted",
            category="ingestion.workflow.user_stories",
            params={"session_id": session.id, "count": len(user_stories)},
        )
        
        # Save user stories to Neo4j and emit events for each
        for i, us in enumerate(user_stories):
            try:
                client.create_user_story(
                    id=us.id,
                    role=us.role,
                    action=us.action,
                    benefit=us.benefit,
                    priority=us.priority,
                    status="draft"
                )
                
                # Emit event for each User Story created
                yield ProgressEvent(
                    phase=IngestionPhase.EXTRACTING_USER_STORIES,
                    message=f"User Story 생성: {us.id}",
                    progress=10 + (10 * (i + 1) // len(user_stories)),
                    data={
                        "type": "UserStory",
                        "object": {
                            "id": us.id,
                            "name": f"{us.role}: {us.action[:30]}...",
                            "type": "UserStory",
                            "role": us.role,
                            "action": us.action,
                            "benefit": us.benefit,
                            "priority": us.priority
                        }
                    }
                )
                await asyncio.sleep(0.15)  # Small delay for visual effect
                
            except Exception as e:
                # Skip if already exists (or any create error) but keep traceability.
                SmartLogger.log(
                    "WARNING",
                    "User story create skipped",
                    category="ingestion.neo4j.user_story",
                    params={"session_id": session.id, "id": us.id, "error": str(e)},
                )
        
        yield ProgressEvent(
            phase=IngestionPhase.EXTRACTING_USER_STORIES,
            message=f"{len(user_stories)}개 User Story 추출 완료",
            progress=20,
            data={
                "count": len(user_stories),
                "items": [{"id": us.id, "role": us.role, "action": us.action[:50]} for us in user_stories]
            }
        )
        
        # Phase 3: Identify Bounded Contexts
        yield ProgressEvent(
            phase=IngestionPhase.IDENTIFYING_BC,
            message="Bounded Context 식별 중...",
            progress=25
        )
        
        from api.features.ingestion.event_storming.nodes import BoundedContextList
        from langchain_core.messages import HumanMessage, SystemMessage
        from api.features.ingestion.event_storming.prompts import IDENTIFY_BC_FROM_STORIES_PROMPT, SYSTEM_PROMPT
        
        llm = get_llm()
        
        stories_text = "\n".join([
            f"[{us.id}] As a {us.role}, I want to {us.action}, so that {us.benefit}"
            for us in user_stories
        ])
        
        structured_llm = llm.with_structured_output(BoundedContextList)
        prompt = IDENTIFY_BC_FROM_STORIES_PROMPT.format(user_stories=stories_text)

        provider = os.getenv("LLM_PROVIDER", "openai")
        model = os.getenv("LLM_MODEL", "gpt-4o")
        if AI_AUDIT_LOG_ENABLED:
            SmartLogger.log(
                "INFO",
                "Ingestion: identify BCs - LLM invoke starting.",
                category="ingestion.llm.identify_bc.start",
                params={
                    "session_id": session.id,
                    "llm": {"provider": provider, "model": model},
                    "user_stories_count": len(user_stories),
                    "prompt_len": len(prompt),
                    "prompt_sha256": sha256_text(prompt),
                    "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                    "system_sha256": sha256_text(SYSTEM_PROMPT),
                },
                max_inline_chars=1800,
            )

        t_llm0 = time.perf_counter()
        bc_response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
        llm_ms = int((time.perf_counter() - t_llm0) * 1000)

        if AI_AUDIT_LOG_ENABLED:
            try:
                resp_dump = bc_response.model_dump() if hasattr(bc_response, "model_dump") else bc_response.dict()
            except Exception:
                resp_dump = {"__type__": type(bc_response).__name__, "__repr__": repr(bc_response)[:1000]}
            bcs = getattr(bc_response, "bounded_contexts", []) or []
            SmartLogger.log(
                "INFO",
                "Ingestion: identify BCs - LLM invoke completed.",
                category="ingestion.llm.identify_bc.done",
                params={
                    "session_id": session.id,
                    "llm": {"provider": provider, "model": model},
                    "llm_ms": llm_ms,
                    "result": {
                        "bounded_contexts_count": len(bcs),
                        "bounded_context_ids": summarize_for_log([getattr(bc, "id", None) for bc in bcs]),
                        "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                    },
                },
                max_inline_chars=1800,
            )
        
        bc_candidates = bc_response.bounded_contexts
        SmartLogger.log(
            "INFO",
            "Bounded contexts identified",
            category="ingestion.workflow.bc",
            params={"session_id": session.id, "count": len(bc_candidates), "ids": [bc.id for bc in bc_candidates][:10]},
        )
        
        # Create BCs in Neo4j
        for bc_idx, bc in enumerate(bc_candidates):
            client.create_bounded_context(
                id=bc.id,
                name=bc.name,
                description=bc.description
            )
            
            # Emit BC creation event
            yield ProgressEvent(
                phase=IngestionPhase.IDENTIFYING_BC,
                message=f"Bounded Context 생성: {bc.name}",
                progress=30 + (10 * bc_idx // max(len(bc_candidates), 1)),
                data={
                    "type": "BoundedContext",
                    "object": {
                        "id": bc.id,
                        "name": bc.name,
                        "type": "BoundedContext",
                        "description": bc.description,
                        "userStoryIds": bc.user_story_ids
                    }
                }
            )
            await asyncio.sleep(0.2)
            
            # Link user stories to BC and emit move events
            for us_id in bc.user_story_ids:
                try:
                    client.link_user_story_to_bc(us_id, bc.id)
                    
                    # Emit event for User Story moving to BC
                    yield ProgressEvent(
                        phase=IngestionPhase.IDENTIFYING_BC,
                        message=f"User Story {us_id} → {bc.name}",
                        progress=30 + (10 * bc_idx // max(len(bc_candidates), 1)),
                        data={
                            "type": "UserStoryAssigned",
                            "object": {
                                "id": us_id,
                                "type": "UserStory",
                                "targetBcId": bc.id,
                                "targetBcName": bc.name
                            }
                        }
                    )
                    await asyncio.sleep(0.1)
                except Exception as e:
                    SmartLogger.log(
                        "WARNING",
                        "User story to BC link skipped",
                        category="ingestion.neo4j.us_to_bc",
                        params={"session_id": session.id, "user_story_id": us_id, "bc_id": bc.id, "error": str(e)},
                    )
        
        # Phase 4: Extract Aggregates
        yield ProgressEvent(
            phase=IngestionPhase.EXTRACTING_AGGREGATES,
            message="Aggregate 추출 중...",
            progress=45
        )
        
        from api.features.ingestion.event_storming.nodes import AggregateList
        from api.features.ingestion.event_storming.prompts import EXTRACT_AGGREGATES_PROMPT
        
        all_aggregates = {}
        progress_per_bc = 10 // max(len(bc_candidates), 1)
        
        for bc_idx, bc in enumerate(bc_candidates):
            bc_id_short = bc.id.replace("BC-", "")
            
            # Create dummy breakdowns context
            breakdowns_text = f"User Stories: {', '.join(bc.user_story_ids)}"
            
            prompt = EXTRACT_AGGREGATES_PROMPT.format(
                bc_name=bc.name,
                bc_id=bc.id,
                bc_id_short=bc_id_short,
                bc_description=bc.description,
                breakdowns=breakdowns_text
            )
            
            structured_llm = llm.with_structured_output(AggregateList)

            provider = os.getenv("LLM_PROVIDER", "openai")
            model = os.getenv("LLM_MODEL", "gpt-4o")
            if AI_AUDIT_LOG_ENABLED:
                SmartLogger.log(
                    "INFO",
                    "Ingestion: extract aggregates - LLM invoke starting.",
                    category="ingestion.llm.extract_aggregates.start",
                    params={
                        "session_id": session.id,
                        "llm": {"provider": provider, "model": model},
                        "bc": {"id": bc.id, "name": bc.name},
                        "prompt_len": len(prompt),
                        "prompt_sha256": sha256_text(prompt),
                        "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                        "system_sha256": sha256_text(SYSTEM_PROMPT),
                    },
                    max_inline_chars=1800,
                )

            t_llm0 = time.perf_counter()
            agg_response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
            llm_ms = int((time.perf_counter() - t_llm0) * 1000)

            if AI_AUDIT_LOG_ENABLED:
                try:
                    resp_dump = agg_response.model_dump() if hasattr(agg_response, "model_dump") else agg_response.dict()
                except Exception:
                    resp_dump = {"__type__": type(agg_response).__name__, "__repr__": repr(agg_response)[:1000]}
                aggs = getattr(agg_response, "aggregates", []) or []
                SmartLogger.log(
                    "INFO",
                    "Ingestion: extract aggregates - LLM invoke completed.",
                    category="ingestion.llm.extract_aggregates.done",
                    params={
                        "session_id": session.id,
                        "llm": {"provider": provider, "model": model},
                        "bc": {"id": bc.id, "name": bc.name},
                        "llm_ms": llm_ms,
                        "result": {
                            "aggregates_count": len(aggs),
                            "aggregate_ids": summarize_for_log([getattr(a, "id", None) for a in aggs]),
                            "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                        },
                    },
                    max_inline_chars=1800,
                )
            
            aggregates = agg_response.aggregates
            all_aggregates[bc.id] = aggregates
            SmartLogger.log(
                "INFO",
                "Aggregates extracted",
                category="ingestion.workflow.aggregates",
                params={"session_id": session.id, "bc_id": bc.id, "bc_name": bc.name, "count": len(aggregates)},
            )
            
            for agg in aggregates:
                client.create_aggregate(
                    id=agg.id,
                    name=agg.name,
                    bc_id=bc.id,
                    root_entity=agg.root_entity,
                    invariants=agg.invariants
                )
                
                yield ProgressEvent(
                    phase=IngestionPhase.EXTRACTING_AGGREGATES,
                    message=f"Aggregate 생성: {agg.name}",
                    progress=45 + progress_per_bc * bc_idx,
                    data={
                        "type": "Aggregate",
                        "object": {
                            "id": agg.id,
                            "name": agg.name,
                            "type": "Aggregate",
                            "parentId": bc.id
                        }
                    }
                )
                await asyncio.sleep(0.15)
        
        # Phase 5: Extract Commands
        yield ProgressEvent(
            phase=IngestionPhase.EXTRACTING_COMMANDS,
            message="Command 추출 중...",
            progress=60
        )
        
        from api.features.ingestion.event_storming.nodes import CommandList
        from api.features.ingestion.event_storming.prompts import EXTRACT_COMMANDS_PROMPT
        
        all_commands = {}
        
        for bc in bc_candidates:
            bc_id_short = bc.id.replace("BC-", "")
            bc_aggregates = all_aggregates.get(bc.id, [])
            
            for agg in bc_aggregates:
                stories_context = "\n".join([
                    f"[{us.id}] As a {us.role}, I want to {us.action}"
                    for us in user_stories if us.id in bc.user_story_ids
                ])
                
                prompt = EXTRACT_COMMANDS_PROMPT.format(
                    aggregate_name=agg.name,
                    aggregate_id=agg.id,
                    bc_name=bc.name,
                    bc_short=bc_id_short,
                    user_story_context=stories_context[:2000]
                )
                
                structured_llm = llm.with_structured_output(CommandList)
                
                try:
                    provider = os.getenv("LLM_PROVIDER", "openai")
                    model = os.getenv("LLM_MODEL", "gpt-4o")
                    if AI_AUDIT_LOG_ENABLED:
                        SmartLogger.log(
                            "INFO",
                            "Ingestion: extract commands - LLM invoke starting.",
                            category="ingestion.llm.extract_commands.start",
                            params={
                                "session_id": session.id,
                                "llm": {"provider": provider, "model": model},
                                "bc": {"id": bc.id, "name": bc.name},
                                "aggregate": {"id": agg.id, "name": agg.name},
                                "prompt_len": len(prompt),
                                "prompt_sha256": sha256_text(prompt),
                                "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                                "system_sha256": sha256_text(SYSTEM_PROMPT),
                            },
                            max_inline_chars=1800,
                        )

                    t_llm0 = time.perf_counter()
                    cmd_response = structured_llm.invoke(
                        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
                    )
                    llm_ms = int((time.perf_counter() - t_llm0) * 1000)
                    commands = cmd_response.commands

                    if AI_AUDIT_LOG_ENABLED:
                        try:
                            resp_dump = (
                                cmd_response.model_dump()
                                if hasattr(cmd_response, "model_dump")
                                else cmd_response.dict()
                            )
                        except Exception:
                            resp_dump = {"__type__": type(cmd_response).__name__, "__repr__": repr(cmd_response)[:1000]}
                        SmartLogger.log(
                            "INFO",
                            "Ingestion: extract commands - LLM invoke completed.",
                            category="ingestion.llm.extract_commands.done",
                            params={
                                "session_id": session.id,
                                "llm": {"provider": provider, "model": model},
                                "bc": {"id": bc.id, "name": bc.name},
                                "aggregate": {"id": agg.id, "name": agg.name},
                                "llm_ms": llm_ms,
                                "result": {
                                    "commands_count": len(commands),
                                    "command_ids": summarize_for_log([getattr(c, "id", None) for c in commands]),
                                    "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                                },
                            },
                            max_inline_chars=1800,
                        )
                except Exception as e:
                    SmartLogger.log(
                        "WARNING",
                        "Command extraction failed (LLM)",
                        category="ingestion.workflow.commands",
                        params={"session_id": session.id, "bc_id": bc.id, "agg_id": agg.id, "error": str(e)},
                    )
                    commands = []
                
                all_commands[agg.id] = commands
                if commands:
                    SmartLogger.log(
                        "INFO",
                        "Commands extracted",
                        category="ingestion.workflow.commands",
                        params={"session_id": session.id, "agg_id": agg.id, "count": len(commands)},
                    )
                
                for cmd in commands:
                    client.create_command(
                        id=cmd.id,
                        name=cmd.name,
                        aggregate_id=agg.id,
                        actor=cmd.actor
                    )
                    
                    yield ProgressEvent(
                        phase=IngestionPhase.EXTRACTING_COMMANDS,
                        message=f"Command 생성: {cmd.name}",
                        progress=65,
                        data={
                            "type": "Command",
                            "object": {
                                "id": cmd.id,
                                "name": cmd.name,
                                "type": "Command",
                                "parentId": agg.id
                            }
                        }
                    )
                    await asyncio.sleep(0.1)
        
        # Phase 6: Extract Events
        yield ProgressEvent(
            phase=IngestionPhase.EXTRACTING_EVENTS,
            message="Event 추출 중...",
            progress=75
        )
        
        from api.features.ingestion.event_storming.nodes import EventList
        from api.features.ingestion.event_storming.prompts import EXTRACT_EVENTS_PROMPT
        
        all_events = {}
        
        for bc in bc_candidates:
            bc_id_short = bc.id.replace("BC-", "")
            bc_aggregates = all_aggregates.get(bc.id, [])
            
            for agg in bc_aggregates:
                commands = all_commands.get(agg.id, [])
                if not commands:
                    continue
                
                commands_text = "\n".join([
                    f"- {cmd.name}: {cmd.description}" if hasattr(cmd, 'description') else f"- {cmd.name}"
                    for cmd in commands
                ])
                
                prompt = EXTRACT_EVENTS_PROMPT.format(
                    aggregate_name=agg.name,
                    bc_name=bc.name,
                    bc_short=bc_id_short,
                    commands=commands_text
                )
                
                structured_llm = llm.with_structured_output(EventList)
                
                try:
                    provider = os.getenv("LLM_PROVIDER", "openai")
                    model = os.getenv("LLM_MODEL", "gpt-4o")
                    if AI_AUDIT_LOG_ENABLED:
                        SmartLogger.log(
                            "INFO",
                            "Ingestion: extract events - LLM invoke starting.",
                            category="ingestion.llm.extract_events.start",
                            params={
                                "session_id": session.id,
                                "llm": {"provider": provider, "model": model},
                                "bc": {"id": bc.id, "name": bc.name},
                                "aggregate": {"id": agg.id, "name": agg.name},
                                "prompt_len": len(prompt),
                                "prompt_sha256": sha256_text(prompt),
                                "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                                "system_sha256": sha256_text(SYSTEM_PROMPT),
                            },
                            max_inline_chars=1800,
                        )

                    t_llm0 = time.perf_counter()
                    evt_response = structured_llm.invoke(
                        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
                    )
                    llm_ms = int((time.perf_counter() - t_llm0) * 1000)
                    events = evt_response.events

                    if AI_AUDIT_LOG_ENABLED:
                        try:
                            resp_dump = (
                                evt_response.model_dump()
                                if hasattr(evt_response, "model_dump")
                                else evt_response.dict()
                            )
                        except Exception:
                            resp_dump = {"__type__": type(evt_response).__name__, "__repr__": repr(evt_response)[:1000]}
                        SmartLogger.log(
                            "INFO",
                            "Ingestion: extract events - LLM invoke completed.",
                            category="ingestion.llm.extract_events.done",
                            params={
                                "session_id": session.id,
                                "llm": {"provider": provider, "model": model},
                                "bc": {"id": bc.id, "name": bc.name},
                                "aggregate": {"id": agg.id, "name": agg.name},
                                "llm_ms": llm_ms,
                                "result": {
                                    "events_count": len(events),
                                    "event_ids": summarize_for_log([getattr(e, "id", None) for e in events]),
                                    "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                                },
                            },
                            max_inline_chars=1800,
                        )
                except Exception as e:
                    SmartLogger.log(
                        "WARNING",
                        "Event extraction failed (LLM)",
                        category="ingestion.workflow.events",
                        params={"session_id": session.id, "bc_id": bc.id, "agg_id": agg.id, "error": str(e)},
                    )
                    events = []
                
                all_events[agg.id] = events
                if events:
                    SmartLogger.log(
                        "INFO",
                        "Events extracted",
                        category="ingestion.workflow.events",
                        params={"session_id": session.id, "agg_id": agg.id, "count": len(events)},
                    )
                
                for i, evt in enumerate(events):
                    cmd_id = commands[i].id if i < len(commands) else commands[0].id if commands else None
                    
                    if cmd_id:
                        client.create_event(
                            id=evt.id,
                            name=evt.name,
                            command_id=cmd_id
                        )
                        
                        yield ProgressEvent(
                            phase=IngestionPhase.EXTRACTING_EVENTS,
                            message=f"Event 생성: {evt.name}",
                            progress=80,
                            data={
                                "type": "Event",
                                "object": {
                                    "id": evt.id,
                                    "name": evt.name,
                                    "type": "Event",
                                    "parentId": cmd_id
                                }
                            }
                        )
                        await asyncio.sleep(0.1)
        
        # Phase 7: Identify Policies
        yield ProgressEvent(
            phase=IngestionPhase.IDENTIFYING_POLICIES,
            message="Policy 식별 중...",
            progress=90
        )
        
        from api.features.ingestion.event_storming.nodes import PolicyList
        from api.features.ingestion.event_storming.prompts import IDENTIFY_POLICIES_PROMPT
        
        # Collect all events for policy identification
        all_events_list = []
        for agg_id, events in all_events.items():
            for evt in events:
                all_events_list.append(f"- {evt.name}")
        
        events_text = "\n".join(all_events_list)
        
        # Collect commands by BC
        commands_by_bc = {}
        for bc in bc_candidates:
            bc_cmds = []
            for agg in all_aggregates.get(bc.id, []):
                for cmd in all_commands.get(agg.id, []):
                    bc_cmds.append(f"- {cmd.name}")
            commands_by_bc[bc.name] = "\n".join(bc_cmds) if bc_cmds else "No commands"
        
        commands_text = "\n".join([
            f"{bc_name}:\n{cmds}" for bc_name, cmds in commands_by_bc.items()
        ])
        
        bc_text = "\n".join([
            f"- {bc.name}: {bc.description}" for bc in bc_candidates
        ])
        
        prompt = IDENTIFY_POLICIES_PROMPT.format(
            events=events_text,
            commands_by_bc=commands_text,
            bounded_contexts=bc_text
        )
        
        structured_llm = llm.with_structured_output(PolicyList)
        
        try:
            provider = os.getenv("LLM_PROVIDER", "openai")
            model = os.getenv("LLM_MODEL", "gpt-4o")
            if AI_AUDIT_LOG_ENABLED:
                SmartLogger.log(
                    "INFO",
                    "Ingestion: identify policies - LLM invoke starting.",
                    category="ingestion.llm.identify_policies.start",
                    params={
                        "session_id": session.id,
                        "llm": {"provider": provider, "model": model},
                        "bounded_contexts_count": len(bc_candidates),
                        "events_count": len(all_events_list),
                        "prompt_len": len(prompt),
                        "prompt_sha256": sha256_text(prompt),
                        "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                        "system_sha256": sha256_text(SYSTEM_PROMPT),
                    },
                    max_inline_chars=1800,
                )

            t_llm0 = time.perf_counter()
            pol_response = structured_llm.invoke(
                [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
            )
            llm_ms = int((time.perf_counter() - t_llm0) * 1000)
            policies = pol_response.policies

            if AI_AUDIT_LOG_ENABLED:
                try:
                    resp_dump = (
                        pol_response.model_dump()
                        if hasattr(pol_response, "model_dump")
                        else pol_response.dict()
                    )
                except Exception:
                    resp_dump = {"__type__": type(pol_response).__name__, "__repr__": repr(pol_response)[:1000]}
                SmartLogger.log(
                    "INFO",
                    "Ingestion: identify policies - LLM invoke completed.",
                    category="ingestion.llm.identify_policies.done",
                    params={
                        "session_id": session.id,
                        "llm": {"provider": provider, "model": model},
                        "llm_ms": llm_ms,
                        "result": {
                            "policies_count": len(policies),
                            "policy_ids": summarize_for_log([getattr(p, "id", None) for p in policies]),
                            "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                        },
                    },
                    max_inline_chars=1800,
                )
        except Exception as e:
            SmartLogger.log(
                "WARNING",
                "Policy identification failed (LLM)",
                category="ingestion.workflow.policies",
                params={"session_id": session.id, "error": str(e)},
            )
            policies = []
        
        for pol in policies:
            # Find trigger event and invoke command IDs
            trigger_event_id = None
            invoke_command_id = None
            target_bc_id = None
            
            for agg_id, events in all_events.items():
                for evt in events:
                    if evt.name == pol.trigger_event:
                        trigger_event_id = evt.id
                        break
            
            for bc in bc_candidates:
                if bc.name == pol.target_bc or bc.id == pol.target_bc:
                    target_bc_id = bc.id
                    for agg in all_aggregates.get(bc.id, []):
                        for cmd in all_commands.get(agg.id, []):
                            if cmd.name == pol.invoke_command:
                                invoke_command_id = cmd.id
                                break
            
            if trigger_event_id and invoke_command_id and target_bc_id:
                try:
                    client.create_policy(
                        id=pol.id,
                        name=pol.name,
                        bc_id=target_bc_id,
                        trigger_event_id=trigger_event_id,
                        invoke_command_id=invoke_command_id,
                        description=pol.description
                    )
                    
                    yield ProgressEvent(
                        phase=IngestionPhase.IDENTIFYING_POLICIES,
                        message=f"Policy 생성: {pol.name}",
                        progress=95,
                        data={
                            "type": "Policy",
                            "object": {
                                "id": pol.id,
                                "name": pol.name,
                                "type": "Policy",
                                "parentId": target_bc_id
                            }
                        }
                    )
                except Exception as e:
                    SmartLogger.log(
                        "WARNING",
                        "Policy create skipped",
                        category="ingestion.neo4j.policy",
                        params={"session_id": session.id, "policy_id": pol.id, "error": str(e)},
                    )
        
        # Complete
        yield ProgressEvent(
            phase=IngestionPhase.COMPLETE,
            message="✅ 모델 생성 완료!",
            progress=100,
            data={
                "summary": {
                    "user_stories": len(user_stories),
                    "bounded_contexts": len(bc_candidates),
                    "aggregates": sum(len(aggs) for aggs in all_aggregates.values()),
                    "commands": sum(len(cmds) for cmds in all_commands.values()),
                    "events": sum(len(evts) for evts in all_events.values()),
                    "policies": len(policies)
                }
            }
        )
        SmartLogger.log(
            "INFO",
            "Ingestion workflow complete",
            category="ingestion.workflow",
            params={
                "session_id": session.id,
                "user_stories": len(user_stories),
                "bounded_contexts": len(bc_candidates),
                "aggregates": sum(len(aggs) for aggs in all_aggregates.values()),
                "commands": sum(len(cmds) for cmds in all_commands.values()),
                "events": sum(len(evts) for evts in all_events.values()),
                "policies": len(policies),
            },
        )
        
    except Exception as e:
        SmartLogger.log("ERROR", "Ingestion workflow failed", category="ingestion.workflow", params={"session_id": session.id, "error": str(e)})
        yield ProgressEvent(
            phase=IngestionPhase.ERROR,
            message=f"❌ 오류 발생: {str(e)}",
            progress=0,
            data={"error": str(e)}
        )


# =============================================================================
# API Endpoints
# =============================================================================


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
        
        if filename.lower().endswith('.pdf'):
            content = extract_text_from_pdf(file_content)
        else:
            # Assume text file
            try:
                content = file_content.decode('utf-8')
            except UnicodeDecodeError:
                content = file_content.decode('latin-1')
    elif text:
        content = text
        SmartLogger.log(
            "INFO",
            "Ingestion upload received (text): starting ingestion session from raw text.",
            category="ingestion.api.upload.inputs",
            params={
                **http_context(request),
                "inputs": {
                    "text": summarize_for_log(text),
                    "text_sha256": sha256_text(text),
                },
            },
        )
        SmartLogger.log(
            "INFO",
            "Upload received (text)",
            category="ingestion.api.upload",
            params={"chars": len(content)},
        )
    else:
        SmartLogger.log(
            "WARNING",
            "Ingestion upload rejected: neither 'file' nor 'text' was provided.",
            category="ingestion.api.upload.invalid",
            params=http_context(request),
        )
        raise HTTPException(
            status_code=400,
            detail="Either 'file' or 'text' must be provided"
        )
    
    if not content.strip():
        SmartLogger.log(
            "WARNING",
            "Ingestion upload rejected: extracted content is empty after parsing.",
            category="ingestion.api.upload.empty",
            params={**http_context(request), "content_len": len(content)},
        )
        raise HTTPException(
            status_code=400,
            detail="Document content is empty"
        )

    # Log reproducible fingerprint of the content without dumping the entire document.
    SmartLogger.log(
        "INFO",
        "Ingestion content prepared: extracted text ready for workflow.",
        category="ingestion.api.upload.content",
        params={
            **http_context(request),
            "content": {
                "len": len(content),
                "sha256": sha256_text(content),
                "preview": summarize_for_log(content),
            },
        },
    )
    
    # Create session
    session = create_session()
    session.content = content
    SmartLogger.log(
        "INFO",
        "Ingestion session created",
        category="ingestion.api.upload",
        params={"session_id": session.id, "content_length": len(content)},
    )
    
    return {
        "session_id": session.id,
        "content_length": len(content),
        "preview": content[:500] + "..." if len(content) > 500 else content
    }


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
            params={**http_context(request), "inputs": {"session_id": session_id}, "active_sessions": len(_sessions)},
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
            yield {
                "event": "progress",
                "data": event.model_dump_json()
            }
        
        # Clean up session after completion
        if session_id in _sessions:
            del _sessions[session_id]
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
        params={**http_context(request), "active": len(_sessions)},
    )
    return [
        {
            "id": s.id,
            "status": s.status.value,
            "progress": s.progress,
            "message": s.message
        }
        for s in _sessions.values()
    ]


@router.delete("/clear-all")
async def clear_all_data(request: Request) -> dict[str, Any]:
    """
    Clear all nodes and relationships from Neo4j.
    Used before starting a fresh ingestion.
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
            # Get counts before deletion
            count_query = """
            MATCH (n)
            WITH labels(n)[0] as label, count(n) as count
            RETURN collect({label: label, count: count}) as counts
            """
            result = session.run(count_query)
            record = result.single()
            before_counts = {item["label"]: item["count"] for item in record["counts"]} if record else {}
            
            # Delete all nodes and relationships
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
            
            return {
                "success": True,
                "message": "모든 데이터가 삭제되었습니다",
                "deleted": before_counts
            }
    except Exception as e:
        SmartLogger.log(
            "ERROR",
            "Clear-all failed: Neo4j delete operation raised an exception.",
            category="ingestion.api.clear_all.error",
            params={**http_context(request), "error": {"type": type(e).__name__, "message": str(e)}},
        )
        return {
            "success": False,
            "message": f"삭제 실패: {str(e)}",
            "deleted": {}
        }


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
            
            return {
                "total": total,
                "counts": counts,
                "hasData": total > 0
            }
    except Exception as e:
        SmartLogger.log(
            "ERROR",
            "Ingestion stats failed: Neo4j count query raised an exception.",
            category="ingestion.api.stats.error",
            params={**http_context(request), "error": {"type": type(e).__name__, "message": str(e)}},
        )
        return {
            "total": 0,
            "counts": {},
            "hasData": False,
            "error": str(e)
        }

