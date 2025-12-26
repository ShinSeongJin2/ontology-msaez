"""
Chat-based Model Modification API (feature router)

- Streaming chat-based modification of domain model objects (SSE)
- ReAct style: THOUGHT/ACTION/OBSERVATION loop with inline JSON action blocks
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from starlette.requests import Request

from api.platform.neo4j import get_session
from api.platform.observability.request_logging import http_context, summarize_for_log, sha256_text
from api.platform.observability.smart_logger import SmartLogger

router = APIRouter(prefix="/api/chat", tags=["chat"])

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = (
    os.getenv("OPENAI_MODEL")
    or os.getenv("CHAT_MODEL")
    or os.getenv("LLM_MODEL")
    or "gpt-4o"
)


# =============================================================================
# Logging Helpers
# =============================================================================


def _env_flag(key: str, default: bool = False) -> bool:
    val = (os.getenv(key) or "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


AI_AUDIT_LOG_ENABLED = _env_flag("AI_AUDIT_LOG_ENABLED", True)
AI_AUDIT_LOG_FULL_OUTPUT = _env_flag("AI_AUDIT_LOG_FULL_OUTPUT", False)


# =============================================================================
# Request/Response Models
# =============================================================================


class ModifyRequest(BaseModel):
    """Request to modify selected nodes based on a prompt."""

    prompt: str
    selectedNodes: List[Dict[str, Any]]
    conversationHistory: List[Dict[str, Any]] = Field(default_factory=list)


# =============================================================================
# ReAct Agent
# =============================================================================


REACT_SYSTEM_PROMPT = """You are an Event Storming domain model modification agent.
You help users modify their domain models based on natural language requests.

You work with these node types:
- **Command**: An action that can be performed
- **Event**: Something that happened in the domain
- **Policy**: A rule that triggers actions based on events
- **Aggregate**: A cluster of domain objects
- **BoundedContext**: A logical boundary containing aggregates

When modifying nodes, you should:
1. Understand the user's intent
2. Identify which nodes need to change
3. Determine if changes will cascade to related nodes
4. Apply changes systematically

You can perform these actions:
- **rename**: Change the name of a node
- **update**: Update properties like description
- **create**: Create a new node
- **delete**: Remove a node (soft delete)
- **connect**: Create a relationship between nodes

IMPORTANT:
- Respond in Korean when the user uses Korean. Match the user's language.
- When creating new nodes, ALWAYS include a "bcId" from the selected node context when possible.
- For "connect" actions, specify:
  - "sourceId"
  - "connectionType": "TRIGGERS" (Event→Policy), "INVOKES" (Policy→Command), or "EMITS" (Command→Event)
"""


def format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    event_data = {"type": event_type, **data}
    return f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"


def extract_section(text: str, section_name: str) -> Optional[str]:
    import re

    patterns = [
        rf"(?:💭|⚡|👁️)?\s*{section_name}:\s*(.+?)(?=(?:💭|⚡|👁️)?\s*(?:THOUGHT|ACTION|OBSERVATION|SUMMARY)|```|\n\n|$)",
        rf"{section_name}:\s*(.+?)(?=\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


async def apply_change(change: Dict[str, Any]) -> bool:
    action = change.get("action")
    target_id = change.get("targetId")
    if not action or not target_id:
        return False

    try:
        with get_session() as session:
            if action == "rename":
                session.run(
                    """
                    MATCH (n {id: $target_id})
                    SET n.name = $new_name, n.updatedAt = datetime()
                    RETURN n.id as id
                    """,
                    target_id=target_id,
                    new_name=change.get("targetName", ""),
                )
                return True

            if action == "update":
                session.run(
                    """
                    MATCH (n {id: $target_id})
                    SET n.description = $description, n.updatedAt = datetime()
                    RETURN n.id as id
                    """,
                    target_id=target_id,
                    description=change.get("description", ""),
                )
                return True

            if action == "delete":
                session.run(
                    """
                    MATCH (n {id: $target_id})
                    SET n.deleted = true, n.deletedAt = datetime()
                    RETURN n.id as id
                    """,
                    target_id=target_id,
                )
                return True

            if action == "create":
                target_type = change.get("targetType", "Command")
                target_name = change.get("targetName", "NewNode")
                bc_id = change.get("bcId") or change.get("targetBcId")

                if target_type == "Command":
                    aggregate_id = change.get("aggregateId")
                    if aggregate_id:
                        session.run(
                            """
                            MERGE (n:Command {id: $target_id})
                            SET n.name = $name, n.description = $description, n.createdAt = datetime()
                            WITH n
                            MATCH (agg:Aggregate {id: $agg_id})
                            MERGE (agg)-[:HAS_COMMAND]->(n)
                            RETURN n.id as id
                            """,
                            target_id=target_id,
                            name=target_name,
                            description=change.get("description", ""),
                            agg_id=aggregate_id,
                        )
                    else:
                        session.run(
                            """
                            MERGE (n:Command {id: $target_id})
                            SET n.name = $name, n.description = $description, n.createdAt = datetime()
                            RETURN n.id as id
                            """,
                            target_id=target_id,
                            name=target_name,
                            description=change.get("description", ""),
                        )

                elif target_type == "Event":
                    command_id = change.get("commandId")
                    if command_id:
                        session.run(
                            """
                            MERGE (n:Event {id: $target_id})
                            SET n.name = $name, n.description = $description, n.version = 1, n.createdAt = datetime()
                            WITH n
                            MATCH (cmd:Command {id: $cmd_id})
                            MERGE (cmd)-[:EMITS]->(n)
                            RETURN n.id as id
                            """,
                            target_id=target_id,
                            name=target_name,
                            description=change.get("description", ""),
                            cmd_id=command_id,
                        )
                    else:
                        session.run(
                            """
                            MERGE (n:Event {id: $target_id})
                            SET n.name = $name, n.description = $description, n.version = 1, n.createdAt = datetime()
                            RETURN n.id as id
                            """,
                            target_id=target_id,
                            name=target_name,
                            description=change.get("description", ""),
                        )

                elif target_type == "Policy":
                    if bc_id:
                        session.run(
                            """
                            MERGE (n:Policy {id: $target_id})
                            SET n.name = $name, n.description = $description, n.createdAt = datetime()
                            WITH n
                            MATCH (bc:BoundedContext {id: $bc_id})
                            MERGE (bc)-[:HAS_POLICY]->(n)
                            RETURN n.id as id
                            """,
                            target_id=target_id,
                            name=target_name,
                            description=change.get("description", ""),
                            bc_id=bc_id,
                        )
                    else:
                        session.run(
                            """
                            MERGE (n:Policy {id: $target_id})
                            SET n.name = $name, n.description = $description, n.createdAt = datetime()
                            RETURN n.id as id
                            """,
                            target_id=target_id,
                            name=target_name,
                            description=change.get("description", ""),
                        )
                else:
                    return False

                change["bcId"] = bc_id
                return True

            if action == "connect":
                source_id = change.get("sourceId")
                connection_type = change.get("connectionType", "TRIGGERS")
                if not source_id:
                    return False

                if connection_type == "TRIGGERS":
                    session.run(
                        """
                        MATCH (evt:Event {id: $source_id})
                        MATCH (pol:Policy {id: $target_id})
                        MERGE (evt)-[:TRIGGERS]->(pol)
                        RETURN evt.id as id
                        """,
                        source_id=source_id,
                        target_id=target_id,
                    )
                elif connection_type == "INVOKES":
                    session.run(
                        """
                        MATCH (pol:Policy {id: $source_id})
                        MATCH (cmd:Command {id: $target_id})
                        MERGE (pol)-[:INVOKES]->(cmd)
                        RETURN pol.id as id
                        """,
                        source_id=source_id,
                        target_id=target_id,
                    )
                elif connection_type == "EMITS":
                    session.run(
                        """
                        MATCH (cmd:Command {id: $source_id})
                        MATCH (evt:Event {id: $target_id})
                        MERGE (cmd)-[:EMITS]->(evt)
                        RETURN cmd.id as id
                        """,
                        source_id=source_id,
                        target_id=target_id,
                    )
                else:
                    return False

                return True

    except Exception:
        return False

    return False


async def stream_react_response(
    prompt: str,
    selected_nodes: List[Dict[str, Any]],
    conversation_history: List[Dict[str, Any]],
) -> AsyncGenerator[str, None]:
    try:
        if not OPENAI_API_KEY:
            yield format_sse_event(
                "error",
                {"message": "OPENAI_API_KEY가 설정되지 않았습니다. 서버 환경변수를 설정해주세요."},
            )
            return

        t0 = time.perf_counter()
        first_token_ms: int | None = None

        llm = ChatOpenAI(
            model=OPENAI_MODEL,
            temperature=0.7,
            streaming=True,
            api_key=OPENAI_API_KEY,
        )

        nodes_context = "\n".join(
            [
                f"- {node.get('type', 'Unknown')}: {node.get('name', node.get('id'))} "
                f"(ID: {node.get('id')}, BC: {node.get('bcId', 'N/A')})"
                for node in selected_nodes
            ]
        )

        messages = [SystemMessage(content=REACT_SYSTEM_PROMPT)]
        for msg in conversation_history[-5:]:
            if msg.get("type") == "user":
                messages.append(HumanMessage(content=msg.get("content", "")))
            elif msg.get("type") == "assistant":
                messages.append(AIMessage(content=msg.get("content", "")))

        current_message = f"""## Selected Nodes
{nodes_context}

## User Request
{prompt}

## Instructions
1. First, analyze what changes are needed (THOUGHT)
2. Then describe the specific actions to take (ACTION)
3. After each action, describe the result (OBSERVATION)
4. If changes cascade to other nodes, continue the ReAct loop
5. Finally, summarize all changes made

Format your response like this:
💭 THOUGHT: ...
⚡ ACTION: ...
👁️ OBSERVATION: ...
✅ SUMMARY: ...

For each change, also output a JSON block in this format:
```json
{{"action": "rename|update|create|delete|connect", "targetId": "...", "targetName": "...", "targetType": "...", "description": "...", "bcId": "BC-xxx"}}
```

For "connect" actions, include:
- "sourceId"
- "connectionType": "TRIGGERS" | "INVOKES" | "EMITS"
"""
        messages.append(HumanMessage(content=current_message))

        applied_changes: list[dict[str, Any]] = []
        buffer = ""
        raw_output = ""
        chunk_count = 0
        total_chars = 0
        json_blocks_seen = 0
        json_blocks_applied = 0
        json_decode_errors = 0

        if AI_AUDIT_LOG_ENABLED:
            SmartLogger.log(
                "INFO",
                "Chat modify: LLM call starting (streaming).",
                category="api.chat.llm.start",
                params={
                    "model": OPENAI_MODEL,
                    "temperature": 0.7,
                    "selected_nodes_count": len(selected_nodes),
                    "conversation_history_count": len(conversation_history),
                    "prompt": prompt,
                    "prompt_sha256": sha256_text(prompt),
                    "prompt_len": len(prompt),
                    "system_prompt_sha256": sha256_text(REACT_SYSTEM_PROMPT),
                    "system_prompt_len": len(REACT_SYSTEM_PROMPT),
                    "constructed_user_message": current_message,
                    "constructed_user_message_sha256": sha256_text(current_message),
                    "constructed_user_message_len": len(current_message),
                    "selected_nodes": summarize_for_log(selected_nodes),
                    "conversation_history_tail": summarize_for_log(conversation_history[-5:]),
                },
            )

        async for chunk in llm.astream(messages):
            if not chunk.content:
                continue

            buffer += chunk.content
            raw_output += chunk.content
            chunk_count += 1
            total_chars += len(chunk.content)

            if first_token_ms is None:
                first_token_ms = int((time.perf_counter() - t0) * 1000)
                if AI_AUDIT_LOG_ENABLED:
                    SmartLogger.log(
                        "INFO",
                        "Chat modify: first token received from LLM.",
                        category="api.chat.llm.first_token",
                        params={"first_token_ms": first_token_ms, "model": OPENAI_MODEL},
                    )

            if "THOUGHT:" in buffer:
                thought = extract_section(buffer, "THOUGHT")
                if thought:
                    yield format_sse_event("thought", {"content": thought})

            if "ACTION:" in buffer:
                action_txt = extract_section(buffer, "ACTION")
                if action_txt:
                    yield format_sse_event("action", {"content": action_txt})

            if "OBSERVATION:" in buffer:
                obs = extract_section(buffer, "OBSERVATION")
                if obs:
                    yield format_sse_event("observation", {"content": obs})

            # Apply JSON change blocks as they appear
            while "```json" in buffer and "```" in buffer[buffer.find("```json") + 7 :]:
                start = buffer.find("```json") + 7
                end = buffer.find("```", start)
                if end <= start:
                    break
                json_str = buffer[start:end].strip()
                try:
                    json_blocks_seen += 1
                    change = json.loads(json_str)
                    t_apply0 = time.perf_counter()
                    applied = await apply_change(change)
                    apply_ms = int((time.perf_counter() - t_apply0) * 1000)
                    if applied:
                        applied_changes.append(change)
                        json_blocks_applied += 1
                        yield format_sse_event("change", {"change": change})
                    if AI_AUDIT_LOG_ENABLED:
                        SmartLogger.log(
                            "INFO",
                            "Chat modify: change block processed.",
                            category="api.chat.change.block",
                            params={
                                "applied": applied,
                                "apply_ms": apply_ms,
                                "change": summarize_for_log(change),
                            },
                        )
                except json.JSONDecodeError:
                    json_decode_errors += 1
                    pass
                buffer = buffer[: buffer.find("```json")] + buffer[end + 3 :]

            yield format_sse_event("content", {"content": chunk.content})

        total_ms = int((time.perf_counter() - t0) * 1000)
        if AI_AUDIT_LOG_ENABLED:
            SmartLogger.log(
                "INFO",
                "Chat modify: LLM streaming completed.",
                category="api.chat.llm.done",
                params={
                    "model": OPENAI_MODEL,
                    "duration_ms": total_ms,
                    "first_token_ms": first_token_ms,
                    "stream": {"chunks": chunk_count, "chars": total_chars},
                    "json_blocks": {
                        "seen": json_blocks_seen,
                        "applied": json_blocks_applied,
                        "json_decode_errors": json_decode_errors,
                    },
                    "applied_changes": summarize_for_log(applied_changes),
                    "raw_output": (raw_output if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(raw_output)),
                    "raw_output_sha256": sha256_text(raw_output),
                    "raw_output_len": len(raw_output),
                },
            )

        yield format_sse_event(
            "complete",
            {"summary": f"완료: {len(applied_changes)}개의 변경사항이 적용되었습니다.", "appliedChanges": applied_changes},
        )

    except Exception as e:
        if AI_AUDIT_LOG_ENABLED:
            SmartLogger.log(
                "ERROR",
                "Chat modify failed: exception during streaming.",
                category="api.chat.llm.error",
                params={"error": {"type": type(e).__name__, "message": str(e)}},
            )
        yield format_sse_event("error", {"message": str(e)})


# =============================================================================
# API Endpoints
# =============================================================================


@router.post("/modify")
async def modify_nodes(request: ModifyRequest, http_request: Request):
    if not request.selectedNodes:
        raise HTTPException(status_code=400, detail="No nodes selected")
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required")

    if AI_AUDIT_LOG_ENABLED:
        SmartLogger.log(
            "INFO",
            "Chat modify requested.",
            category="api.chat.modify.request",
            params={
                **http_context(http_request),
                "inputs": {
                    "model": OPENAI_MODEL,
                    "selected_nodes_count": len(request.selectedNodes),
                    "conversation_history_count": len(request.conversationHistory or []),
                    "prompt": request.prompt,
                    "prompt_sha256": sha256_text(request.prompt),
                    "prompt_len": len(request.prompt),
                    "selectedNodes": summarize_for_log(request.selectedNodes),
                    "conversationHistory": summarize_for_log(request.conversationHistory),
                },
            },
        )

    async def generate():
        async for event in stream_react_response(request.prompt, request.selectedNodes, request.conversationHistory):
            yield event
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/node/{node_id}")
async def get_node_details(node_id: str) -> Dict[str, Any]:
    query = """
    MATCH (n {id: $node_id})

    // Find parent BC
    OPTIONAL MATCH (bc1:BoundedContext)-[:HAS_AGGREGATE]->(n)
    OPTIONAL MATCH (bc2:BoundedContext)-[:HAS_AGGREGATE]->(agg:Aggregate)-[:HAS_COMMAND]->(n)
    OPTIONAL MATCH (bc3:BoundedContext)-[:HAS_AGGREGATE]->(agg2:Aggregate)-[:HAS_COMMAND]->(cmd:Command)-[:EMITS]->(n)
    OPTIONAL MATCH (bc4:BoundedContext)-[:HAS_POLICY]->(n)

    WITH n, coalesce(bc1, bc2, bc3, bc4) as bc

    OPTIONAL MATCH (n)-[r]-(related)

    RETURN n {.*, labels: labels(n)} as node,
           bc {.id, .name, .description} as boundedContext,
           collect({
               id: related.id,
               name: related.name,
               type: labels(related)[0],
               relationship: type(r),
               direction: CASE WHEN startNode(r) = n THEN 'outgoing' ELSE 'incoming' END
           }) as relationships
    """

    with get_session() as session:
        result = session.run(query, node_id=node_id)
        record = result.single()
        if not record:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        node = dict(record["node"])
        relationships = [r for r in record["relationships"] if r.get("id")]
        bc = dict(record["boundedContext"]) if record["boundedContext"] else None

        if bc:
            node["bcId"] = bc["id"]
            node["bcName"] = bc["name"]

        return {"node": node, "boundedContext": bc, "relationships": relationships}


