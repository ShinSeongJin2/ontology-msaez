"""
Change Management API for User Story Editing with Impact Analysis

Provides endpoints for:
- Impact analysis when a User Story is modified
- LLM-based change plan generation with LangGraph workflow
- Vector search for related objects across BCs
- Human-in-the-loop plan revision
- Applying approved changes to Neo4j

The workflow now supports:
1. Scope analysis: Determine if change is LOCAL, CROSS_BC, or NEW_CAPABILITY
2. Vector search: Find semantically related objects when cross-BC connections are needed
3. Plan generation: Create comprehensive change plan including new connections
"""

from __future__ import annotations

import os
from typing import Any, Optional, List

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from starlette.requests import Request
from neo4j import GraphDatabase
from pydantic import BaseModel, Field

from api.smart_logger import SmartLogger
from api.request_logging import http_context, summarize_for_log

load_dotenv()

router = APIRouter(prefix="/api/change", tags=["change"])

# Neo4j Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "12345msaez")
NEO4J_DATABASE = (os.getenv("NEO4J_DATABASE") or os.getenv("neo4j_database") or "").strip() or None

driver = None


def get_driver():
    global driver
    if driver is None:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return driver


def get_session():
    if NEO4J_DATABASE:
        return get_driver().session(database=NEO4J_DATABASE)
    return get_driver().session()


# =============================================================================
# Request/Response Models
# =============================================================================


class UserStoryEdit(BaseModel):
    """Edited user story data."""
    role: str
    action: str
    benefit: Optional[str] = None
    changes: List[dict] = Field(default_factory=list)


class ChangePlanRequest(BaseModel):
    """Request for generating or revising a change plan."""
    userStoryId: str
    originalUserStory: Optional[dict] = None
    editedUserStory: dict
    impactedNodes: List[dict]
    feedback: Optional[str] = None
    previousPlan: Optional[List[dict]] = None


class ChangeItem(BaseModel):
    """A single change in the plan."""
    action: str  # rename, update, create, delete
    targetType: str  # Aggregate, Command, Event, Policy
    targetId: str
    targetName: str
    from_value: Optional[str] = Field(None, alias="from")
    to_value: Optional[str] = Field(None, alias="to")
    description: str
    reason: str


class ChangePlanResponse(BaseModel):
    """Response containing the generated change plan."""
    changes: List[dict]
    summary: str


class VectorSearchRequest(BaseModel):
    """Request for vector search of related objects."""
    query: str
    nodeTypes: List[str] = Field(default_factory=lambda: ["Command", "Event", "Policy", "Aggregate"])
    excludeIds: List[str] = Field(default_factory=list)
    limit: int = 10


class VectorSearchResult(BaseModel):
    """A single result from vector search."""
    id: str
    name: str
    type: str
    bcId: Optional[str] = None
    bcName: Optional[str] = None
    similarity: float
    description: Optional[str] = None


class ApplyChangesRequest(BaseModel):
    """Request to apply approved changes."""
    userStoryId: str
    editedUserStory: dict
    changePlan: List[dict]


class ApplyChangesResponse(BaseModel):
    """Response after applying changes."""
    success: bool
    appliedChanges: List[dict]
    errors: List[str] = Field(default_factory=list)


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("/impact/{user_story_id}")
async def get_impact_analysis(user_story_id: str, request: Request) -> dict[str, Any]:
    """
    Analyze the impact of changing a User Story.
    
    Returns:
    - The original user story
    - All connected objects (Aggregate, Command, Event) that may need updates
    
    This follows multiple relationship paths:
    1. Direct IMPLEMENTS from UserStory to any node
    2. Through BoundedContext hierarchy
    3. All related Commands and Events in the same aggregate
    """
    SmartLogger.log(
        "INFO",
        "Impact analysis requested: resolving connected nodes for the given user story.",
        category="change.impact.inputs",
        params={**http_context(request), "inputs": {"user_story_id": user_story_id}},
    )
    # Query to get the user story and all connected objects
    query = """
    MATCH (us:UserStory {id: $user_story_id})
    
    // Path 1: Direct IMPLEMENTS relationships
    OPTIONAL MATCH (us)-[:IMPLEMENTS]->(directTarget)
    WHERE directTarget:Aggregate OR directTarget:Command OR directTarget:Event OR directTarget:BoundedContext
    
    // Path 2: Through BoundedContext - find the BC this user story belongs to
    OPTIONAL MATCH (us)-[:IMPLEMENTS]->(bc:BoundedContext)
    OPTIONAL MATCH (bc)-[:HAS_AGGREGATE]->(bcAgg:Aggregate)
    OPTIONAL MATCH (bcAgg)-[:HAS_COMMAND]->(bcCmd:Command)
    OPTIONAL MATCH (bcCmd)-[:EMITS]->(bcEvt:Event)
    
    // Path 3: If user story implements an aggregate, get its commands and events
    OPTIONAL MATCH (us)-[:IMPLEMENTS]->(usAgg:Aggregate)
    OPTIONAL MATCH (usAgg)-[:HAS_COMMAND]->(usAggCmd:Command)
    OPTIONAL MATCH (usAggCmd)-[:EMITS]->(usAggEvt:Event)
    
    // Path 4: If user story implements a command, get its events
    OPTIONAL MATCH (us)-[:IMPLEMENTS]->(usCmd:Command)
    OPTIONAL MATCH (usCmd)-[:EMITS]->(usCmdEvt:Event)
    OPTIONAL MATCH (usAggParent:Aggregate)-[:HAS_COMMAND]->(usCmd)
    
    WITH us,
         collect(DISTINCT bc) as bcs,
         collect(DISTINCT bcAgg) + collect(DISTINCT usAgg) + collect(DISTINCT usAggParent) as allAggs,
         collect(DISTINCT bcCmd) + collect(DISTINCT usAggCmd) + collect(DISTINCT usCmd) as allCmds,
         collect(DISTINCT bcEvt) + collect(DISTINCT usAggEvt) + collect(DISTINCT usCmdEvt) as allEvts
    
    // Get the first BC (user story typically belongs to one BC)
    WITH us, 
         CASE WHEN size(bcs) > 0 THEN bcs[0] ELSE null END as bc,
         allAggs, allCmds, allEvts
    
    RETURN {
        id: us.id,
        role: us.role,
        action: us.action,
        benefit: us.benefit,
        priority: us.priority,
        status: us.status
    } as userStory,
    bc {.id, .name, .description} as boundedContext,
    [a IN allAggs WHERE a IS NOT NULL | a {.id, .name, .rootEntity, type: 'Aggregate'}] as aggregates,
    [c IN allCmds WHERE c IS NOT NULL | c {.id, .name, .actor, type: 'Command'}] as commands,
    [e IN allEvts WHERE e IS NOT NULL | e {.id, .name, .version, type: 'Event'}] as events
    """
    
    with get_session() as session:
        SmartLogger.log(
            "INFO",
            "Impact analysis executing Neo4j query: collecting aggregates/commands/events reachable from user story.",
            category="change.impact.query",
            params={**http_context(request), "user_story_id": user_story_id},
        )
        result = session.run(query, user_story_id=user_story_id)
        record = result.single()
        
        if not record:
            SmartLogger.log(
                "WARNING",
                "Impact analysis failed: user story not found in Neo4j.",
                category="change.impact.not_found",
                params={**http_context(request), "user_story_id": user_story_id},
            )
            raise HTTPException(status_code=404, detail=f"User story {user_story_id} not found")
        
        user_story = dict(record["userStory"])
        bounded_context = dict(record["boundedContext"]) if record["boundedContext"] else None
        
        # Collect all impacted nodes
        impacted_nodes = []
        
        # Add aggregates (deduplicated)
        seen_ids = set()
        for agg in record["aggregates"]:
            if agg and agg["id"] not in seen_ids:
                impacted_nodes.append(dict(agg))
                seen_ids.add(agg["id"])
        
        # Add commands (deduplicated)
        for cmd in record["commands"]:
            if cmd and cmd["id"] not in seen_ids:
                impacted_nodes.append(dict(cmd))
                seen_ids.add(cmd["id"])
        
        # Add events (deduplicated)
        for evt in record["events"]:
            if evt and evt["id"] not in seen_ids:
                impacted_nodes.append(dict(evt))
                seen_ids.add(evt["id"])
        
        SmartLogger.log(
            "INFO",
            "Impact analysis computed: impacted nodes deduplicated and returned.",
            category="change.impact.done",
            params={
                **http_context(request),
                "user_story_id": user_story_id,
                "boundedContext": bounded_context.get("id") if bounded_context else None,
                "impactedNodes": len(impacted_nodes),
            },
        )
        return {
            "userStory": user_story,
            "boundedContext": bounded_context,
            "impactedNodes": impacted_nodes
        }


@router.post("/plan")
async def generate_change_plan(payload: ChangePlanRequest, request: Request) -> dict[str, Any]:
    """
    Generate a change plan using LangGraph-based workflow.
    
    This endpoint uses a multi-step workflow:
    1. Analyze scope: Determine if change is LOCAL, CROSS_BC, or NEW_CAPABILITY
    2. Vector search: If CROSS_BC, search for related objects in other BCs
    3. Generate plan: Create comprehensive plan including new connections
    
    If feedback is provided, it will revise the previous plan.
    
    Returns:
    - scope: The determined scope of the change
    - scopeReasoning: Why this scope was determined
    - keywords: Keywords used for vector search
    - relatedObjects: Objects found via vector search (for CROSS_BC)
    - changes: The proposed changes
    - summary: Summary of the plan
    """
    from agent.change_graph import run_change_planning
    
    try:
        SmartLogger.log(
            "INFO",
            "Generate change plan called: capturing full router inputs for reproducibility.",
            category="change.plan.inputs",
            params={
                **http_context(request),
                "inputs": summarize_for_log(payload.model_dump(by_alias=True)),
            },
        )
        SmartLogger.log(
            "INFO",
            "Generate change plan requested",
            category="change.plan",
            params={
                "userStoryId": payload.userStoryId,
                "impactedNodes": len(payload.impactedNodes),
                "hasFeedback": bool(payload.feedback),
                "hasPreviousPlan": bool(payload.previousPlan),
            },
        )
        result = run_change_planning(
            user_story_id=payload.userStoryId,
            original_user_story=payload.originalUserStory or {},
            edited_user_story=payload.editedUserStory,
            connected_objects=payload.impactedNodes,
            feedback=payload.feedback,
            previous_plan=payload.previousPlan
        )
        SmartLogger.log(
            "INFO",
            "Generate change plan completed",
            category="change.plan",
            params={
                "userStoryId": payload.userStoryId,
                "scope": result.get("scope"),
                "changes": len(result.get("changes") or []),
                "relatedObjects": len(result.get("relatedObjects") or []),
            },
        )

        # LDVC: Propagation summary for runtime verification without code review
        try:
            propagation = result.get("propagation") or {}
            SmartLogger.log(
                "INFO",
                "Propagation summary: verify iterative impact expansion (rounds/stopReason/confirmed/review) from logs alone.",
                category="change.plan.propagation.summary",
                params={
                    **http_context(request),
                    "userStoryId": payload.userStoryId,
                    "enabled": propagation.get("enabled"),
                    "rounds": propagation.get("rounds"),
                    "stopReason": propagation.get("stopReason"),
                    "confirmed_count": len(propagation.get("confirmed") or []),
                    "review_count": len(propagation.get("review") or []),
                },
            )
        except Exception:
            # Never break the endpoint due to logging.
            pass
        
        return result
        
    except Exception as e:
        import traceback
        SmartLogger.log(
            "ERROR",
            "Failed to generate change plan",
            category="change.plan",
            params={
                **http_context(request),
                "userStoryId": getattr(payload, "userStoryId", None),
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
        )
        raise HTTPException(status_code=500, detail=f"Failed to generate change plan: {str(e)}")


@router.post("/apply")
async def apply_changes(payload: ApplyChangesRequest, request: Request) -> ApplyChangesResponse:
    """
    Apply the approved change plan to Neo4j.
    
    Steps:
    1. Update the user story with new values
    2. Apply each change in the plan (rename, update, etc.)
    3. Return results of applied changes
    """
    applied_changes = []
    errors = []
    SmartLogger.log(
        "INFO",
        "Apply changes requested: capturing full router inputs for reproducibility.",
        category="change.apply.inputs",
        params={
            **http_context(request),
            "inputs": summarize_for_log(payload.model_dump(by_alias=True)),
        },
    )
    
    with get_session() as session:
        # Step 1: Update the user story
        try:
            us_query = """
            MATCH (us:UserStory {id: $user_story_id})
            SET us.role = $role,
                us.action = $action,
                us.benefit = $benefit,
                us.updatedAt = datetime()
            RETURN us.id as id
            """
            session.run(
                us_query,
                user_story_id=payload.userStoryId,
                role=payload.editedUserStory.get("role"),
                action=payload.editedUserStory.get("action"),
                benefit=payload.editedUserStory.get("benefit"),
            )
            applied_changes.append({
                "action": "update",
                "targetType": "UserStory",
                "targetId": payload.userStoryId,
                "success": True
            })
            SmartLogger.log(
                "INFO",
                "User story updated: new role/action/benefit written to Neo4j.",
                category="change.apply.user_story.updated",
                params={
                    **http_context(request),
                    "userStoryId": payload.userStoryId,
                    "editedUserStory": summarize_for_log(payload.editedUserStory),
                },
            )
        except Exception as e:
            errors.append(f"Failed to update user story: {str(e)}")
            SmartLogger.log(
                "ERROR",
                "Failed to update user story: Neo4j update raised an exception.",
                category="change.apply.user_story.error",
                params={**http_context(request), "userStoryId": payload.userStoryId, "error": str(e)},
            )
        
        # Step 2: Apply each change in the plan
        for idx, change in enumerate(payload.changePlan):
            try:
                SmartLogger.log(
                    "INFO",
                    "Applying change item from approved plan.",
                    category="change.apply.item.start",
                    params={
                        **http_context(request),
                        "userStoryId": payload.userStoryId,
                        "index": idx + 1,
                        "total": len(payload.changePlan),
                        "change": summarize_for_log(change),
                    },
                )
                if change.get("action") == "rename":
                    # Rename a node
                    rename_query = """
                    MATCH (n {id: $node_id})
                    SET n.name = $new_name, n.updatedAt = datetime()
                    RETURN n.id as id
                    """
                    session.run(
                        rename_query,
                        node_id=change.get("targetId"),
                        new_name=change.get("to")
                    )
                    applied_changes.append({
                        **change,
                        "success": True
                    })
                    SmartLogger.log(
                        "INFO",
                        "Applied change item: node renamed successfully.",
                        category="change.apply.item.renamed",
                        params={**http_context(request), "change": summarize_for_log(change)},
                    )
                    
                elif change.get("action") == "update":
                    # Update node properties
                    # Build dynamic property update
                    update_query = """
                    MATCH (n {id: $node_id})
                    SET n.description = $description, n.updatedAt = datetime()
                    RETURN n.id as id
                    """
                    session.run(
                        update_query,
                        node_id=change.get("targetId"),
                        description=change.get("description", "")
                    )
                    applied_changes.append({
                        **change,
                        "success": True
                    })
                    SmartLogger.log(
                        "INFO",
                        "Applied change item: node updated successfully.",
                        category="change.apply.item.updated",
                        params={**http_context(request), "change": summarize_for_log(change)},
                    )
                    
                elif change.get("action") == "create":
                    # Create a new node based on type
                    target_type = change.get("targetType")
                    target_id = change.get("targetId")
                    target_name = change.get("targetName")
                    target_bc_id = change.get("targetBcId")
                    
                    if target_type == "Policy":
                        create_query = """
                        MERGE (pol:Policy {id: $pol_id})
                        SET pol.name = $name,
                            pol.description = $description,
                            pol.createdAt = datetime()
                        WITH pol
                        OPTIONAL MATCH (bc:BoundedContext {id: $bc_id})
                        WHERE bc IS NOT NULL
                        MERGE (bc)-[:HAS_POLICY]->(pol)
                        RETURN pol.id as id
                        """
                        session.run(
                            create_query,
                            pol_id=target_id,
                            name=target_name,
                            description=change.get("description", ""),
                            bc_id=target_bc_id
                        )
                    elif target_type == "Command":
                        create_query = """
                        MERGE (cmd:Command {id: $cmd_id})
                        SET cmd.name = $name,
                            cmd.description = $description,
                            cmd.createdAt = datetime()
                        RETURN cmd.id as id
                        """
                        session.run(
                            create_query,
                            cmd_id=target_id,
                            name=target_name,
                            description=change.get("description", "")
                        )
                    elif target_type == "Event":
                        create_query = """
                        MERGE (evt:Event {id: $evt_id})
                        SET evt.name = $name,
                            evt.description = $description,
                            evt.version = 1,
                            evt.createdAt = datetime()
                        RETURN evt.id as id
                        """
                        session.run(
                            create_query,
                            evt_id=target_id,
                            name=target_name,
                            description=change.get("description", "")
                        )
                    else:
                        SmartLogger.log(
                            "WARNING",
                            "Create change item used an unsupported targetType: no node was created.",
                            category="change.apply.item.create.unsupported",
                            params={**http_context(request), "targetType": target_type, "change": summarize_for_log(change)},
                        )
                    
                    applied_changes.append({
                        **change,
                        "success": True
                    })
                    SmartLogger.log(
                        "INFO",
                        "Applied change item: node create attempted.",
                        category="change.apply.item.created",
                        params={**http_context(request), "change": summarize_for_log(change)},
                    )
                    
                elif change.get("action") == "connect":
                    # Create a connection between nodes
                    connection_type = change.get("connectionType", "TRIGGERS")
                    source_id = change.get("sourceId")
                    target_id = change.get("targetId")
                    
                    if connection_type == "TRIGGERS":
                        # Event -> TRIGGERS -> Policy
                        connect_query = """
                        MATCH (evt:Event {id: $source_id})
                        MATCH (pol:Policy {id: $target_id})
                        MERGE (evt)-[:TRIGGERS {priority: 1, isEnabled: true, createdAt: datetime()}]->(pol)
                        RETURN evt.id as id
                        """
                        session.run(connect_query, source_id=source_id, target_id=target_id)
                    elif connection_type == "INVOKES":
                        # Policy -> INVOKES -> Command
                        connect_query = """
                        MATCH (pol:Policy {id: $source_id})
                        MATCH (cmd:Command {id: $target_id})
                        MERGE (pol)-[:INVOKES {isAsync: true, createdAt: datetime()}]->(cmd)
                        RETURN pol.id as id
                        """
                        session.run(connect_query, source_id=source_id, target_id=target_id)
                    elif connection_type == "IMPLEMENTS":
                        # UserStory -> IMPLEMENTS -> Node
                        connect_query = """
                        MATCH (us:UserStory {id: $source_id})
                        MATCH (n {id: $target_id})
                        MERGE (us)-[:IMPLEMENTS {createdAt: datetime()}]->(n)
                        RETURN us.id as id
                        """
                        session.run(connect_query, source_id=source_id, target_id=target_id)
                    else:
                        SmartLogger.log(
                            "WARNING",
                            "Connect change item used an unsupported connectionType: no relationship was created.",
                            category="change.apply.item.connect.unsupported",
                            params={**http_context(request), "connectionType": connection_type, "change": summarize_for_log(change)},
                        )
                    
                    applied_changes.append({
                        **change,
                        "success": True
                    })
                    SmartLogger.log(
                        "INFO",
                        "Applied change item: relationship connect attempted.",
                        category="change.apply.item.connected",
                        params={**http_context(request), "change": summarize_for_log(change)},
                    )
                    
                elif change.get("action") == "delete":
                    # Delete a node (soft delete or actual delete)
                    delete_query = """
                    MATCH (n {id: $node_id})
                    SET n.deleted = true, n.deletedAt = datetime()
                    RETURN n.id as id
                    """
                    session.run(
                        delete_query,
                        node_id=change.get("targetId")
                    )
                    applied_changes.append({
                        **change,
                        "success": True
                    })
                    SmartLogger.log(
                        "INFO",
                        "Applied change item: node soft-deleted successfully.",
                        category="change.apply.item.deleted",
                        params={**http_context(request), "change": summarize_for_log(change)},
                    )
                else:
                    SmartLogger.log(
                        "WARNING",
                        "Apply skipped: change item has unsupported 'action'.",
                        category="change.apply.item.unsupported",
                        params={**http_context(request), "change": summarize_for_log(change)},
                    )
                    
            except Exception as e:
                errors.append(f"Failed to apply {change.get('action')} on {change.get('targetId')}: {str(e)}")
                applied_changes.append({
                    **change,
                    "success": False,
                    "error": str(e)
                })
                SmartLogger.log(
                    "ERROR",
                    "Failed to apply change item",
                    category="change.apply",
                    params={
                        **http_context(request),
                        "userStoryId": payload.userStoryId,
                        "action": change.get("action"),
                        "targetId": change.get("targetId"),
                        "error": str(e),
                    },
                )
    
    SmartLogger.log(
        "INFO",
        "Apply changes completed",
        category="change.apply",
        params={
            **http_context(request),
            "userStoryId": payload.userStoryId,
            "appliedChanges": len(applied_changes),
            "errors": len(errors),
        },
    )
    return ApplyChangesResponse(
        success=len(errors) == 0,
        appliedChanges=applied_changes,
        errors=errors
    )


@router.get("/history/{user_story_id}")
async def get_change_history(user_story_id: str, request: Request) -> list[dict[str, Any]]:
    """
    Get the change history for a user story.
    """
    query = """
    MATCH (us:UserStory {id: $user_story_id})
    OPTIONAL MATCH (us)-[r:CHANGED_TO]->(version)
    RETURN us {.*} as current,
           collect(version {.*, changedAt: r.changedAt}) as history
    ORDER BY r.changedAt DESC
    """
    
    with get_session() as session:
        SmartLogger.log(
            "INFO",
            "Change history requested: returning current user story and version history.",
            category="change.history.request",
            params={**http_context(request), "inputs": {"user_story_id": user_story_id}},
        )
        result = session.run(query, user_story_id=user_story_id)
        record = result.single()
        
        if not record:
            SmartLogger.log(
                "WARNING",
                "Change history not found: user story id did not match any node.",
                category="change.history.not_found",
                params={**http_context(request), "inputs": {"user_story_id": user_story_id}},
            )
            raise HTTPException(status_code=404, detail=f"User story {user_story_id} not found")
        
        payload = {
            "current": dict(record["current"]) if record["current"] else None,
            "history": [dict(h) for h in record["history"]]
        }
        SmartLogger.log(
            "INFO",
            "Change history returned.",
            category="change.history.done",
            params={
                **http_context(request),
                "user_story_id": user_story_id,
                "versions": len(payload.get("history") or []),
            },
        )
        return payload


@router.post("/search")
async def vector_search(payload: VectorSearchRequest, request: Request) -> List[VectorSearchResult]:
    """
    Search for related objects using semantic/keyword matching.
    
    This is useful for:
    - Finding objects in other BCs that might be relevant to a change
    - Discovering existing capabilities (like Notification) that can be connected
    
    Returns objects sorted by similarity score.
    """
    query = """
    UNWIND $keywords as keyword
    MATCH (n)
    WHERE (
        ($nodeTypes IS NULL OR any(t IN $nodeTypes WHERE t IN labels(n)))
    )
    AND (n:Command OR n:Event OR n:Policy OR n:Aggregate)
    AND (
        toLower(n.name) CONTAINS toLower(keyword) 
        OR toLower(coalesce(n.description, '')) CONTAINS toLower(keyword)
    )
    AND NOT n.id IN $excludeIds
    
    // Get the BC for each node
    OPTIONAL MATCH (bc:BoundedContext)-[:HAS_AGGREGATE|HAS_POLICY*1..3]->(n)
    
    WITH DISTINCT n, bc,
         CASE 
             WHEN toLower(n.name) CONTAINS toLower($primary_keyword) THEN 1.0
             WHEN toLower(n.name) CONTAINS toLower($query) THEN 0.9
             ELSE 0.7
         END as score
    
    RETURN {
        id: n.id,
        name: n.name,
        type: labels(n)[0],
        bcId: bc.id,
        bcName: bc.name,
        description: n.description,
        similarity: score
    } as result
    ORDER BY score DESC
    LIMIT $limit
    """
    
    # Extract keywords from query
    SmartLogger.log(
        "INFO",
        "Vector search requested: capturing router inputs for reproducibility.",
        category="change.search.inputs",
        params={**http_context(request), "inputs": summarize_for_log(payload.model_dump())},
    )

    keywords = [w.strip() for w in payload.query.split() if len(w.strip()) > 2]
    if not keywords:
        keywords = [payload.query]
        SmartLogger.log(
            "INFO",
            "Vector search keyword fallback: query had no tokens > 2 chars, using full query as keyword.",
            category="change.search.keyword_fallback",
            params={**http_context(request), "query": payload.query},
        )
    SmartLogger.log(
        "INFO",
        "Vector search executing Neo4j query.",
        category="change.search.query",
        params={
            **http_context(request),
            "query": payload.query,
            "keywords": keywords[:10],
            "limit": payload.limit,
            "nodeTypes": payload.nodeTypes,
            "excludeIds_count": len(payload.excludeIds or []),
        },
    )
    
    with get_session() as session:
        result = session.run(
            query,
            keywords=keywords,
            primary_keyword=keywords[0] if keywords else "",
            query=payload.query,
            nodeTypes=payload.nodeTypes if payload.nodeTypes else None,
            excludeIds=payload.excludeIds,
            limit=payload.limit
        )
        
        results = []
        seen_ids = set()
        for record in result:
            obj = record["result"]
            if obj["id"] and obj["id"] not in seen_ids:
                seen_ids.add(obj["id"])
                results.append(VectorSearchResult(
                    id=obj["id"],
                    name=obj["name"],
                    type=obj["type"],
                    bcId=obj.get("bcId"),
                    bcName=obj.get("bcName"),
                    similarity=obj.get("similarity", 0.5),
                    description=obj.get("description")
                ))
        
        SmartLogger.log(
            "INFO",
            "Vector search returned.",
            category="change.search.done",
            params={**http_context(request), "query": payload.query, "results": len(results)},
        )
        return results


@router.get("/all-nodes")
async def get_all_nodes(request: Request) -> dict[str, List[dict[str, Any]]]:
    """
    Get all nodes grouped by type for frontend reference.
    Useful for showing available connection targets.
    """
    query = """
    MATCH (bc:BoundedContext)
    OPTIONAL MATCH (bc)-[:HAS_AGGREGATE]->(agg:Aggregate)
    OPTIONAL MATCH (agg)-[:HAS_COMMAND]->(cmd:Command)
    OPTIONAL MATCH (cmd)-[:EMITS]->(evt:Event)
    OPTIONAL MATCH (bc)-[:HAS_POLICY]->(pol:Policy)
    
    WITH bc, 
         collect(DISTINCT agg {.id, .name, .rootEntity}) as aggregates,
         collect(DISTINCT cmd {.id, .name, .actor}) as commands,
         collect(DISTINCT evt {.id, .name, .version}) as events,
         collect(DISTINCT pol {.id, .name, .triggerCondition}) as policies
    
    RETURN bc {.id, .name, .description,
        aggregates: aggregates,
        commands: commands,
        events: events,
        policies: policies
    } as boundedContext
    """
    
    with get_session() as session:
        SmartLogger.log(
            "INFO",
            "All-nodes requested: returning nodes grouped by BC for frontend reference.",
            category="change.all_nodes.request",
            params=http_context(request),
        )
        result = session.run(query)
        bounded_contexts = []
        for record in result:
            bc = dict(record["boundedContext"])
            bounded_contexts.append(bc)

        SmartLogger.log(
            "INFO",
            "All-nodes returned.",
            category="change.all_nodes.done",
            params={**http_context(request), "boundedContexts": len(bounded_contexts)},
        )
        return {"boundedContexts": bounded_contexts}

