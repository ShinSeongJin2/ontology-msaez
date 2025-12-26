"""
LangGraph-based Change Planning Workflow

This module implements a sophisticated change planning workflow that:
1. Analyzes if changes can be resolved within existing connections
2. Uses vector search to find related objects across the entire graph
3. Proposes connections to other BCs when needed (e.g., Notification BC)
4. Supports human-in-the-loop for plan approval and revision

Workflow Steps:
1. analyze_change_scope: Determine if change is local or requires external connections
2. search_related_objects: Vector search for semantically related objects
3. generate_connection_plan: Create plan for new connections
4. await_approval: Human-in-the-loop approval
5. apply_changes: Execute approved changes
"""

from __future__ import annotations

import os
from typing import Any, Optional, List, Dict
from enum import Enum

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

load_dotenv()


# =============================================================================
# State Definitions
# =============================================================================


class ChangeScope(str, Enum):
    """Scope of the change impact."""
    LOCAL = "local"  # Can be resolved within existing connections
    CROSS_BC = "cross_bc"  # Requires connections to other BCs
    NEW_CAPABILITY = "new_capability"  # Requires entirely new objects


class ChangePlanningPhase(str, Enum):
    """Current phase of change planning."""
    INIT = "init"
    ANALYZE_SCOPE = "analyze_scope"
    SEARCH_RELATED = "search_related"
    GENERATE_PLAN = "generate_plan"
    AWAIT_APPROVAL = "await_approval"
    REVISE_PLAN = "revise_plan"
    APPLY_CHANGES = "apply_changes"
    COMPLETE = "complete"


class ProposedChange(BaseModel):
    """A single proposed change."""
    action: str  # create, update, connect, rename
    targetType: str  # Aggregate, Command, Event, Policy
    targetId: str
    targetName: str
    targetBcId: Optional[str] = None
    targetBcName: Optional[str] = None
    description: str
    reason: str
    from_value: Optional[str] = None
    to_value: Optional[str] = None
    connectionType: Optional[str] = None  # TRIGGERS, INVOKES, etc.
    sourceId: Optional[str] = None  # For connections


class RelatedObject(BaseModel):
    """An object found via vector search."""
    id: str
    name: str
    type: str  # Aggregate, Command, Event, Policy
    bcId: Optional[str] = None
    bcName: Optional[str] = None
    similarity: float
    description: Optional[str] = None


class ChangePlanningState(BaseModel):
    """State for the change planning workflow."""
    
    # Input
    user_story_id: str = ""
    original_user_story: Dict[str, Any] = Field(default_factory=dict)
    edited_user_story: Dict[str, Any] = Field(default_factory=dict)
    change_description: str = ""  # What changed
    
    # Connected objects (from existing relationships)
    connected_objects: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Analysis results
    phase: ChangePlanningPhase = ChangePlanningPhase.INIT
    change_scope: Optional[ChangeScope] = None
    scope_reasoning: str = ""
    keywords_to_search: List[str] = Field(default_factory=list)
    
    # Vector search results
    related_objects: List[RelatedObject] = Field(default_factory=list)
    
    # Generated plan
    proposed_changes: List[ProposedChange] = Field(default_factory=list)
    plan_summary: str = ""
    
    # Human-in-the-loop
    awaiting_approval: bool = False
    human_feedback: Optional[str] = None
    revision_count: int = 0
    
    # Results
    applied_changes: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    
    class Config:
        arbitrary_types_allowed = True


# =============================================================================
# LLM and Vector Search Utilities
# =============================================================================


def get_llm():
    """Get the configured LLM instance."""
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=0)
    else:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=0)


def get_embeddings():
    """Get the embeddings model."""
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(model="text-embedding-3-small")


def get_neo4j_driver():
    """Get Neo4j driver."""
    from neo4j import GraphDatabase
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "12345msaez")
    return GraphDatabase.driver(uri, auth=(user, password))


# =============================================================================
# Node Functions
# =============================================================================


def analyze_scope_node(state: ChangePlanningState) -> Dict[str, Any]:
    """
    Analyze whether the change can be resolved within existing connections
    or requires cross-BC connections.
    """
    llm = get_llm()
    
    # Build context
    original = state.original_user_story
    edited = state.edited_user_story
    connected = state.connected_objects
    
    connected_text = "\n".join([
        f"- {obj.get('type', 'Unknown')}: {obj.get('name', '?')} (BC: {obj.get('bcName', 'Unknown')})"
        for obj in connected
    ])
    
    prompt = f"""Analyze this User Story change and determine its scope.

## Original User Story
Role: {original.get('role', 'user')}
Action: {original.get('action', '')}
Benefit: {original.get('benefit', '')}

## Modified User Story
Role: {edited.get('role', 'user')}
Action: {edited.get('action', '')}
Benefit: {edited.get('benefit', '')}

## Currently Connected Objects (in same BC)
{connected_text if connected_text else "No connected objects found"}

## Your Task
Determine the SCOPE of this change:

1. LOCAL - The change can be handled by modifying/adding objects within the currently connected BC
   Example: Changing "add to cart" to "add to cart with quantity validation"

2. CROSS_BC - The change requires connecting to or creating objects in a DIFFERENT Bounded Context
   Example: Adding "send notification" requires connecting to Notification BC
   
3. NEW_CAPABILITY - The change requires creating entirely new capabilities that don't exist yet
   Example: Adding AI-powered recommendations when no ML infrastructure exists

Also identify KEY TERMS that should be searched in the graph to find related objects.
For example, if the change mentions "notification", search for objects related to notification.

Respond in this exact JSON format:
{{
    "scope": "LOCAL" or "CROSS_BC" or "NEW_CAPABILITY",
    "reasoning": "Explanation of why this scope was chosen",
    "keywords": ["keyword1", "keyword2", ...],
    "change_description": "Brief description of what changed"
}}"""

    response = llm.invoke([
        SystemMessage(content="You are a DDD expert analyzing change impact."),
        HumanMessage(content=prompt)
    ])
    
    import json
    try:
        # Extract JSON from response
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        result = json.loads(content.strip())
        
        scope_map = {
            "LOCAL": ChangeScope.LOCAL,
            "CROSS_BC": ChangeScope.CROSS_BC,
            "NEW_CAPABILITY": ChangeScope.NEW_CAPABILITY
        }
        
        return {
            "phase": ChangePlanningPhase.SEARCH_RELATED if result["scope"] != "LOCAL" else ChangePlanningPhase.GENERATE_PLAN,
            "change_scope": scope_map.get(result["scope"], ChangeScope.LOCAL),
            "scope_reasoning": result.get("reasoning", ""),
            "keywords_to_search": result.get("keywords", []),
            "change_description": result.get("change_description", "")
        }
    except Exception as e:
        return {
            "phase": ChangePlanningPhase.GENERATE_PLAN,
            "change_scope": ChangeScope.LOCAL,
            "scope_reasoning": f"Failed to parse LLM response: {str(e)}",
            "keywords_to_search": [],
            "change_description": ""
        }


def search_related_objects_node(state: ChangePlanningState) -> Dict[str, Any]:
    """
    Use vector search to find semantically related objects across all BCs.
    """
    if not state.keywords_to_search:
        return {
            "phase": ChangePlanningPhase.GENERATE_PLAN,
            "related_objects": []
        }
    
    embeddings = get_embeddings()
    driver = get_neo4j_driver()
    
    related_objects = []
    
    try:
        # Combine keywords into a search query
        search_query = " ".join(state.keywords_to_search)
        query_embedding = embeddings.embed_query(search_query)
        
        # First, check if vector index exists and nodes have embeddings
        with driver.session() as session:
            # Try vector search if embeddings exist
            vector_search_query = """
            // First try to find objects by name similarity
            UNWIND $keywords as keyword
            MATCH (n)
            WHERE (n:Command OR n:Event OR n:Policy OR n:Aggregate)
            AND (toLower(n.name) CONTAINS toLower(keyword) 
                 OR toLower(coalesce(n.description, '')) CONTAINS toLower(keyword))
            
            // Get the BC for each node
            OPTIONAL MATCH (bc:BoundedContext)-[:HAS_AGGREGATE|HAS_POLICY*1..3]->(n)
            
            WITH DISTINCT n, bc,
                 CASE 
                     WHEN toLower(n.name) CONTAINS toLower($primary_keyword) THEN 1.0
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
            LIMIT 10
            """
            
            result = session.run(
                vector_search_query,
                keywords=state.keywords_to_search,
                primary_keyword=state.keywords_to_search[0] if state.keywords_to_search else ""
            )
            
            seen_ids = set()
            # Exclude already connected objects
            connected_ids = {obj.get('id') for obj in state.connected_objects}
            
            for record in result:
                obj = record["result"]
                if obj["id"] and obj["id"] not in seen_ids and obj["id"] not in connected_ids:
                    seen_ids.add(obj["id"])
                    related_objects.append(RelatedObject(
                        id=obj["id"],
                        name=obj["name"],
                        type=obj["type"],
                        bcId=obj.get("bcId"),
                        bcName=obj.get("bcName"),
                        similarity=obj.get("similarity", 0.5),
                        description=obj.get("description")
                    ))
    
    except Exception as e:
        print(f"Vector search error: {e}")
    finally:
        driver.close()
    
    return {
        "phase": ChangePlanningPhase.GENERATE_PLAN,
        "related_objects": related_objects
    }


def generate_plan_node(state: ChangePlanningState) -> Dict[str, Any]:
    """
    Generate a comprehensive change plan considering:
    - Changes within existing connections
    - New connections to found related objects
    - Creating new objects if needed
    """
    llm = get_llm()
    
    # Build context
    original = state.original_user_story
    edited = state.edited_user_story
    
    connected_text = "\n".join([
        f"- {obj.get('type', 'Unknown')} [{obj.get('id')}]: {obj.get('name', '?')}"
        for obj in state.connected_objects
    ])
    
    related_text = "\n".join([
        f"- {obj.type} [{obj.id}]: {obj.name} (BC: {obj.bcName}, similarity: {obj.similarity:.2f})"
        for obj in state.related_objects
    ]) if state.related_objects else "No related objects found via search"
    
    prompt = f"""Generate a change plan for this User Story modification.

## Change Scope: {state.change_scope.value if state.change_scope else 'unknown'}
{state.scope_reasoning}

## Original User Story
Role: {original.get('role', 'user')}
Action: {original.get('action', '')}
Benefit: {original.get('benefit', '')}

## Modified User Story
Role: {edited.get('role', 'user')}
Action: {edited.get('action', '')}  
Benefit: {edited.get('benefit', '')}

## Currently Connected Objects
{connected_text if connected_text else "None"}

## Related Objects Found (from other BCs)
{related_text}

## Your Task
Create a detailed change plan. Consider:

1. If CROSS_BC: Propose connections to related objects in other BCs
   - Use Policy to connect Events to Commands across BCs
   - Event from one BC TRIGGERS Policy which INVOKES Command in another BC

2. If LOCAL: Propose changes within existing objects

3. If NEW_CAPABILITY: Propose creating new objects

For each change, specify:
- action: "create", "update", "connect", or "rename"
- targetType: "Aggregate", "Command", "Event", or "Policy"
- For connections: specify connectionType (TRIGGERS, INVOKES) and sourceId

Respond in this exact JSON format:
{{
    "summary": "Brief summary of the plan",
    "changes": [
        {{
            "action": "connect",
            "targetType": "Policy",
            "targetId": "POL-NEW-POLICY-ID",
            "targetName": "PolicyName",
            "targetBcId": "BC-ID",
            "targetBcName": "BC Name",
            "description": "What this change does",
            "reason": "Why this change is needed",
            "connectionType": "TRIGGERS or INVOKES",
            "sourceId": "EVT-SOURCE-ID"
        }},
        ...
    ]
}}"""

    response = llm.invoke([
        SystemMessage(content="""You are a DDD expert creating change plans.
When connecting BCs, always use the Event-Policy-Command pattern:
- Event (from source BC) TRIGGERS Policy
- Policy INVOKES Command (in target BC)"""),
        HumanMessage(content=prompt)
    ])
    
    import json
    try:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        result = json.loads(content.strip())
        
        proposed_changes = []
        for change in result.get("changes", []):
            proposed_changes.append(ProposedChange(
                action=change.get("action", "update"),
                targetType=change.get("targetType", "Unknown"),
                targetId=change.get("targetId", ""),
                targetName=change.get("targetName", ""),
                targetBcId=change.get("targetBcId"),
                targetBcName=change.get("targetBcName"),
                description=change.get("description", ""),
                reason=change.get("reason", ""),
                from_value=change.get("from"),
                to_value=change.get("to"),
                connectionType=change.get("connectionType"),
                sourceId=change.get("sourceId")
            ))
        
        return {
            "phase": ChangePlanningPhase.AWAIT_APPROVAL,
            "proposed_changes": proposed_changes,
            "plan_summary": result.get("summary", ""),
            "awaiting_approval": True
        }
        
    except Exception as e:
        return {
            "phase": ChangePlanningPhase.AWAIT_APPROVAL,
            "proposed_changes": [],
            "plan_summary": f"Error generating plan: {str(e)}",
            "awaiting_approval": True,
            "error": str(e)
        }


def revise_plan_node(state: ChangePlanningState) -> Dict[str, Any]:
    """
    Revise the plan based on human feedback.
    """
    if not state.human_feedback:
        return {"phase": ChangePlanningPhase.AWAIT_APPROVAL}
    
    llm = get_llm()
    
    current_plan = [
        {
            "action": c.action,
            "targetType": c.targetType,
            "targetId": c.targetId,
            "targetName": c.targetName,
            "description": c.description,
            "reason": c.reason
        }
        for c in state.proposed_changes
    ]
    
    import json
    
    prompt = f"""Revise this change plan based on user feedback.

## Current Plan
{json.dumps(current_plan, indent=2)}

## User Feedback
{state.human_feedback}

## Context
- User Story ID: {state.user_story_id}
- Original Action: {state.original_user_story.get('action', '')}
- New Action: {state.edited_user_story.get('action', '')}

## Related Objects Available
{chr(10).join([f"- {obj.type}: {obj.name} (BC: {obj.bcName})" for obj in state.related_objects])}

Provide the revised plan in the same JSON format:
{{
    "summary": "Brief summary of revised plan",
    "changes": [...]
}}"""

    response = llm.invoke([
        SystemMessage(content="You are revising a change plan based on user feedback."),
        HumanMessage(content=prompt)
    ])
    
    try:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        result = json.loads(content.strip())
        
        proposed_changes = []
        for change in result.get("changes", []):
            proposed_changes.append(ProposedChange(
                action=change.get("action", "update"),
                targetType=change.get("targetType", "Unknown"),
                targetId=change.get("targetId", ""),
                targetName=change.get("targetName", ""),
                targetBcId=change.get("targetBcId"),
                targetBcName=change.get("targetBcName"),
                description=change.get("description", ""),
                reason=change.get("reason", ""),
                connectionType=change.get("connectionType"),
                sourceId=change.get("sourceId")
            ))
        
        return {
            "phase": ChangePlanningPhase.AWAIT_APPROVAL,
            "proposed_changes": proposed_changes,
            "plan_summary": result.get("summary", ""),
            "awaiting_approval": True,
            "human_feedback": None,
            "revision_count": state.revision_count + 1
        }
        
    except Exception as e:
        return {
            "phase": ChangePlanningPhase.AWAIT_APPROVAL,
            "error": str(e)
        }


def apply_changes_node(state: ChangePlanningState) -> Dict[str, Any]:
    """
    Apply the approved changes to Neo4j.
    """
    driver = get_neo4j_driver()
    applied_changes = []
    
    try:
        with driver.session() as session:
            # Update user story
            session.run("""
                MATCH (us:UserStory {id: $us_id})
                SET us.role = $role,
                    us.action = $action,
                    us.benefit = $benefit,
                    us.updatedAt = datetime()
            """, 
                us_id=state.user_story_id,
                role=state.edited_user_story.get("role"),
                action=state.edited_user_story.get("action"),
                benefit=state.edited_user_story.get("benefit")
            )
            applied_changes.append({
                "action": "update",
                "targetType": "UserStory",
                "targetId": state.user_story_id,
                "success": True
            })
            
            # Apply each proposed change
            for change in state.proposed_changes:
                try:
                    if change.action == "connect" and change.connectionType == "TRIGGERS":
                        # Create Event -> TRIGGERS -> Policy connection
                        session.run("""
                            MATCH (evt:Event {id: $source_id})
                            MATCH (pol:Policy {id: $target_id})
                            MERGE (evt)-[:TRIGGERS {priority: 1, isEnabled: true}]->(pol)
                        """, source_id=change.sourceId, target_id=change.targetId)
                        
                    elif change.action == "connect" and change.connectionType == "INVOKES":
                        # Create Policy -> INVOKES -> Command connection
                        session.run("""
                            MATCH (pol:Policy {id: $source_id})
                            MATCH (cmd:Command {id: $target_id})
                            MERGE (pol)-[:INVOKES {isAsync: true}]->(cmd)
                        """, source_id=change.sourceId, target_id=change.targetId)
                        
                    elif change.action == "create":
                        # Create new node based on type
                        if change.targetType == "Policy":
                            session.run("""
                                MATCH (bc:BoundedContext {id: $bc_id})
                                MERGE (pol:Policy {id: $pol_id})
                                SET pol.name = $name,
                                    pol.description = $description,
                                    pol.createdAt = datetime()
                                MERGE (bc)-[:HAS_POLICY]->(pol)
                            """, 
                                bc_id=change.targetBcId,
                                pol_id=change.targetId,
                                name=change.targetName,
                                description=change.description
                            )
                        # Add more create cases as needed
                    
                    elif change.action == "update":
                        session.run("""
                            MATCH (n {id: $node_id})
                            SET n.name = $name, n.updatedAt = datetime()
                        """, node_id=change.targetId, name=change.targetName)
                    
                    applied_changes.append({
                        "action": change.action,
                        "targetType": change.targetType,
                        "targetId": change.targetId,
                        "success": True
                    })
                    
                except Exception as e:
                    applied_changes.append({
                        "action": change.action,
                        "targetType": change.targetType,
                        "targetId": change.targetId,
                        "success": False,
                        "error": str(e)
                    })
    
    finally:
        driver.close()
    
    return {
        "phase": ChangePlanningPhase.COMPLETE,
        "applied_changes": applied_changes,
        "awaiting_approval": False
    }


# =============================================================================
# Routing Functions
# =============================================================================


def route_after_scope_analysis(state: ChangePlanningState) -> str:
    """Route based on change scope."""
    if state.change_scope in [ChangeScope.CROSS_BC, ChangeScope.NEW_CAPABILITY]:
        return "search_related"
    return "generate_plan"


def route_after_approval(state: ChangePlanningState) -> str:
    """Route based on human approval."""
    if state.human_feedback:
        if state.human_feedback.upper() == "APPROVED":
            return "apply_changes"
        else:
            return "revise_plan"
    return "await_approval"


# =============================================================================
# Graph Builder
# =============================================================================


def create_change_planning_graph(checkpointer=None):
    """Create the change planning workflow graph."""
    
    graph = StateGraph(ChangePlanningState)
    
    # Add nodes
    graph.add_node("analyze_scope", analyze_scope_node)
    graph.add_node("search_related", search_related_objects_node)
    graph.add_node("generate_plan", generate_plan_node)
    graph.add_node("revise_plan", revise_plan_node)
    graph.add_node("apply_changes", apply_changes_node)
    
    # Set entry point
    graph.set_entry_point("analyze_scope")
    
    # Add edges
    graph.add_conditional_edges(
        "analyze_scope",
        route_after_scope_analysis,
        {
            "search_related": "search_related",
            "generate_plan": "generate_plan"
        }
    )
    
    graph.add_edge("search_related", "generate_plan")
    graph.add_edge("generate_plan", END)  # Pause for approval
    graph.add_edge("revise_plan", END)  # Pause for re-approval
    graph.add_edge("apply_changes", END)
    
    if checkpointer is None:
        checkpointer = MemorySaver()
    
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=[]  # We handle approval in API layer
    )


# =============================================================================
# Runner Class
# =============================================================================


class ChangePlanningRunner:
    """Runner for the change planning workflow."""
    
    def __init__(self, thread_id: str = "default"):
        self.checkpointer = MemorySaver()
        self.graph = create_change_planning_graph(self.checkpointer)
        self.thread_id = thread_id
        self.config = {"configurable": {"thread_id": thread_id}}
        self._current_state: Optional[ChangePlanningState] = None
    
    def start(
        self,
        user_story_id: str,
        original_user_story: Dict[str, Any],
        edited_user_story: Dict[str, Any],
        connected_objects: List[Dict[str, Any]]
    ) -> ChangePlanningState:
        """Start the change planning workflow."""
        
        initial_state = ChangePlanningState(
            user_story_id=user_story_id,
            original_user_story=original_user_story,
            edited_user_story=edited_user_story,
            connected_objects=connected_objects,
            phase=ChangePlanningPhase.INIT
        )
        
        # Run until we need approval
        for event in self.graph.stream(initial_state, self.config, stream_mode="values"):
            self._current_state = ChangePlanningState(**event) if isinstance(event, dict) else event
        
        return self._current_state
    
    def provide_feedback(self, feedback: str) -> ChangePlanningState:
        """Provide feedback and continue."""
        if self._current_state is None:
            raise ValueError("Workflow not started")
        
        # Update state
        self.graph.update_state(
            self.config,
            {"human_feedback": feedback, "awaiting_approval": False}
        )
        
        # Determine next action
        if feedback.upper() == "APPROVED":
            # Run apply_changes
            self.graph.update_state(self.config, {"phase": ChangePlanningPhase.APPLY_CHANGES})
            result = self.graph.invoke(None, self.config)
        else:
            # Run revision
            self.graph.update_state(self.config, {"phase": ChangePlanningPhase.REVISE_PLAN})
            result = self.graph.invoke(None, self.config)
        
        self._current_state = ChangePlanningState(**result) if isinstance(result, dict) else result
        return self._current_state
    
    def get_state(self) -> Optional[ChangePlanningState]:
        """Get current state."""
        return self._current_state


# =============================================================================
# API Helper Functions
# =============================================================================


def run_change_planning(
    user_story_id: str,
    original_user_story: Dict[str, Any],
    edited_user_story: Dict[str, Any],
    connected_objects: List[Dict[str, Any]],
    feedback: Optional[str] = None,
    previous_plan: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Run the change planning workflow and return the plan.
    
    This is the main entry point for the API.
    """
    import uuid
    
    thread_id = str(uuid.uuid4())
    runner = ChangePlanningRunner(thread_id)
    
    if feedback and previous_plan:
        # This is a revision request
        # Reconstruct state and run revision
        state = ChangePlanningState(
            user_story_id=user_story_id,
            original_user_story=original_user_story,
            edited_user_story=edited_user_story,
            connected_objects=connected_objects,
            proposed_changes=[ProposedChange(**c) for c in previous_plan],
            human_feedback=feedback,
            phase=ChangePlanningPhase.REVISE_PLAN
        )
        
        # Run just the revision node
        result = revise_plan_node(state)
        return {
            "scope": state.change_scope.value if state.change_scope else "local",
            "scopeReasoning": state.scope_reasoning,
            "relatedObjects": [obj.dict() for obj in state.related_objects],
            "changes": [c.dict() for c in result.get("proposed_changes", [])],
            "summary": result.get("plan_summary", "")
        }
    
    # Start fresh planning
    final_state = runner.start(
        user_story_id=user_story_id,
        original_user_story=original_user_story,
        edited_user_story=edited_user_story,
        connected_objects=connected_objects
    )
    
    return {
        "scope": final_state.change_scope.value if final_state.change_scope else "local",
        "scopeReasoning": final_state.scope_reasoning,
        "keywords": final_state.keywords_to_search,
        "relatedObjects": [obj.dict() for obj in final_state.related_objects],
        "changes": [c.dict() for c in final_state.proposed_changes],
        "summary": final_state.plan_summary
    }

