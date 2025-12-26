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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# Add parent directory to path for agent imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])


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
        raise HTTPException(
            status_code=500,
            detail="PDF processing requires PyMuPDF. Install with: pip install PyMuPDF"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {str(e)}")


# =============================================================================
# LLM Integration for User Story Extraction
# =============================================================================


def get_llm():
    """Get configured LLM instance."""
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    
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
    
    response = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt)
    ])
    
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
    from agent.neo4j_client import get_neo4j_client
    
    client = get_neo4j_client()
    
    try:
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
                
            except Exception:
                pass  # Skip if already exists
        
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
        
        from agent.nodes import BoundedContextList
        from langchain_core.messages import HumanMessage, SystemMessage
        from agent.prompts import IDENTIFY_BC_FROM_STORIES_PROMPT, SYSTEM_PROMPT
        
        llm = get_llm()
        
        stories_text = "\n".join([
            f"[{us.id}] As a {us.role}, I want to {us.action}, so that {us.benefit}"
            for us in user_stories
        ])
        
        structured_llm = llm.with_structured_output(BoundedContextList)
        prompt = IDENTIFY_BC_FROM_STORIES_PROMPT.format(user_stories=stories_text)
        
        bc_response = structured_llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt)
        ])
        
        bc_candidates = bc_response.bounded_contexts
        
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
                except Exception:
                    pass
        
        # Phase 4: Extract Aggregates
        yield ProgressEvent(
            phase=IngestionPhase.EXTRACTING_AGGREGATES,
            message="Aggregate 추출 중...",
            progress=45
        )
        
        from agent.nodes import AggregateList
        from agent.prompts import EXTRACT_AGGREGATES_PROMPT
        
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
            
            agg_response = structured_llm.invoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=prompt)
            ])
            
            aggregates = agg_response.aggregates
            all_aggregates[bc.id] = aggregates
            
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
        
        from agent.nodes import CommandList
        from agent.prompts import EXTRACT_COMMANDS_PROMPT
        
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
                    cmd_response = structured_llm.invoke([
                        SystemMessage(content=SYSTEM_PROMPT),
                        HumanMessage(content=prompt)
                    ])
                    commands = cmd_response.commands
                except Exception:
                    commands = []
                
                all_commands[agg.id] = commands
                
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
        
        from agent.nodes import EventList
        from agent.prompts import EXTRACT_EVENTS_PROMPT
        
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
                    evt_response = structured_llm.invoke([
                        SystemMessage(content=SYSTEM_PROMPT),
                        HumanMessage(content=prompt)
                    ])
                    events = evt_response.events
                except Exception:
                    events = []
                
                all_events[agg.id] = events
                
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
        
        from agent.nodes import PolicyList
        from agent.prompts import IDENTIFY_POLICIES_PROMPT
        
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
            pol_response = structured_llm.invoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=prompt)
            ])
            policies = pol_response.policies
        except Exception:
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
                except Exception:
                    pass
        
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
        
    except Exception as e:
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
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None)
) -> dict[str, Any]:
    """
    Upload a requirements document (text or PDF) to start ingestion.
    
    Returns a session_id for SSE streaming of progress.
    """
    content = ""
    
    if file:
        file_content = await file.read()
        filename = file.filename or ""
        
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
    else:
        raise HTTPException(
            status_code=400,
            detail="Either 'file' or 'text' must be provided"
        )
    
    if not content.strip():
        raise HTTPException(
            status_code=400,
            detail="Document content is empty"
        )
    
    # Create session
    session = create_session()
    session.content = content
    
    return {
        "session_id": session.id,
        "content_length": len(content),
        "preview": content[:500] + "..." if len(content) > 500 else content
    }


@router.get("/stream/{session_id}")
async def stream_progress(session_id: str):
    """
    SSE endpoint for streaming ingestion progress.
    
    Client should connect after receiving session_id from /upload.
    """
    session = get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    async def event_generator():
        async for event in run_ingestion_workflow(session, session.content):
            add_event(session, event)
            yield {
                "event": "progress",
                "data": event.model_dump_json()
            }
        
        # Clean up session after completion
        if session_id in _sessions:
            del _sessions[session_id]
    
    return EventSourceResponse(event_generator())


@router.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    """List all active ingestion sessions."""
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
async def clear_all_data() -> dict[str, Any]:
    """
    Clear all nodes and relationships from Neo4j.
    Used before starting a fresh ingestion.
    """
    from agent.neo4j_client import get_neo4j_client
    
    client = get_neo4j_client()
    
    try:
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
            
            return {
                "success": True,
                "message": "모든 데이터가 삭제되었습니다",
                "deleted": before_counts
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"삭제 실패: {str(e)}",
            "deleted": {}
        }


@router.get("/stats")
async def get_data_stats() -> dict[str, Any]:
    """
    Get current data statistics from Neo4j.
    """
    from agent.neo4j_client import get_neo4j_client
    
    client = get_neo4j_client()
    
    try:
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
            
            return {
                "total": total,
                "counts": counts,
                "hasData": total > 0
            }
    except Exception as e:
        return {
            "total": 0,
            "counts": {},
            "hasData": False,
            "error": str(e)
        }

