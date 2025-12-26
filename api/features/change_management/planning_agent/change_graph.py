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
import json
import time
from collections import Counter
from typing import Any, Optional, List, Dict
from enum import Enum

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from api.smart_logger import SmartLogger
from api.request_logging import summarize_for_log, sha256_text

load_dotenv()


# =============================================================================
# LLM Audit Logging (prompt/output + performance)
# =============================================================================


def _env_flag(key: str, default: bool = False) -> bool:
    val = (os.getenv(key) or "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


# Global toggles (shared across backend modules)
AI_AUDIT_LOG_ENABLED = _env_flag("AI_AUDIT_LOG_ENABLED", True)
AI_AUDIT_LOG_FULL_PROMPT = _env_flag("AI_AUDIT_LOG_FULL_PROMPT", False)
AI_AUDIT_LOG_FULL_OUTPUT = _env_flag("AI_AUDIT_LOG_FULL_OUTPUT", False)


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
    PROPAGATE_IMPACTS = "propagate_impacts"
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


class PropagationCandidate(BaseModel):
    """
    A candidate node identified by propagation as potentially impacted by the change.
    """

    id: str
    type: str
    name: str
    bcId: Optional[str] = None
    bcName: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    evidence_paths: List[str] = Field(default_factory=list)
    suggested_change_type: str = "unknown"  # rename/update/create/connect/delete/unknown
    round: int = 0  # Which round this candidate was identified in (0 = seed)


class ChangePlanningState(BaseModel):
    """State for the change planning workflow."""
    
    # Input
    user_story_id: str = ""
    original_user_story: Dict[str, Any] = Field(default_factory=dict)
    edited_user_story: Dict[str, Any] = Field(default_factory=dict)
    change_description: str = ""  # What changed
    
    # Connected objects (from existing relationships)
    connected_objects: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Propagation (iterative impact expansion)
    propagation_enabled: bool = True
    propagation_confirmed: List[PropagationCandidate] = Field(default_factory=list)
    propagation_review: List[PropagationCandidate] = Field(default_factory=list)
    propagation_rounds: int = 0
    propagation_stop_reason: str = ""
    propagation_debug: Dict[str, Any] = Field(default_factory=dict)

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


def get_neo4j_database() -> str | None:
    """Get target Neo4j database name (multi-database support)."""
    db = (os.getenv("NEO4J_DATABASE") or os.getenv("neo4j_database") or "").strip()
    return db or None


def neo4j_session(driver):
    """Create a session for the configured database (or default)."""
    db = get_neo4j_database()
    return driver.session(database=db) if db else driver.session()


# =============================================================================
# Propagation Utilities (2-hop subgraph + iterative expansion)
# =============================================================================


def _extract_json_from_llm_text(text: str) -> str:
    """
    Extract JSON payload from an LLM response that may contain markdown fences.
    """
    if not text:
        return ""
    content = text
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0]
    return content.strip()


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _relationship_whitelist() -> List[str]:
    """
    Relationship whitelist used for 2-hop propagation context.
    Defaults align with p_local/poc/1_poc_propagation.md.
    """
    raw = os.getenv(
        "CHANGE_PROPAGATION_REL_WHITELIST",
        "IMPLEMENTS,HAS_AGGREGATE,HAS_COMMAND,EMITS,HAS_POLICY,TRIGGERS,INVOKES",
    )
    return [x.strip() for x in raw.split(",") if x.strip()]


def _propagation_limits() -> Dict[str, Any]:
    """
    Stop rules / budget limits (sane defaults for PoC).
    """
    def _env_int(key: str, default: int) -> int:
        try:
            return int((os.getenv(key) or "").strip() or default)
        except Exception:
            return default

    def _env_float(key: str, default: float) -> float:
        try:
            return float((os.getenv(key) or "").strip() or default)
        except Exception:
            return default

    return {
        "max_rounds": _env_int("CHANGE_PROPAGATION_MAX_ROUNDS", 4),
        "max_confirmed_nodes": _env_int("CHANGE_PROPAGATION_MAX_CONFIRMED", 60),
        "max_new_per_round": _env_int("CHANGE_PROPAGATION_MAX_NEW_PER_ROUND", 20),
        "max_frontier_per_round": _env_int("CHANGE_PROPAGATION_MAX_FRONTIER_PER_ROUND", 8),
        "confidence_confirmed": _env_float("CHANGE_PROPAGATION_CONFIRMED_THRESHOLD", 0.70),
        "confidence_review": _env_float("CHANGE_PROPAGATION_REVIEW_THRESHOLD", 0.40),
    }


def _get_node_contexts(session, node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Resolve (bcId, bcName) for each node id in one query.
    """
    if not node_ids:
        return {}

    query = """
    UNWIND $node_ids as node_id
    MATCH (n {id: node_id})
    WITH n, labels(n)[0] as nodeType, node_id

    // Find parent BC based on known containment patterns
    OPTIONAL MATCH (bc1:BoundedContext {id: node_id})
    OPTIONAL MATCH (bc2:BoundedContext)-[:HAS_AGGREGATE]->(n)
    OPTIONAL MATCH (bc3:BoundedContext)-[:HAS_AGGREGATE]->(:Aggregate)-[:HAS_COMMAND]->(n)
    OPTIONAL MATCH (bc4:BoundedContext)-[:HAS_AGGREGATE]->(:Aggregate)-[:HAS_COMMAND]->(:Command)-[:EMITS]->(n)
    OPTIONAL MATCH (bc5:BoundedContext)-[:HAS_POLICY]->(n)

    WITH n, nodeType, coalesce(bc1, bc2, bc3, bc4, bc5) as bc
    RETURN collect({
        nodeId: n.id,
        nodeType: nodeType,
        bcId: bc.id,
        bcName: bc.name
    }) as results
    """
    rec = session.run(query, node_ids=node_ids).single()
    if not rec:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in rec["results"] or []:
        nid = row.get("nodeId")
        if nid:
            out[nid] = row
    return out


def _fetch_2hop_subgraph(session, node_id: str, rel_types: List[str]) -> Dict[str, Any]:
    """
    Fetch a 2-hop context subgraph around a node using a whitelist of relationship types.

    Returns {nodes: [...], relationships: [...]} where relationships preserve direction.
    """
    if not node_id:
        return {"nodes": [], "relationships": []}

    # Cypher does not allow parameterized relationship type lists, so we embed the pattern.
    rel_pattern = "|".join(rel_types) if rel_types else ""
    if not rel_pattern:
        return {"nodes": [], "relationships": []}

    query = f"""
    MATCH (center {{id: $node_id}})
    OPTIONAL MATCH p=(center)-[r:{rel_pattern}*1..2]-(n)
    WITH center, [p in collect(p) WHERE p IS NOT NULL] as ps
    WITH center,
         CASE
            WHEN size(ps) = 0 THEN [center]
            ELSE reduce(allNodes = [], p in ps | allNodes + nodes(p))
         END as node_list,
         CASE
            WHEN size(ps) = 0 THEN []
            ELSE reduce(allRels = [], p in ps | allRels + relationships(p))
         END as rel_list

    UNWIND node_list as nd
    WITH collect(DISTINCT nd) as nodes, rel_list

    UNWIND (CASE WHEN size(rel_list) = 0 THEN [null] ELSE rel_list END) as rl
    WITH nodes, collect(DISTINCT rl) as rels
    WITH nodes, [r IN rels WHERE r IS NOT NULL] as rels

    RETURN
      [n in nodes | {{
        id: n.id,
        type: labels(n)[0],
        name: coalesce(n.name, ''),
        description: coalesce(n.description, ''),
        properties: properties(n)
      }}] as nodes,
      [r in rels | {{
        source: startNode(r).id,
        target: endNode(r).id,
        type: type(r),
        properties: properties(r)
      }}] as relationships
    """

    record = session.run(query, node_id=node_id).single()
    if not record:
        return {"nodes": [], "relationships": []}
    nodes = record["nodes"] or []
    relationships = record["relationships"] or []

    # Enrich nodes with BC context (bcId/bcName) for better cross-BC reasoning
    node_ids = [n.get("id") for n in nodes if n.get("id")]
    ctx = _get_node_contexts(session, node_ids)
    for n in nodes:
        nid = n.get("id")
        if nid and nid in ctx:
            n["bcId"] = ctx[nid].get("bcId")
            n["bcName"] = ctx[nid].get("bcName")

    return {"nodes": nodes, "relationships": relationships}


def _format_subgraph_for_prompt(center_id: str, subgraph: Dict[str, Any], max_nodes: int = 60, max_rels: int = 120) -> str:
    nodes = subgraph.get("nodes") or []
    rels = subgraph.get("relationships") or []
    nodes = nodes[:max_nodes]
    rels = rels[:max_rels]

    node_lines = []
    for n in nodes:
        node_lines.append(
            f"- {n.get('type','?')} [{n.get('id','?')}]: {n.get('name','')} (BC: {n.get('bcName') or 'Unknown'})"
        )

    rel_lines = []
    for r in rels:
        rel_lines.append(f"- {r.get('source')} -{r.get('type')}-> {r.get('target')}")

    return (
        f"### Center: {center_id}\n"
        f"Nodes ({len(nodes)}):\n" + ("\n".join(node_lines) if node_lines else "None") + "\n\n"
        f"Relationships ({len(rels)}):\n" + ("\n".join(rel_lines) if rel_lines else "None")
    )


def _propagation_prompt(
    edited_user_story: Dict[str, Any],
    change_description: str,
    centers_context_text: str,
    max_new: int,
) -> str:
    return f"""You are acting as a graph-based impact propagation engine for an Event Storming model.

Your job is to identify additional impacted nodes (2nd~N-th order) caused by the modified User Story.
You MUST only propose candidates that exist in the provided context subgraphs (by id).

## Modified User Story
Role: {edited_user_story.get('role', 'user')}
Action: {edited_user_story.get('action', '')}
Benefit: {edited_user_story.get('benefit', '')}
Change description: {change_description}

## Context subgraphs (2-hop, whitelist relationships, includes BC context)
{centers_context_text}

## Rules
- Propose at most {max_new} NEW candidates (ids not already seen) this round.
- For each candidate, include a confidence in [0,1].
- Provide at least 1 evidence path string using relationship types, e.g.:
  CMD-X -EMITS-> EVT-Y -TRIGGERS-> POL-Z
- If evidence is weak or the candidate is speculative, set lower confidence (<0.70).
- suggested_change_type should be one of: rename, update, create, connect, delete, unknown.

## Output JSON (exactly this shape)
{{
  "candidates": [
    {{
      "id": "NODE-ID",
      "type": "Command|Event|Policy|Aggregate|BoundedContext|UserStory|...",
      "name": "Node name",
      "confidence": 0.0,
      "reason": "Why this node is impacted",
      "evidence_paths": ["..."],
      "suggested_change_type": "update"
    }}
  ]
}}
"""


def propagate_impacts_node(state: ChangePlanningState) -> Dict[str, Any]:
    """
    Iteratively expand impacted node candidates (2nd~N-th order) using 2-hop graph contexts.

    Option 1 from PoC: inserted inside /plan (LangGraph workflow), not as a separate endpoint.
    """
    enabled = (os.getenv("CHANGE_PROPAGATION_ENABLED", "true").strip().lower() in ["1", "true", "yes", "y"])
    if not enabled:
        SmartLogger.log(
            "INFO",
            "Impact propagation skipped: CHANGE_PROPAGATION_ENABLED is disabled, so the plan will be generated without iterative expansion.",
            category="agent.change_graph.propagation.skipped",
            params={
                "user_story_id": state.user_story_id,
                "scope": state.change_scope.value if state.change_scope else None,
                "reason": "disabled_by_env",
            },
        )
        return {
            "phase": ChangePlanningPhase.SEARCH_RELATED if state.change_scope in [ChangeScope.CROSS_BC, ChangeScope.NEW_CAPABILITY] else ChangePlanningPhase.GENERATE_PLAN,
            "propagation_enabled": False,
            "propagation_confirmed": [],
            "propagation_review": [],
            "propagation_rounds": 0,
            "propagation_stop_reason": "disabled",
            "propagation_debug": {"enabled": False},
        }

    limits = _propagation_limits()
    rel_types = _relationship_whitelist()
    llm = get_llm()

    seed_nodes = state.connected_objects or []
    seed_ids = [n.get("id") for n in seed_nodes if n.get("id")]
    seen_ids = set(seed_ids)

    SmartLogger.log(
        "INFO",
        "Impact propagation started: iteratively expanding impacted nodes using 2-hop contexts and evidence-backed LLM suggestions.",
        category="agent.change_graph.propagation.start",
        params={
            "user_story_id": state.user_story_id,
            "scope": state.change_scope.value if state.change_scope else None,
            "change_description": state.change_description,
            "seed_count": len(seed_ids),
            "seed_ids": summarize_for_log(seed_ids),
            "limits": limits,
            "relationship_whitelist": rel_types,
        },
        max_inline_chars=1400,
    )

    # If there are no seeds, skip safely.
    if not seed_ids:
        SmartLogger.log(
            "INFO",
            "Impact propagation stopped early: no seed nodes were provided (nothing to expand).",
            category="agent.change_graph.propagation.stop",
            params={
                "user_story_id": state.user_story_id,
                "stop_reason": "no_seeds",
                "limits": limits,
            },
        )
        return {
            "phase": ChangePlanningPhase.SEARCH_RELATED if state.change_scope in [ChangeScope.CROSS_BC, ChangeScope.NEW_CAPABILITY] else ChangePlanningPhase.GENERATE_PLAN,
            "propagation_enabled": True,
            "propagation_confirmed": [],
            "propagation_review": [],
            "propagation_rounds": 0,
            "propagation_stop_reason": "no_seeds",
            "propagation_debug": {"limits": limits, "whitelist": rel_types, "seed_count": 0},
        }

    confirmed: List[PropagationCandidate] = []
    review: List[PropagationCandidate] = []
    review_by_id: Dict[str, PropagationCandidate] = {}
    confirmed_ids: set[str] = set()

    # Maintain a local cache of node metadata discovered during subgraph pulls
    node_meta_by_id: Dict[str, Dict[str, Any]] = {n.get("id"): n for n in seed_nodes if n.get("id")}

    driver = get_neo4j_driver()
    stop_reason = "max_rounds_reached"
    rounds_done = 0

    try:
        with neo4j_session(driver) as session:
            frontier: List[str] = list(seed_ids)

            for round_idx in range(1, max(1, limits["max_rounds"]) + 1):
                rounds_done = round_idx

                # Stop if budgets reached
                if len(confirmed) >= limits["max_confirmed_nodes"]:
                    stop_reason = "max_confirmed_reached"
                    break

                if not frontier:
                    stop_reason = "fixpoint_no_frontier"
                    break

                # Cap frontier for cost control
                frontier_original_size = len(frontier)
                frontier = frontier[: limits["max_frontier_per_round"]]

                SmartLogger.log(
                    "INFO",
                    "Impact propagation round started: building 2-hop contexts around frontier nodes.",
                    category="agent.change_graph.propagation.round.start",
                    params={
                        "user_story_id": state.user_story_id,
                        "round": round_idx,
                        "frontier_original_size": frontier_original_size,
                        "frontier_capped_size": len(frontier),
                        "frontier": frontier,
                        "confirmed_so_far": len(confirmed),
                        "review_so_far": len(review),
                        "seen_so_far": len(seen_ids),
                    },
                    max_inline_chars=1200,
                )

                # Build context for this round
                contexts = []
                union_node_ids = set()
                per_center_subgraph_sizes: Dict[str, Dict[str, int]] = {}

                for center_id in frontier:
                    subgraph = _fetch_2hop_subgraph(session, center_id, rel_types)
                    per_center_subgraph_sizes[center_id] = {
                        "nodes": len(subgraph.get("nodes") or []),
                        "relationships": len(subgraph.get("relationships") or []),
                    }
                    for n in subgraph.get("nodes") or []:
                        nid = n.get("id")
                        if nid:
                            union_node_ids.add(nid)
                            # cache best-effort metadata for later plan finalization
                            node_meta_by_id.setdefault(nid, {
                                "id": nid,
                                "name": n.get("name") or "",
                                "type": n.get("type") or "",
                                "bcId": n.get("bcId"),
                                "bcName": n.get("bcName"),
                                "description": n.get("description") or "",
                            })
                    contexts.append(_format_subgraph_for_prompt(center_id, subgraph))

                remaining_confirmed_budget = max(0, limits["max_confirmed_nodes"] - len(confirmed))
                round_budget = min(limits["max_new_per_round"], remaining_confirmed_budget)

                if round_budget <= 0:
                    stop_reason = "budget_exhausted"
                    break

                SmartLogger.log(
                    "INFO",
                    "Impact propagation round context prepared: union subgraph assembled; invoking LLM with stop rules and budget limits.",
                    category="agent.change_graph.propagation.round.context_ready",
                    params={
                        "user_story_id": state.user_story_id,
                        "round": round_idx,
                        "relationship_whitelist": rel_types,
                        "union_node_count": len(union_node_ids),
                        "per_center_subgraph_sizes": per_center_subgraph_sizes,
                        "remaining_confirmed_budget": remaining_confirmed_budget,
                        "round_budget": round_budget,
                    },
                    max_inline_chars=1800,
                )

                prompt = _propagation_prompt(
                    edited_user_story=state.edited_user_story,
                    change_description=state.change_description,
                    centers_context_text="\n\n".join(contexts),
                    max_new=round_budget,
                )

                SmartLogger.log(
                    "INFO",
                    "Propagation round: invoking LLM to identify additional impacted candidates.",
                    category="agent.change_graph.propagation.round",
                    params={
                        "round": round_idx,
                        "frontier": frontier,
                        "seen_ids": len(seen_ids),
                        "confirmed": len(confirmed),
                        "review": len(review),
                        "round_budget": round_budget,
                    },
                    max_inline_chars=1200,
                )

                provider = os.getenv("LLM_PROVIDER", "openai")
                model = os.getenv("LLM_MODEL", "gpt-4o")
                system_msg = "You are a DDD expert performing iterative impact propagation with evidence."

                if AI_AUDIT_LOG_ENABLED:
                    SmartLogger.log(
                        "INFO",
                        "Impact propagation: LLM invoke starting.",
                        category="agent.change_graph.propagation.llm.start",
                        params={
                            "user_story_id": state.user_story_id,
                            "round": round_idx,
                            "llm": {"provider": provider, "model": model},
                            "round_budget": round_budget,
                            "union_node_count": len(union_node_ids),
                            "prompt_len": len(prompt),
                            "prompt_sha256": sha256_text(prompt),
                            "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                            "system_len": len(system_msg),
                            "system_sha256": sha256_text(system_msg),
                        },
                        max_inline_chars=1800,
                    )

                t_llm0 = time.perf_counter()
                response = llm.invoke([SystemMessage(content=system_msg), HumanMessage(content=prompt)])
                llm_ms = int((time.perf_counter() - t_llm0) * 1000)

                resp_text = getattr(response, "content", "") or ""
                if AI_AUDIT_LOG_ENABLED:
                    SmartLogger.log(
                        "INFO",
                        "Impact propagation: LLM invoke completed.",
                        category="agent.change_graph.propagation.llm.done",
                        params={
                            "user_story_id": state.user_story_id,
                            "round": round_idx,
                            "llm": {"provider": provider, "model": model},
                            "llm_ms": llm_ms,
                            "response_len": len(resp_text),
                            "response_sha256": sha256_text(resp_text),
                            "response": resp_text if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_text),
                        },
                        max_inline_chars=1800,
                    )

                parsed: Dict[str, Any] = {}
                try:
                    parsed = json.loads(_extract_json_from_llm_text(getattr(response, "content", "") or ""))
                except Exception as e:
                    SmartLogger.log(
                        "WARNING",
                        "Propagation round: failed to parse LLM JSON, stopping propagation early.",
                        category="agent.change_graph.propagation.parse_error",
                        params={"round": round_idx, "error": str(e), "raw": (getattr(response, "content", "") or "")[:1500]},
                        max_inline_chars=1600,
                    )
                    stop_reason = "llm_parse_error"
                    break

                candidates = parsed.get("candidates") or []
                if not isinstance(candidates, list):
                    candidates = []

                new_confirmed_ids: List[str] = []
                added_this_round = 0
                stats = Counter()
                stats["llm_candidates_total"] = len(candidates)

                for c in candidates:
                    if not isinstance(c, dict):
                        stats["skip_non_dict"] += 1
                        continue

                    cid = (c.get("id") or "").strip()
                    if not cid:
                        stats["skip_missing_id"] += 1
                        continue

                    # Must exist in provided contexts (hard guardrail)
                    if cid not in union_node_ids:
                        stats["skip_not_in_context"] += 1
                        continue

                    # Skip already confirmed/seed. Review candidates may be re-proposed and upgraded.
                    if cid in confirmed_ids:
                        stats["skip_already_confirmed"] += 1
                        continue
                    if cid in seen_ids and cid not in review_by_id:
                        stats["skip_already_seen"] += 1
                        continue

                    ctype = (c.get("type") or node_meta_by_id.get(cid, {}).get("type") or "").strip()
                    cname = (c.get("name") or node_meta_by_id.get(cid, {}).get("name") or "").strip()
                    conf = _safe_float(c.get("confidence"), 0.0)
                    reason = (c.get("reason") or "").strip()
                    evidence_paths = c.get("evidence_paths") or []
                    if not isinstance(evidence_paths, list):
                        evidence_paths = []
                    evidence_paths = [str(p) for p in evidence_paths if str(p).strip()][:5]
                    suggested = (c.get("suggested_change_type") or "unknown").strip().lower()

                    meta = node_meta_by_id.get(cid) or {}
                    cand = PropagationCandidate(
                        id=cid,
                        type=ctype or meta.get("type") or "Unknown",
                        name=cname or meta.get("name") or "",
                        bcId=meta.get("bcId"),
                        bcName=meta.get("bcName"),
                        confidence=conf,
                        reason=reason,
                        evidence_paths=evidence_paths,
                        suggested_change_type=suggested if suggested else "unknown",
                        round=round_idx,
                    )

                    # Classify
                    if conf >= limits["confidence_confirmed"] and added_this_round < limits["max_new_per_round"]:
                        # Promote from review if it existed
                        if cid in review_by_id:
                            try:
                                review.remove(review_by_id[cid])
                            except ValueError:
                                pass
                            review_by_id.pop(cid, None)
                            stats["promoted_review_to_confirmed"] += 1
                        confirmed.append(cand)
                        confirmed_ids.add(cid)
                        new_confirmed_ids.append(cid)
                        seen_ids.add(cid)
                        added_this_round += 1
                        stats["added_confirmed"] += 1
                    elif conf >= limits["confidence_review"]:
                        # Update existing review candidate if confidence improves
                        prev = review_by_id.get(cid)
                        if prev is None:
                            review.append(cand)
                            review_by_id[cid] = cand
                            seen_ids.add(cid)
                            stats["added_review"] += 1
                        else:
                            if cand.confidence > prev.confidence:
                                # Replace the stored candidate in-place (best-effort)
                                try:
                                    idx = review.index(prev)
                                    review[idx] = cand
                                except ValueError:
                                    review.append(cand)
                                review_by_id[cid] = cand
                                stats["updated_review_higher_confidence"] += 1
                                # keep seen_ids as-is
                    else:
                        # Discard low confidence; do NOT mark seen so it can re-appear with more evidence later.
                        stats["discard_low_confidence"] += 1
                        continue

                SmartLogger.log(
                    "INFO",
                    "Impact propagation round classified candidates: accepted/ignored counts explain why the frontier will expand or converge.",
                    category="agent.change_graph.propagation.round.classified",
                    params={
                        "user_story_id": state.user_story_id,
                        "round": round_idx,
                        "thresholds": {
                            "confirmed": limits["confidence_confirmed"],
                            "review": limits["confidence_review"],
                        },
                        "stats": dict(stats),
                        "new_confirmed_ids": new_confirmed_ids,
                        "confirmed_total": len(confirmed),
                        "review_total": len(review),
                        "seen_total": len(seen_ids),
                    },
                    max_inline_chars=1800,
                )

                if not new_confirmed_ids:
                    stop_reason = "fixpoint_no_new_confirmed"
                    break

                # Next frontier is newly confirmed
                frontier = new_confirmed_ids

    finally:
        try:
            driver.close()
        except Exception:
            pass

    # Merge confirmed into connected_objects for downstream plan finalization
    expanded_connected = list(state.connected_objects or [])
    connected_before = len(expanded_connected)
    existing_ids = {n.get("id") for n in expanded_connected if n.get("id")}

    for cand in confirmed:
        if cand.id in existing_ids:
            continue
        meta = node_meta_by_id.get(cand.id) or {}
        expanded_connected.append({
            "id": cand.id,
            "type": cand.type or meta.get("type") or "Unknown",
            "name": cand.name or meta.get("name") or "",
            "bcId": cand.bcId or meta.get("bcId"),
            "bcName": cand.bcName or meta.get("bcName"),
            "description": meta.get("description") or "",
            "propagation": {
                "confidence": cand.confidence,
                "reason": cand.reason,
                "evidence_paths": cand.evidence_paths,
                "suggested_change_type": cand.suggested_change_type,
            },
        })
        existing_ids.add(cand.id)

    SmartLogger.log(
        "INFO",
        "Impact propagation completed: stop reason and counts summarize whether the algorithm converged safely.",
        category="agent.change_graph.propagation.done",
        params={
            "user_story_id": state.user_story_id,
            "rounds_done": rounds_done,
            "stop_reason": stop_reason,
            "seed_count": len(seed_ids),
            "confirmed_count": len(confirmed),
            "review_count": len(review),
            "connected_objects_before": connected_before,
            "connected_objects_after": len(expanded_connected),
        },
    )

    return {
        "phase": ChangePlanningPhase.SEARCH_RELATED if state.change_scope in [ChangeScope.CROSS_BC, ChangeScope.NEW_CAPABILITY] else ChangePlanningPhase.GENERATE_PLAN,
        "propagation_enabled": True,
        "propagation_confirmed": confirmed,
        "propagation_review": review,
        "propagation_rounds": rounds_done,
        "propagation_stop_reason": stop_reason,
        "propagation_debug": {
            "limits": limits,
            "whitelist": rel_types,
            "seed_count": len(seed_ids),
            "confirmed_count": len(confirmed),
            "review_count": len(review),
        },
        "connected_objects": expanded_connected,
    }


# =============================================================================
# Node Functions
# =============================================================================


def analyze_scope_node(state: ChangePlanningState) -> Dict[str, Any]:
    """
    Analyze whether the change can be resolved within existing connections
    or requires cross-BC connections.
    """
    llm = get_llm()

    SmartLogger.log(
        "INFO",
        "Scope analysis started: determining whether the change is LOCAL, CROSS_BC, or NEW_CAPABILITY.",
        category="agent.change_graph.scope.start",
        params={
            "user_story_id": state.user_story_id,
            "connected_objects_count": len(state.connected_objects or []),
            "original_user_story": summarize_for_log(state.original_user_story),
            "edited_user_story": summarize_for_log(state.edited_user_story),
        },
        max_inline_chars=1200,
    )
    
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

    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    system_msg = "You are a DDD expert analyzing change impact."

    if AI_AUDIT_LOG_ENABLED:
        SmartLogger.log(
            "INFO",
            "Scope analysis: LLM invoke starting.",
            category="agent.change_graph.scope.llm.start",
            params={
                "user_story_id": state.user_story_id,
                "llm": {"provider": provider, "model": model},
                "prompt_len": len(prompt),
                "prompt_sha256": sha256_text(prompt),
                "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                "system_len": len(system_msg),
                "system_sha256": sha256_text(system_msg),
            },
            max_inline_chars=1600,
        )

    t_llm0 = time.perf_counter()
    response = llm.invoke([SystemMessage(content=system_msg), HumanMessage(content=prompt)])
    llm_ms = int((time.perf_counter() - t_llm0) * 1000)

    resp_text = getattr(response, "content", "") or ""
    if AI_AUDIT_LOG_ENABLED:
        SmartLogger.log(
            "INFO",
            "Scope analysis: LLM invoke completed.",
            category="agent.change_graph.scope.llm.done",
            params={
                "user_story_id": state.user_story_id,
                "llm": {"provider": provider, "model": model},
                "llm_ms": llm_ms,
                "response_len": len(resp_text),
                "response_sha256": sha256_text(resp_text),
                "response": resp_text if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_text),
            },
            max_inline_chars=1600,
        )
    
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
        
        payload = {
            "phase": ChangePlanningPhase.SEARCH_RELATED if result["scope"] != "LOCAL" else ChangePlanningPhase.GENERATE_PLAN,
            "change_scope": scope_map.get(result["scope"], ChangeScope.LOCAL),
            "scope_reasoning": result.get("reasoning", ""),
            "keywords_to_search": result.get("keywords", []),
            "change_description": result.get("change_description", "")
        }

        SmartLogger.log(
            "INFO",
            "Scope analysis completed: scope determined based on user story delta and current connections.",
            category="agent.change_graph.scope.done",
            params={
                "user_story_id": state.user_story_id,
                "scope": payload["change_scope"].value if payload.get("change_scope") else None,
                "keywords_to_search": (payload.get("keywords_to_search") or [])[:20],
                "change_description": payload.get("change_description"),
                "reasoning_preview": (payload.get("scope_reasoning") or "")[:300],
            },
            max_inline_chars=1000,
        )
        return payload
    except Exception as e:
        SmartLogger.log(
            "WARNING",
            "Scope analysis fallback: failed to parse LLM response, defaulting scope to LOCAL to keep workflow moving.",
            category="agent.change_graph.scope.parse_error",
            params={
                "user_story_id": state.user_story_id,
                "error": str(e),
                "llm_raw_preview": (getattr(response, "content", "") or "")[:1200],
            },
            max_inline_chars=1400,
        )
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
        with neo4j_session(driver) as session:
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
        SmartLogger.log("ERROR", "Vector search error", category="agent.change_graph.search_related", params={"error": str(e)})
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

    SmartLogger.log(
        "INFO",
        "Plan finalization started: generating an APPLY-ready change plan grounded in connected objects + propagation candidates.",
        category="agent.change_graph.plan_finalizer.start",
        params={
            "user_story_id": state.user_story_id,
            "scope": state.change_scope.value if state.change_scope else None,
            "connected_objects_count": len(state.connected_objects or []),
            "propagation": {
                "enabled": state.propagation_enabled,
                "confirmed": len(state.propagation_confirmed or []),
                "review": len(state.propagation_review or []),
                "rounds": state.propagation_rounds,
                "stop_reason": state.propagation_stop_reason,
            },
            "related_objects_count": len(state.related_objects or []),
        },
        max_inline_chars=1200,
    )
    
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
    
    # Propagation context (confirmed + review). Use confirmed by default for plan finalization.
    confirmed = state.propagation_confirmed or []
    review = state.propagation_review or []

    confirmed_text = "\n".join([
        f"- {c.type} [{c.id}]: {c.name} (BC: {c.bcName or 'Unknown'}, confidence: {c.confidence:.2f})\n"
        f"  reason: {c.reason}\n"
        f"  evidence: {', '.join(c.evidence_paths[:2]) if c.evidence_paths else 'n/a'}"
        for c in confirmed
    ]) if confirmed else "None"

    review_text = "\n".join([
        f"- {c.type} [{c.id}]: {c.name} (BC: {c.bcName or 'Unknown'}, confidence: {c.confidence:.2f})\n"
        f"  reason: {c.reason}\n"
        f"  evidence: {', '.join(c.evidence_paths[:2]) if c.evidence_paths else 'n/a'}"
        for c in review[:20]
    ]) if review else "None"

    prompt = f"""Generate an APPLY-READY change plan (finalization) for this User Story modification.

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

## Propagation (Confirmed impacted candidates)
{confirmed_text}

## Propagation (Review candidates - lower confidence, include only if necessary)
{review_text}

## Related Objects Found (from other BCs)
{related_text}

## Your Task
Finalize a detailed change plan using the objects above as your grounding context. IMPORTANT:

1. Prefer using the Propagation Confirmed candidates as the authoritative set of impacted nodes.
2. You MAY include some Review candidates only when the justification is strong and required for consistency.
3. Do NOT invent random node ids. If you propose "create", you must use a NEW id (not existing ones).
4. Keep actions within what /api/change/apply supports:
   - action: rename, update, create, connect, delete
   - create targetType: Policy, Command, Event (Aggregate create is NOT supported by apply)
   - connect connectionType: TRIGGERS, INVOKES, IMPLEMENTS
5. Cross-BC connections must use the Event-Policy-Command pattern:
   - Event (source BC) TRIGGERS Policy (target or intermediary)
   - Policy INVOKES Command (target BC)

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

    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    system_msg = """You are a DDD expert creating change plans.
When connecting BCs, always use the Event-Policy-Command pattern:
- Event (from source BC) TRIGGERS Policy
- Policy INVOKES Command (in target BC)"""

    if AI_AUDIT_LOG_ENABLED:
        SmartLogger.log(
            "INFO",
            "Plan finalization: LLM invoke starting.",
            category="agent.change_graph.plan_finalizer.llm.start",
            params={
                "user_story_id": state.user_story_id,
                "scope": state.change_scope.value if state.change_scope else None,
                "llm": {"provider": provider, "model": model},
                "prompt_len": len(prompt),
                "prompt_sha256": sha256_text(prompt),
                "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                "system_len": len(system_msg),
                "system_sha256": sha256_text(system_msg),
            },
            max_inline_chars=1800,
        )

    t_llm0 = time.perf_counter()
    response = llm.invoke([SystemMessage(content=system_msg), HumanMessage(content=prompt)])
    llm_ms = int((time.perf_counter() - t_llm0) * 1000)

    resp_text = getattr(response, "content", "") or ""
    if AI_AUDIT_LOG_ENABLED:
        SmartLogger.log(
            "INFO",
            "Plan finalization: LLM invoke completed.",
            category="agent.change_graph.plan_finalizer.llm.done",
            params={
                "user_story_id": state.user_story_id,
                "scope": state.change_scope.value if state.change_scope else None,
                "llm": {"provider": provider, "model": model},
                "llm_ms": llm_ms,
                "response_len": len(resp_text),
                "response_sha256": sha256_text(resp_text),
                "response": resp_text if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_text),
            },
            max_inline_chars=1800,
        )
    
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
        
        # High-signal summary for log-driven verification
        action_counts = Counter([c.action for c in proposed_changes])
        connect_types = Counter([c.connectionType for c in proposed_changes if c.action == "connect" and c.connectionType])
        create_types = Counter([c.targetType for c in proposed_changes if c.action == "create" and c.targetType])

        SmartLogger.log(
            "INFO",
            "Plan finalization completed: proposed changes are ready for human approval and /apply execution.",
            category="agent.change_graph.plan_finalizer.done",
            params={
                "user_story_id": state.user_story_id,
                "scope": state.change_scope.value if state.change_scope else None,
                "summary_preview": (result.get("summary") or "")[:400],
                "changes_count": len(proposed_changes),
                "action_counts": dict(action_counts),
                "connect_types": dict(connect_types),
                "create_types": dict(create_types),
            },
            max_inline_chars=1200,
        )

        return {
            "phase": ChangePlanningPhase.AWAIT_APPROVAL,
            "proposed_changes": proposed_changes,
            "plan_summary": result.get("summary", ""),
            "awaiting_approval": True
        }
        
    except Exception as e:
        SmartLogger.log(
            "ERROR",
            "Plan finalization failed: LLM response could not be parsed into the expected JSON shape.",
            category="agent.change_graph.plan_finalizer.parse_error",
            params={
                "user_story_id": state.user_story_id,
                "error": str(e),
                "llm_raw_preview": (getattr(response, "content", "") or "")[:1500],
            },
            max_inline_chars=1600,
        )
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

    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    system_msg = "You are revising a change plan based on user feedback."

    if AI_AUDIT_LOG_ENABLED:
        SmartLogger.log(
            "INFO",
            "Plan revision: LLM invoke starting.",
            category="agent.change_graph.plan_revision.llm.start",
            params={
                "user_story_id": state.user_story_id,
                "revision_count": state.revision_count,
                "llm": {"provider": provider, "model": model},
                "prompt_len": len(prompt),
                "prompt_sha256": sha256_text(prompt),
                "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                "system_len": len(system_msg),
                "system_sha256": sha256_text(system_msg),
            },
            max_inline_chars=1600,
        )

    t_llm0 = time.perf_counter()
    response = llm.invoke([SystemMessage(content=system_msg), HumanMessage(content=prompt)])
    llm_ms = int((time.perf_counter() - t_llm0) * 1000)

    resp_text = getattr(response, "content", "") or ""
    if AI_AUDIT_LOG_ENABLED:
        SmartLogger.log(
            "INFO",
            "Plan revision: LLM invoke completed.",
            category="agent.change_graph.plan_revision.llm.done",
            params={
                "user_story_id": state.user_story_id,
                "revision_count": state.revision_count,
                "llm": {"provider": provider, "model": model},
                "llm_ms": llm_ms,
                "response_len": len(resp_text),
                "response_sha256": sha256_text(resp_text),
                "response": resp_text if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_text),
            },
            max_inline_chars=1600,
        )
    
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
        with neo4j_session(driver) as session:
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
        SmartLogger.log(
            "INFO",
            "Routing decision after propagation: scope requires cross-BC discovery, so we will search related objects before finalizing the plan.",
            category="agent.change_graph.route.after_propagation",
            params={
                "user_story_id": state.user_story_id,
                "scope": state.change_scope.value if state.change_scope else None,
                "next": "search_related",
            },
        )
        return "search_related"

    SmartLogger.log(
        "INFO",
        "Routing decision after propagation: scope is LOCAL, so we will finalize the plan without cross-BC search.",
        category="agent.change_graph.route.after_propagation",
        params={
            "user_story_id": state.user_story_id,
            "scope": state.change_scope.value if state.change_scope else None,
            "next": "generate_plan",
        },
    )
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
    graph.add_node("propagate_impacts", propagate_impacts_node)
    graph.add_node("search_related", search_related_objects_node)
    graph.add_node("generate_plan", generate_plan_node)
    graph.add_node("revise_plan", revise_plan_node)
    graph.add_node("apply_changes", apply_changes_node)
    
    # Set entry point
    graph.set_entry_point("analyze_scope")
    
    # Add edges
    graph.add_edge("analyze_scope", "propagate_impacts")
    graph.add_conditional_edges(
        "propagate_impacts",
        route_after_scope_analysis,
        {
            "search_related": "search_related",
            "generate_plan": "generate_plan",
        },
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
            "summary": result.get("plan_summary", ""),
            "propagation": {
                "enabled": state.propagation_enabled,
                "rounds": state.propagation_rounds,
                "stopReason": state.propagation_stop_reason,
                "confirmed": [c.model_dump() for c in (state.propagation_confirmed or [])],
                "review": [c.model_dump() for c in (state.propagation_review or [])],
            },
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
        "summary": final_state.plan_summary,
        "propagation": {
            "enabled": final_state.propagation_enabled,
            "rounds": final_state.propagation_rounds,
            "stopReason": final_state.propagation_stop_reason,
            "confirmed": [c.model_dump() for c in (final_state.propagation_confirmed or [])],
            "review": [c.model_dump() for c in (final_state.propagation_review or [])],
            "debug": final_state.propagation_debug,
        },
    }

