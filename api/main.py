"""
FastAPI Backend for Event Storming Navigator

Provides REST APIs for:
- BC (Bounded Context) listing and tree structure
- Subgraph queries for canvas rendering
- Document ingestion with real-time progress streaming
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from neo4j import GraphDatabase

load_dotenv()

# Neo4j Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "12345msaez")

driver = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage Neo4j connection lifecycle."""
    global driver
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    yield
    if driver:
        driver.close()


app = FastAPI(
    title="Event Storming Navigator API",
    description="API for Ontology-based Event Storming Canvas",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for Vue.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include ingestion router
from api.ingestion import router as ingestion_router
app.include_router(ingestion_router)

# Include change management router
from api.change import router as change_router
app.include_router(change_router)


def get_session():
    """Get a Neo4j session."""
    return driver.session()


# =============================================================================
# API Endpoints
# =============================================================================


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    try:
        with get_session() as session:
            session.run("RETURN 1")
        return {"status": "healthy", "neo4j": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.delete("/api/graph/clear")
async def clear_all_nodes():
    """
    DELETE /graph/clear - 모든 노드와 관계 삭제
    새로운 인제스션 전에 기존 데이터를 모두 삭제합니다.
    """
    query = """
    MATCH (n)
    DETACH DELETE n
    """
    with get_session() as session:
        result = session.run(query)
        summary = result.consume()
        return {
            "status": "cleared",
            "nodes_deleted": summary.counters.nodes_deleted,
            "relationships_deleted": summary.counters.relationships_deleted
        }


@app.get("/api/graph/stats")
async def get_graph_stats():
    """
    GET /graph/stats - 그래프 통계 조회
    현재 Neo4j에 저장된 노드 수를 반환합니다.
    """
    query = """
    MATCH (n)
    WITH labels(n)[0] as label, count(n) as count
    RETURN collect({label: label, count: count}) as stats
    """
    with get_session() as session:
        result = session.run(query)
        record = result.single()
        if record:
            stats = {item["label"]: item["count"] for item in record["stats"] if item["label"]}
            total = sum(stats.values())
            return {"total": total, "by_type": stats}
        return {"total": 0, "by_type": {}}


@app.delete("/api/graph/clear")
async def clear_all_nodes():
    """
    DELETE /graph/clear - 모든 노드 삭제
    Clears all nodes and relationships from the graph.
    Used before starting a new ingestion.
    """
    query = """
    MATCH (n)
    DETACH DELETE n
    """
    count_query = """
    MATCH (n)
    RETURN count(n) as count
    """
    
    with get_session() as session:
        # Get count before deletion
        count_result = session.run(count_query)
        count_before = count_result.single()["count"]
        
        # Delete all nodes
        session.run(query)
        
        return {
            "success": True,
            "deleted_nodes": count_before,
            "message": f"Deleted {count_before} nodes and all relationships"
        }


@app.get("/api/graph/stats")
async def get_graph_stats():
    """
    GET /graph/stats - 그래프 통계 조회
    Returns count of each node type.
    """
    query = """
    MATCH (n)
    WITH labels(n)[0] as label, count(n) as count
    RETURN collect({label: label, count: count}) as stats
    """
    
    with get_session() as session:
        result = session.run(query)
        record = result.single()
        stats = record["stats"] if record else []
        
        # Convert to dict
        stats_dict = {item["label"]: item["count"] for item in stats if item["label"]}
        total = sum(stats_dict.values())
        
        return {
            "total": total,
            "by_type": stats_dict
        }


@app.get("/api/user-stories")
async def get_all_user_stories() -> list[dict[str, Any]]:
    """
    GET /user-stories - User Story 목록 조회
    Returns all User Stories with their BC assignments.
    """
    query = """
    MATCH (us:UserStory)
    OPTIONAL MATCH (us)-[:IMPLEMENTS]->(bc:BoundedContext)
    RETURN {
        id: us.id,
        role: us.role,
        action: us.action,
        benefit: us.benefit,
        priority: us.priority,
        status: us.status,
        bcId: bc.id,
        bcName: bc.name
    } as user_story
    ORDER BY us.id
    """
    with get_session() as session:
        result = session.run(query)
        return [dict(record["user_story"]) for record in result]


@app.get("/api/user-stories/unassigned")
async def get_unassigned_user_stories() -> list[dict[str, Any]]:
    """
    GET /user-stories/unassigned - BC에 할당되지 않은 User Story 조회
    """
    query = """
    MATCH (us:UserStory)
    WHERE NOT (us)-[:IMPLEMENTS]->(:BoundedContext)
    RETURN {
        id: us.id,
        role: us.role,
        action: us.action,
        benefit: us.benefit,
        priority: us.priority,
        status: us.status
    } as user_story
    ORDER BY us.id
    """
    with get_session() as session:
        result = session.run(query)
        return [dict(record["user_story"]) for record in result]


@app.get("/api/contexts")
async def get_all_contexts() -> list[dict[str, Any]]:
    """
    GET /contexts - BC 목록 조회
    Returns all Bounded Contexts with basic info.
    """
    query = """
    MATCH (bc:BoundedContext)
    OPTIONAL MATCH (bc)-[:HAS_AGGREGATE]->(agg:Aggregate)
    OPTIONAL MATCH (us:UserStory)-[:IMPLEMENTS]->(bc)
    WITH bc, count(DISTINCT agg) as aggregateCount, count(DISTINCT us) as userStoryCount
    RETURN {
        id: bc.id,
        name: bc.name,
        description: bc.description,
        owner: bc.owner,
        aggregateCount: aggregateCount,
        userStoryCount: userStoryCount
    } as context
    ORDER BY bc.name
    """
    with get_session() as session:
        result = session.run(query)
        return [dict(record["context"]) for record in result]


@app.get("/api/contexts/{context_id}/tree")
async def get_context_tree(context_id: str) -> dict[str, Any]:
    """
    GET /contexts/{id}/tree - BC 하위 트리
    Returns the full tree structure under a Bounded Context.
    """
    query = """
    MATCH (bc:BoundedContext {id: $context_id})
    OPTIONAL MATCH (bc)-[:HAS_AGGREGATE]->(agg:Aggregate)
    OPTIONAL MATCH (agg)-[:HAS_COMMAND]->(cmd:Command)
    OPTIONAL MATCH (cmd)-[:EMITS]->(evt:Event)
    OPTIONAL MATCH (bc)-[:HAS_POLICY]->(pol:Policy)
    WITH bc, agg, cmd, evt, pol
    WITH bc,
         agg,
         collect(DISTINCT {
             id: cmd.id,
             name: cmd.name,
             type: 'Command',
             actor: cmd.actor
         }) as commands,
         collect(DISTINCT {
             id: evt.id,
             name: evt.name,
             type: 'Event',
             version: evt.version
         }) as events
    WITH bc, 
         collect(DISTINCT {
             id: agg.id,
             name: agg.name,
             type: 'Aggregate',
             rootEntity: agg.rootEntity,
             commands: commands,
             events: events
         }) as aggregates
    MATCH (bc)-[:HAS_POLICY]->(pol:Policy)
    OPTIONAL MATCH (evt:Event)-[:TRIGGERS]->(pol)
    OPTIONAL MATCH (pol)-[:INVOKES]->(cmd:Command)
    WITH bc, aggregates,
         collect(DISTINCT {
             id: pol.id,
             name: pol.name,
             type: 'Policy',
             description: pol.description,
             triggerEventId: evt.id,
             invokeCommandId: cmd.id
         }) as policies
    RETURN {
        id: bc.id,
        name: bc.name,
        type: 'BoundedContext',
        description: bc.description,
        aggregates: aggregates,
        policies: policies
    } as tree
    """
    with get_session() as session:
        result = session.run(query, context_id=context_id)
        record = result.single()
        if not record:
            raise HTTPException(status_code=404, detail=f"Context {context_id} not found")
        return dict(record["tree"])


@app.get("/api/contexts/{context_id}/full-tree")
async def get_context_full_tree(context_id: str) -> dict[str, Any]:
    """
    GET /contexts/{id}/full-tree - BC 하위 전체 트리 (정규화된 구조)
    """
    # Get BC info
    bc_query = """
    MATCH (bc:BoundedContext {id: $context_id})
    RETURN bc {.id, .name, .description, .owner} as bc
    """
    
    # Get User Stories for this BC
    us_query = """
    MATCH (us:UserStory)-[:IMPLEMENTS]->(bc:BoundedContext {id: $context_id})
    RETURN us {.id, .role, .action, .benefit, .priority, .status} as userStory
    ORDER BY us.id
    """
    
    # Get Aggregates
    agg_query = """
    MATCH (bc:BoundedContext {id: $context_id})-[:HAS_AGGREGATE]->(agg:Aggregate)
    RETURN agg {.id, .name, .rootEntity, .invariants} as aggregate
    ORDER BY agg.name
    """
    
    # Get Commands per Aggregate
    cmd_query = """
    MATCH (bc:BoundedContext {id: $context_id})-[:HAS_AGGREGATE]->(agg:Aggregate)-[:HAS_COMMAND]->(cmd:Command)
    RETURN agg.id as aggregateId, cmd {.id, .name, .actor, .inputSchema} as command
    ORDER BY cmd.name
    """
    
    # Get Events
    evt_query = """
    MATCH (bc:BoundedContext {id: $context_id})-[:HAS_AGGREGATE]->(agg:Aggregate)-[:HAS_COMMAND]->(cmd:Command)-[:EMITS]->(evt:Event)
    RETURN agg.id as aggregateId, cmd.id as commandId, evt {.id, .name, .version} as event
    ORDER BY evt.name
    """
    
    # Get Policies
    pol_query = """
    MATCH (bc:BoundedContext {id: $context_id})-[:HAS_POLICY]->(pol:Policy)
    OPTIONAL MATCH (evt:Event)-[:TRIGGERS]->(pol)
    OPTIONAL MATCH (pol)-[:INVOKES]->(cmd:Command)
    RETURN pol {.id, .name, .description} as policy, 
           evt.id as triggerEventId, 
           cmd.id as invokeCommandId
    ORDER BY pol.name
    """
    
    with get_session() as session:
        # BC
        bc_result = session.run(bc_query, context_id=context_id)
        bc_record = bc_result.single()
        if not bc_record:
            raise HTTPException(status_code=404, detail=f"Context {context_id} not found")
        bc = dict(bc_record["bc"])
        bc["type"] = "BoundedContext"
        
        # User Stories
        us_result = session.run(us_query, context_id=context_id)
        user_stories = []
        for record in us_result:
            us = dict(record["userStory"])
            us["type"] = "UserStory"
            us["name"] = f"{us.get('role', 'user')}: {us.get('action', '')[:30]}..."
            user_stories.append(us)
        
        # Aggregates
        agg_result = session.run(agg_query, context_id=context_id)
        aggregates = {}
        for record in agg_result:
            agg = dict(record["aggregate"])
            agg["type"] = "Aggregate"
            agg["commands"] = []
            agg["events"] = []
            aggregates[agg["id"]] = agg
        
        # Commands
        cmd_result = session.run(cmd_query, context_id=context_id)
        commands_map = {}
        for record in cmd_result:
            agg_id = record["aggregateId"]
            cmd = dict(record["command"])
            cmd["type"] = "Command"
            cmd["events"] = []
            if agg_id in aggregates:
                aggregates[agg_id]["commands"].append(cmd)
                commands_map[cmd["id"]] = cmd
        
        # Events
        evt_result = session.run(evt_query, context_id=context_id)
        for record in evt_result:
            agg_id = record["aggregateId"]
            cmd_id = record["commandId"]
            evt = dict(record["event"])
            evt["type"] = "Event"
            if cmd_id in commands_map:
                commands_map[cmd_id]["events"].append(evt)
            if agg_id in aggregates:
                aggregates[agg_id]["events"].append(evt)
        
        # Policies
        pol_result = session.run(pol_query, context_id=context_id)
        policies = []
        for record in pol_result:
            pol = dict(record["policy"])
            pol["type"] = "Policy"
            pol["triggerEventId"] = record["triggerEventId"]
            pol["invokeCommandId"] = record["invokeCommandId"]
            policies.append(pol)
        
        bc["userStories"] = user_stories
        bc["aggregates"] = list(aggregates.values())
        bc["policies"] = policies
        
        return bc


@app.get("/api/graph/subgraph")
async def get_subgraph(
    node_ids: list[str] = Query(..., description="List of node IDs to include"),
) -> dict[str, Any]:
    """
    GET /graph/subgraph - 선택 노드 기준 서브그래프
    Returns nodes and relations for the selected node IDs.
    
    Input: Node IDs
    Output: Nodes (Type, Name, Meta) + Relations (Type, Direction)
    """
    # Query to get nodes and their relationships
    query = """
    // Get all requested nodes
    UNWIND $node_ids as nodeId
    MATCH (n)
    WHERE n.id = nodeId
    WITH collect(n) as nodes
    
    // Get relationships between these nodes
    UNWIND nodes as n1
    UNWIND nodes as n2
    OPTIONAL MATCH (n1)-[r]->(n2)
    WHERE n1 <> n2 AND r IS NOT NULL
    
    WITH nodes, collect(DISTINCT {
        source: n1.id,
        target: n2.id,
        type: type(r),
        properties: properties(r)
    }) as relationships
    
    UNWIND nodes as n
    WITH collect(DISTINCT {
        id: n.id,
        name: n.name,
        type: labels(n)[0],
        properties: properties(n)
    }) as nodes, relationships
    
    RETURN nodes, [r IN relationships WHERE r.source IS NOT NULL] as relationships
    """
    
    with get_session() as session:
        result = session.run(query, node_ids=node_ids)
        record = result.single()
        
        if not record:
            return {"nodes": [], "relationships": []}
        
        nodes = record["nodes"]
        relationships = record["relationships"]
        
        return {
            "nodes": nodes,
            "relationships": relationships
        }


@app.get("/api/graph/expand/{node_id}")
async def expand_node(node_id: str) -> dict[str, Any]:
    """
    Expand a node to get its connected nodes based on type.
    - BoundedContext → All Aggregates + Policies
    - Aggregate → All Commands + Events
    - Command → Events it emits
    - Event → Policies it triggers
    - Policy → Commands it invokes
    """
    
    # First, determine the node type
    type_query = """
    MATCH (n {id: $node_id})
    RETURN labels(n)[0] as nodeType, n as node
    """
    
    with get_session() as session:
        type_result = session.run(type_query, node_id=node_id)
        type_record = type_result.single()
        
        if not type_record:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        
        node_type = type_record["nodeType"]
        main_node = dict(type_record["node"])
        main_node["type"] = node_type
        
        nodes = [main_node]
        relationships = []
        
        if node_type == "BoundedContext":
            # Get Aggregates
            agg_query = """
            MATCH (bc:BoundedContext {id: $node_id})-[r:HAS_AGGREGATE]->(agg:Aggregate)
            OPTIONAL MATCH (agg)-[r2:HAS_COMMAND]->(cmd:Command)
            OPTIONAL MATCH (cmd)-[r3:EMITS]->(evt:Event)
            RETURN agg, cmd, evt,
                   {source: bc.id, target: agg.id, type: 'HAS_AGGREGATE'} as rel1,
                   {source: agg.id, target: cmd.id, type: 'HAS_COMMAND'} as rel2,
                   {source: cmd.id, target: evt.id, type: 'EMITS'} as rel3
            """
            agg_result = session.run(agg_query, node_id=node_id)
            seen_ids = {node_id}
            
            for record in agg_result:
                if record["agg"] and record["agg"]["id"] not in seen_ids:
                    agg = dict(record["agg"])
                    agg["type"] = "Aggregate"
                    nodes.append(agg)
                    seen_ids.add(agg["id"])
                    if record["rel1"]["target"]:
                        relationships.append(dict(record["rel1"]))
                
                if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    nodes.append(cmd)
                    seen_ids.add(cmd["id"])
                    if record["rel2"]["target"]:
                        relationships.append(dict(record["rel2"]))
                
                if record["evt"] and record["evt"]["id"] not in seen_ids:
                    evt = dict(record["evt"])
                    evt["type"] = "Event"
                    nodes.append(evt)
                    seen_ids.add(evt["id"])
                    if record["rel3"]["target"]:
                        relationships.append(dict(record["rel3"]))
            
            # Get Policies
            pol_query = """
            MATCH (bc:BoundedContext {id: $node_id})-[:HAS_POLICY]->(pol:Policy)
            OPTIONAL MATCH (evt:Event)-[r:TRIGGERS]->(pol)
            OPTIONAL MATCH (pol)-[r2:INVOKES]->(cmd:Command)
            RETURN pol, evt.id as triggerEventId, cmd.id as invokeCommandId
            """
            pol_result = session.run(pol_query, node_id=node_id)
            for record in pol_result:
                if record["pol"] and record["pol"]["id"] not in seen_ids:
                    pol = dict(record["pol"])
                    pol["type"] = "Policy"
                    nodes.append(pol)
                    seen_ids.add(pol["id"])
                    
                    if record["triggerEventId"]:
                        relationships.append({
                            "source": record["triggerEventId"],
                            "target": pol["id"],
                            "type": "TRIGGERS"
                        })
                    if record["invokeCommandId"]:
                        relationships.append({
                            "source": pol["id"],
                            "target": record["invokeCommandId"],
                            "type": "INVOKES"
                        })
        
        elif node_type == "Aggregate":
            # Get Commands and Events
            expand_query = """
            MATCH (agg:Aggregate {id: $node_id})-[:HAS_COMMAND]->(cmd:Command)
            OPTIONAL MATCH (cmd)-[:EMITS]->(evt:Event)
            RETURN cmd, evt
            """
            expand_result = session.run(expand_query, node_id=node_id)
            seen_ids = {node_id}
            
            for record in expand_result:
                if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    nodes.append(cmd)
                    seen_ids.add(cmd["id"])
                    relationships.append({
                        "source": node_id,
                        "target": cmd["id"],
                        "type": "HAS_COMMAND"
                    })
                
                if record["evt"] and record["evt"]["id"] not in seen_ids:
                    evt = dict(record["evt"])
                    evt["type"] = "Event"
                    nodes.append(evt)
                    seen_ids.add(evt["id"])
                    relationships.append({
                        "source": record["cmd"]["id"],
                        "target": evt["id"],
                        "type": "EMITS"
                    })
        
        elif node_type == "Command":
            # Get Events
            expand_query = """
            MATCH (cmd:Command {id: $node_id})-[:EMITS]->(evt:Event)
            RETURN evt
            """
            expand_result = session.run(expand_query, node_id=node_id)
            
            for record in expand_result:
                if record["evt"]:
                    evt = dict(record["evt"])
                    evt["type"] = "Event"
                    nodes.append(evt)
                    relationships.append({
                        "source": node_id,
                        "target": evt["id"],
                        "type": "EMITS"
                    })
        
        elif node_type == "Event":
            # Get Policies
            expand_query = """
            MATCH (evt:Event {id: $node_id})-[:TRIGGERS]->(pol:Policy)
            OPTIONAL MATCH (pol)-[:INVOKES]->(cmd:Command)
            RETURN pol, cmd
            """
            expand_result = session.run(expand_query, node_id=node_id)
            seen_ids = {node_id}
            
            for record in expand_result:
                if record["pol"] and record["pol"]["id"] not in seen_ids:
                    pol = dict(record["pol"])
                    pol["type"] = "Policy"
                    nodes.append(pol)
                    seen_ids.add(pol["id"])
                    relationships.append({
                        "source": node_id,
                        "target": pol["id"],
                        "type": "TRIGGERS"
                    })
                
                if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    nodes.append(cmd)
                    seen_ids.add(cmd["id"])
                    relationships.append({
                        "source": record["pol"]["id"],
                        "target": cmd["id"],
                        "type": "INVOKES"
                    })
        
        elif node_type == "Policy":
            # Get Commands it invokes
            expand_query = """
            MATCH (pol:Policy {id: $node_id})-[:INVOKES]->(cmd:Command)
            RETURN cmd
            """
            expand_result = session.run(expand_query, node_id=node_id)
            
            for record in expand_result:
                if record["cmd"]:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    nodes.append(cmd)
                    relationships.append({
                        "source": node_id,
                        "target": cmd["id"],
                        "type": "INVOKES"
                    })
        
        # Deduplicate relationships
        unique_rels = []
        seen_rels = set()
        for rel in relationships:
            if rel.get("source") and rel.get("target"):
                key = (rel["source"], rel["target"], rel["type"])
                if key not in seen_rels:
                    seen_rels.add(key)
                    unique_rels.append(rel)
        
        return {
            "nodes": nodes,
            "relationships": unique_rels
        }


@app.get("/api/graph/find-relations")
async def find_relations(
    node_ids: list[str] = Query(..., description="List of node IDs on canvas"),
) -> list[dict[str, Any]]:
    """
    Find ALL relations between nodes that are currently on the canvas.
    This includes:
    - Direct relations (HAS_COMMAND, EMITS, etc.)
    - Cross-BC relations (Event TRIGGERS Policy, Policy INVOKES Command)
    """
    # Query for direct relationships between canvas nodes
    direct_query = """
    UNWIND $node_ids as sourceId
    UNWIND $node_ids as targetId
    MATCH (source {id: sourceId})-[r]->(target {id: targetId})
    WHERE sourceId <> targetId
    RETURN DISTINCT {
        source: source.id,
        target: target.id,
        type: type(r)
    } as relationship
    """
    
    # Query specifically for Event → TRIGGERS → Policy (cross-BC)
    cross_bc_query = """
    UNWIND $node_ids as evtId
    UNWIND $node_ids as polId
    MATCH (evt:Event {id: evtId})-[r:TRIGGERS]->(pol:Policy {id: polId})
    RETURN DISTINCT {
        source: evt.id,
        target: pol.id,
        type: 'TRIGGERS'
    } as relationship
    
    UNION
    
    // Policy → INVOKES → Command (cross-BC)
    UNWIND $node_ids as polId
    UNWIND $node_ids as cmdId
    MATCH (pol:Policy {id: polId})-[r:INVOKES]->(cmd:Command {id: cmdId})
    RETURN DISTINCT {
        source: pol.id,
        target: cmd.id,
        type: 'INVOKES'
    } as relationship
    """
    
    relationships = []
    seen = set()
    
    with get_session() as session:
        # Get direct relationships
        result = session.run(direct_query, node_ids=node_ids)
        for record in result:
            rel = dict(record["relationship"])
            key = (rel["source"], rel["target"], rel["type"])
            if key not in seen:
                seen.add(key)
                relationships.append(rel)
        
        # Get cross-BC relationships
        result = session.run(cross_bc_query, node_ids=node_ids)
        for record in result:
            rel = dict(record["relationship"])
            key = (rel["source"], rel["target"], rel["type"])
            if key not in seen:
                seen.add(key)
                relationships.append(rel)
    
    return relationships


@app.get("/api/graph/find-cross-bc-relations")
async def find_cross_bc_relations(
    new_node_ids: list[str] = Query(..., description="Newly added node IDs"),
    existing_node_ids: list[str] = Query(..., description="Existing node IDs on canvas"),
) -> list[dict[str, Any]]:
    """
    Find cross-BC relationships between newly added nodes and existing canvas nodes.
    
    This is optimized for the use case where user drops a new BC onto canvas
    and we need to find connections like:
    - Event (existing) → TRIGGERS → Policy (new)
    - Event (new) → TRIGGERS → Policy (existing)
    - Policy (existing) → INVOKES → Command (new)
    - Policy (new) → INVOKES → Command (existing)
    """
    query = """
    // Event → TRIGGERS → Policy (existing event triggers new policy)
    UNWIND $existing_ids as evtId
    UNWIND $new_ids as polId
    OPTIONAL MATCH (evt:Event {id: evtId})-[:TRIGGERS]->(pol:Policy {id: polId})
    WITH collect({source: evt.id, target: pol.id, type: 'TRIGGERS'}) as r1
    
    // Event → TRIGGERS → Policy (new event triggers existing policy)
    UNWIND $new_ids as evtId
    UNWIND $existing_ids as polId
    OPTIONAL MATCH (evt:Event {id: evtId})-[:TRIGGERS]->(pol:Policy {id: polId})
    WITH r1, collect({source: evt.id, target: pol.id, type: 'TRIGGERS'}) as r2
    
    // Policy → INVOKES → Command (existing policy invokes new command)
    UNWIND $existing_ids as polId
    UNWIND $new_ids as cmdId
    OPTIONAL MATCH (pol:Policy {id: polId})-[:INVOKES]->(cmd:Command {id: cmdId})
    WITH r1, r2, collect({source: pol.id, target: cmd.id, type: 'INVOKES'}) as r3
    
    // Policy → INVOKES → Command (new policy invokes existing command)
    UNWIND $new_ids as polId
    UNWIND $existing_ids as cmdId
    OPTIONAL MATCH (pol:Policy {id: polId})-[:INVOKES]->(cmd:Command {id: cmdId})
    WITH r1, r2, r3, collect({source: pol.id, target: cmd.id, type: 'INVOKES'}) as r4
    
    RETURN r1 + r2 + r3 + r4 as relationships
    """
    
    with get_session() as session:
        result = session.run(query, new_ids=new_node_ids, existing_ids=existing_node_ids)
        record = result.single()
        
        if not record:
            return []
        
        # Filter out null relationships and deduplicate
        relationships = []
        seen = set()
        
        for rel in record["relationships"]:
            if rel.get("source") and rel.get("target"):
                key = (rel["source"], rel["target"], rel["type"])
                if key not in seen:
                    seen.add(key)
                    relationships.append(rel)
        
        return relationships


@app.get("/api/graph/node-context/{node_id}")
async def get_node_context(node_id: str) -> dict[str, Any]:
    """
    Get the BoundedContext that contains a given node.
    Returns BC info so nodes can be properly grouped.
    """
    query = """
    MATCH (n {id: $node_id})
    OPTIONAL MATCH (bc:BoundedContext)-[:HAS_AGGREGATE|HAS_POLICY*1..2]->(n)
    OPTIONAL MATCH (bc2:BoundedContext)-[:HAS_AGGREGATE]->(agg:Aggregate)-[:HAS_COMMAND]->(n)
    OPTIONAL MATCH (bc3:BoundedContext)-[:HAS_AGGREGATE]->(agg2:Aggregate)-[:HAS_COMMAND]->(cmd:Command)-[:EMITS]->(n)
    WITH n, coalesce(bc, bc2, bc3) as context
    RETURN {
        nodeId: n.id,
        nodeType: labels(n)[0],
        bcId: context.id,
        bcName: context.name,
        bcDescription: context.description
    } as result
    """
    
    with get_session() as session:
        result = session.run(query, node_id=node_id)
        record = result.single()
        
        if not record:
            return {"nodeId": node_id, "bcId": None}
        
        return dict(record["result"])


@app.get("/api/graph/expand-with-bc/{node_id}")
async def expand_node_with_bc(node_id: str) -> dict[str, Any]:
    """
    Expand a node and include its parent BoundedContext.
    This ensures nodes are always displayed within their BC container.
    """
    # First get the node's BC context
    context_query = """
    MATCH (n {id: $node_id})
    WITH n, labels(n)[0] as nodeType
    
    // Find parent BC based on node type
    OPTIONAL MATCH (bc1:BoundedContext {id: $node_id})
    OPTIONAL MATCH (bc2:BoundedContext)-[:HAS_AGGREGATE]->(n)
    OPTIONAL MATCH (bc3:BoundedContext)-[:HAS_AGGREGATE]->(agg:Aggregate)-[:HAS_COMMAND]->(n)
    OPTIONAL MATCH (bc4:BoundedContext)-[:HAS_AGGREGATE]->(agg2:Aggregate)-[:HAS_COMMAND]->(cmd:Command)-[:EMITS]->(n)
    OPTIONAL MATCH (bc5:BoundedContext)-[:HAS_POLICY]->(n)
    
    WITH n, nodeType, coalesce(bc1, bc2, bc3, bc4, bc5) as bc
    RETURN n, nodeType, bc
    """
    
    with get_session() as session:
        ctx_result = session.run(context_query, node_id=node_id)
        ctx_record = ctx_result.single()
        
        if not ctx_record:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        
        node_type = ctx_record["nodeType"]
        bc = ctx_record["bc"]
        main_node = dict(ctx_record["n"])
        main_node["type"] = node_type
        
        nodes = []
        relationships = []
        seen_ids = set()
        
        # Always include BC if found
        if bc:
            bc_node = dict(bc)
            bc_node["type"] = "BoundedContext"
            nodes.append(bc_node)
            seen_ids.add(bc["id"])
            
            # Mark all child nodes with their BC
            main_node["bcId"] = bc["id"]
        
        nodes.append(main_node)
        seen_ids.add(node_id)
        
        # Now expand based on node type
        if node_type == "BoundedContext":
            # Get all aggregates, commands, events under this BC
            expand_query = """
            MATCH (bc:BoundedContext {id: $node_id})-[:HAS_AGGREGATE]->(agg:Aggregate)
            OPTIONAL MATCH (agg)-[:HAS_COMMAND]->(cmd:Command)
            OPTIONAL MATCH (cmd)-[:EMITS]->(evt:Event)
            RETURN agg, cmd, evt
            """
            expand_result = session.run(expand_query, node_id=node_id)
            
            for record in expand_result:
                if record["agg"] and record["agg"]["id"] not in seen_ids:
                    agg = dict(record["agg"])
                    agg["type"] = "Aggregate"
                    agg["bcId"] = node_id
                    nodes.append(agg)
                    seen_ids.add(agg["id"])
                    relationships.append({
                        "source": node_id,
                        "target": agg["id"],
                        "type": "HAS_AGGREGATE"
                    })
                
                if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    cmd["bcId"] = node_id
                    nodes.append(cmd)
                    seen_ids.add(cmd["id"])
                    if record["agg"]:
                        relationships.append({
                            "source": record["agg"]["id"],
                            "target": cmd["id"],
                            "type": "HAS_COMMAND"
                        })
                
                if record["evt"] and record["evt"]["id"] not in seen_ids:
                    evt = dict(record["evt"])
                    evt["type"] = "Event"
                    evt["bcId"] = node_id
                    nodes.append(evt)
                    seen_ids.add(evt["id"])
                    if record["cmd"]:
                        relationships.append({
                            "source": record["cmd"]["id"],
                            "target": evt["id"],
                            "type": "EMITS"
                        })
            
            # Get policies
            pol_query = """
            MATCH (bc:BoundedContext {id: $node_id})-[:HAS_POLICY]->(pol:Policy)
            OPTIONAL MATCH (evt:Event)-[:TRIGGERS]->(pol)
            OPTIONAL MATCH (pol)-[:INVOKES]->(cmd:Command)
            RETURN pol, evt.id as triggerEventId, cmd.id as invokeCommandId
            """
            pol_result = session.run(pol_query, node_id=node_id)
            
            for record in pol_result:
                if record["pol"] and record["pol"]["id"] not in seen_ids:
                    pol = dict(record["pol"])
                    pol["type"] = "Policy"
                    pol["bcId"] = node_id
                    nodes.append(pol)
                    seen_ids.add(pol["id"])
                    
                    if record["triggerEventId"]:
                        relationships.append({
                            "source": record["triggerEventId"],
                            "target": pol["id"],
                            "type": "TRIGGERS"
                        })
                    if record["invokeCommandId"]:
                        relationships.append({
                            "source": pol["id"],
                            "target": record["invokeCommandId"],
                            "type": "INVOKES"
                        })
        
        elif node_type == "Aggregate":
            bc_id = bc["id"] if bc else None
            
            # Get Commands and Events
            expand_query = """
            MATCH (agg:Aggregate {id: $node_id})-[:HAS_COMMAND]->(cmd:Command)
            OPTIONAL MATCH (cmd)-[:EMITS]->(evt:Event)
            RETURN cmd, evt
            """
            expand_result = session.run(expand_query, node_id=node_id)
            
            for record in expand_result:
                if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    cmd["bcId"] = bc_id
                    nodes.append(cmd)
                    seen_ids.add(cmd["id"])
                    relationships.append({
                        "source": node_id,
                        "target": cmd["id"],
                        "type": "HAS_COMMAND"
                    })
                
                if record["evt"] and record["evt"]["id"] not in seen_ids:
                    evt = dict(record["evt"])
                    evt["type"] = "Event"
                    evt["bcId"] = bc_id
                    nodes.append(evt)
                    seen_ids.add(evt["id"])
                    relationships.append({
                        "source": record["cmd"]["id"],
                        "target": evt["id"],
                        "type": "EMITS"
                    })
            
            # Also get Policies from the same BC that are triggered by events in this aggregate
            if bc_id:
                pol_query = """
                MATCH (bc:BoundedContext {id: $bc_id})-[:HAS_POLICY]->(pol:Policy)
                OPTIONAL MATCH (evt:Event)-[:TRIGGERS]->(pol)
                OPTIONAL MATCH (pol)-[:INVOKES]->(cmd:Command)
                RETURN pol, evt.id as triggerEventId, cmd.id as invokeCommandId
                """
                pol_result = session.run(pol_query, bc_id=bc_id)
                
                for record in pol_result:
                    if record["pol"] and record["pol"]["id"] not in seen_ids:
                        pol = dict(record["pol"])
                        pol["type"] = "Policy"
                        pol["bcId"] = bc_id
                        pol["triggerEventId"] = record["triggerEventId"]
                        pol["invokeCommandId"] = record["invokeCommandId"]
                        nodes.append(pol)
                        seen_ids.add(pol["id"])
                        
                        # Add TRIGGERS relationship if the trigger event is on canvas
                        if record["triggerEventId"]:
                            relationships.append({
                                "source": record["triggerEventId"],
                                "target": pol["id"],
                                "type": "TRIGGERS"
                            })
                        # Add INVOKES relationship if the command is on canvas
                        if record["invokeCommandId"]:
                            relationships.append({
                                "source": pol["id"],
                                "target": record["invokeCommandId"],
                                "type": "INVOKES"
                            })
        
        elif node_type == "Command":
            bc_id = bc["id"] if bc else None
            
            # Get Events
            expand_query = """
            MATCH (cmd:Command {id: $node_id})-[:EMITS]->(evt:Event)
            RETURN evt
            """
            expand_result = session.run(expand_query, node_id=node_id)
            
            for record in expand_result:
                if record["evt"]:
                    evt = dict(record["evt"])
                    evt["type"] = "Event"
                    evt["bcId"] = bc_id
                    nodes.append(evt)
                    relationships.append({
                        "source": node_id,
                        "target": evt["id"],
                        "type": "EMITS"
                    })
        
        elif node_type == "Event":
            bc_id = bc["id"] if bc else None
            
            # Get Policies triggered by this event
            expand_query = """
            MATCH (evt:Event {id: $node_id})-[:TRIGGERS]->(pol:Policy)
            OPTIONAL MATCH (pol)-[:INVOKES]->(cmd:Command)
            OPTIONAL MATCH (polBc:BoundedContext)-[:HAS_POLICY]->(pol)
            RETURN pol, cmd, polBc
            """
            expand_result = session.run(expand_query, node_id=node_id)
            
            for record in expand_result:
                pol_bc_id = record["polBc"]["id"] if record["polBc"] else bc_id
                
                if record["pol"] and record["pol"]["id"] not in seen_ids:
                    pol = dict(record["pol"])
                    pol["type"] = "Policy"
                    pol["bcId"] = pol_bc_id
                    nodes.append(pol)
                    seen_ids.add(pol["id"])
                    relationships.append({
                        "source": node_id,
                        "target": pol["id"],
                        "type": "TRIGGERS"
                    })
                
                if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    cmd["bcId"] = pol_bc_id
                    nodes.append(cmd)
                    seen_ids.add(cmd["id"])
                    relationships.append({
                        "source": record["pol"]["id"],
                        "target": cmd["id"],
                        "type": "INVOKES"
                    })
        
        elif node_type == "Policy":
            bc_id = bc["id"] if bc else None
            
            # Get Commands invoked by this policy
            expand_query = """
            MATCH (pol:Policy {id: $node_id})-[:INVOKES]->(cmd:Command)
            RETURN cmd
            """
            expand_result = session.run(expand_query, node_id=node_id)
            
            for record in expand_result:
                if record["cmd"]:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    cmd["bcId"] = bc_id
                    nodes.append(cmd)
                    relationships.append({
                        "source": node_id,
                        "target": cmd["id"],
                        "type": "INVOKES"
                    })
        
        # Deduplicate relationships
        unique_rels = []
        seen_rels = set()
        for rel in relationships:
            if rel.get("source") and rel.get("target"):
                key = (rel["source"], rel["target"], rel["type"])
                if key not in seen_rels:
                    seen_rels.add(key)
                    unique_rels.append(rel)
        
        return {
            "nodes": nodes,
            "relationships": unique_rels,
            "bcContext": {
                "id": bc["id"],
                "name": bc["name"],
                "description": bc.get("description")
            } if bc else None
        }


@app.get("/api/graph/event-triggers/{event_id}")
async def get_event_triggers(event_id: str) -> dict[str, Any]:
    """
    Get all Policies triggered by an Event, along with their parent BCs and related nodes.
    Used when double-clicking an Event on canvas to expand triggered policies.
    """
    query = """
    MATCH (evt:Event {id: $event_id})-[:TRIGGERS]->(pol:Policy)<-[:HAS_POLICY]-(bc:BoundedContext)
    OPTIONAL MATCH (pol)-[:INVOKES]->(cmd:Command)<-[:HAS_COMMAND]-(agg:Aggregate)<-[:HAS_AGGREGATE]-(bc)
    OPTIONAL MATCH (cmd)-[:EMITS]->(resultEvt:Event)
    RETURN DISTINCT bc, pol, cmd, agg, resultEvt
    """
    
    with get_session() as session:
        result = session.run(query, event_id=event_id)
        
        nodes = []
        relationships = []
        seen_ids = set()
        bc_nodes = {}  # Track BC nodes to group children
        
        for record in result:
            # Add BC
            if record["bc"] and record["bc"]["id"] not in seen_ids:
                bc = dict(record["bc"])
                bc["type"] = "BoundedContext"
                nodes.append(bc)
                seen_ids.add(bc["id"])
                bc_nodes[bc["id"]] = bc
            
            bc_id = record["bc"]["id"] if record["bc"] else None
            
            # Add Aggregate
            if record["agg"] and record["agg"]["id"] not in seen_ids:
                agg = dict(record["agg"])
                agg["type"] = "Aggregate"
                agg["bcId"] = bc_id
                nodes.append(agg)
                seen_ids.add(agg["id"])
            
            # Add Policy
            if record["pol"] and record["pol"]["id"] not in seen_ids:
                pol = dict(record["pol"])
                pol["type"] = "Policy"
                pol["bcId"] = bc_id
                nodes.append(pol)
                seen_ids.add(pol["id"])
                
                # Event → TRIGGERS → Policy
                relationships.append({
                    "source": event_id,
                    "target": pol["id"],
                    "type": "TRIGGERS"
                })
            
            # Add Command
            if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                cmd = dict(record["cmd"])
                cmd["type"] = "Command"
                cmd["bcId"] = bc_id
                nodes.append(cmd)
                seen_ids.add(cmd["id"])
                
                # Policy → INVOKES → Command
                if record["pol"]:
                    relationships.append({
                        "source": record["pol"]["id"],
                        "target": cmd["id"],
                        "type": "INVOKES"
                    })
                
                # Aggregate → HAS_COMMAND → Command
                if record["agg"]:
                    relationships.append({
                        "source": record["agg"]["id"],
                        "target": cmd["id"],
                        "type": "HAS_COMMAND"
                    })
            
            # Add Result Event
            if record["resultEvt"] and record["resultEvt"]["id"] not in seen_ids:
                evt = dict(record["resultEvt"])
                evt["type"] = "Event"
                evt["bcId"] = bc_id
                nodes.append(evt)
                seen_ids.add(evt["id"])
                
                # Command → EMITS → Event
                if record["cmd"]:
                    relationships.append({
                        "source": record["cmd"]["id"],
                        "target": evt["id"],
                        "type": "EMITS"
                    })
        
        # Deduplicate relationships
        unique_rels = []
        seen_rels = set()
        for rel in relationships:
            key = (rel["source"], rel["target"], rel["type"])
            if key not in seen_rels:
                seen_rels.add(key)
                unique_rels.append(rel)
        
        return {
            "sourceEventId": event_id,
            "nodes": nodes,
            "relationships": unique_rels
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)

