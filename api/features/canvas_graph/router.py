from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from starlette.requests import Request

from api.platform.neo4j import get_session
from api.platform.observability.request_logging import http_context, summarize_for_log
from api.platform.observability.smart_logger import SmartLogger

router = APIRouter(prefix="/api/graph", tags=["canvas-graph"])


@router.delete("/clear")
async def clear_all_nodes(request: Request):
    """
    DELETE /api/graph/clear - 모든 노드와 관계 삭제
    새로운 인제스션 전에 기존 데이터를 모두 삭제합니다.
    """
    query = """
    MATCH (n)
    DETACH DELETE n
    """
    SmartLogger.log(
        "WARNING",
        "Graph clear requested: DETACH DELETE all nodes/relationships (destructive).",
        category="api.graph.clear.request",
        params=http_context(request),
    )
    with get_session() as session:
        result = session.run(query)
        summary = result.consume()
        SmartLogger.log(
            "INFO",
            "Graph cleared: all nodes/relationships removed.",
            category="api.graph.clear.done",
            params={
                **http_context(request),
                "deleted": {
                    "nodes_deleted": summary.counters.nodes_deleted,
                    "relationships_deleted": summary.counters.relationships_deleted,
                },
            },
        )
        return {
            "status": "cleared",
            "nodes_deleted": summary.counters.nodes_deleted,
            "relationships_deleted": summary.counters.relationships_deleted,
        }


@router.get("/stats")
async def get_graph_stats(request: Request):
    """
    GET /api/graph/stats - 그래프 통계 조회
    현재 Neo4j에 저장된 노드 수를 반환합니다.
    """
    query = """
    MATCH (n)
    WITH labels(n)[0] as label, count(n) as count
    RETURN collect({label: label, count: count}) as stats
    """
    SmartLogger.log(
        "INFO",
        "Graph stats requested: counting nodes by label.",
        category="api.graph.stats.request",
        params=http_context(request),
    )
    with get_session() as session:
        result = session.run(query)
        record = result.single()
        if record:
            stats = {item["label"]: item["count"] for item in record["stats"] if item["label"]}
            total = sum(stats.values())
            SmartLogger.log(
                "INFO",
                "Graph stats computed: counts by label returned.",
                category="api.graph.stats.done",
                params={**http_context(request), "total": total, "by_type": stats},
            )
            return {"total": total, "by_type": stats}
        SmartLogger.log(
            "INFO",
            "Graph stats empty: no nodes found.",
            category="api.graph.stats.empty",
            params=http_context(request),
        )
        return {"total": 0, "by_type": {}}


@router.get("/subgraph")
async def get_subgraph(
    request: Request,
    node_ids: list[str] = Query(..., description="List of node IDs to include"),
) -> dict[str, Any]:
    """
    GET /api/graph/subgraph - 선택 노드 기준 서브그래프
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

    SmartLogger.log(
        "INFO",
        "Subgraph requested: returning nodes + relationships for given node_ids.",
        category="api.graph.subgraph.request",
        params={**http_context(request), "inputs": {"node_ids": summarize_for_log(node_ids)}},
    )
    with get_session() as session:
        result = session.run(query, node_ids=node_ids)
        record = result.single()

        if not record:
            SmartLogger.log(
                "INFO",
                "Subgraph empty: no matching nodes found for provided ids.",
                category="api.graph.subgraph.empty",
                params={**http_context(request), "inputs": {"node_ids": summarize_for_log(node_ids)}},
            )
            return {"nodes": [], "relationships": []}

        nodes = record["nodes"]
        relationships = record["relationships"]

        payload = {"nodes": nodes, "relationships": relationships}
        SmartLogger.log(
            "INFO",
            "Subgraph returned.",
            category="api.graph.subgraph.done",
            params={**http_context(request), "summary": {"nodes": len(nodes), "relationships": len(relationships)}},
        )
        return payload


@router.get("/expand/{node_id}")
async def expand_node(node_id: str, request: Request) -> dict[str, Any]:
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
        SmartLogger.log(
            "INFO",
            "Expand requested: expanding connected nodes by node type.",
            category="api.graph.expand.request",
            params={**http_context(request), "inputs": {"node_id": node_id}},
        )
        type_result = session.run(type_query, node_id=node_id)
        type_record = type_result.single()

        if not type_record:
            SmartLogger.log(
                "WARNING",
                "Expand aborted: node_id not found.",
                category="api.graph.expand.not_found",
                params={**http_context(request), "inputs": {"node_id": node_id}},
            )
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        node_type = type_record["nodeType"]
        main_node = dict(type_record["node"])
        main_node["type"] = node_type
        SmartLogger.log(
            "INFO",
            "Expand node type resolved: determining expansion strategy.",
            category="api.graph.expand.node_type",
            params={**http_context(request), "inputs": {"node_id": node_id}, "nodeType": node_type},
        )

        nodes = [main_node]
        relationships: list[dict[str, Any]] = []

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
                        relationships.append(
                            {"source": record["triggerEventId"], "target": pol["id"], "type": "TRIGGERS"}
                        )
                    if record["invokeCommandId"]:
                        relationships.append({"source": pol["id"], "target": record["invokeCommandId"], "type": "INVOKES"})

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
                    relationships.append({"source": node_id, "target": cmd["id"], "type": "HAS_COMMAND"})

                if record["evt"] and record["evt"]["id"] not in seen_ids:
                    evt = dict(record["evt"])
                    evt["type"] = "Event"
                    nodes.append(evt)
                    seen_ids.add(evt["id"])
                    relationships.append({"source": record["cmd"]["id"], "target": evt["id"], "type": "EMITS"})

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
                    relationships.append({"source": node_id, "target": evt["id"], "type": "EMITS"})

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
                    relationships.append({"source": node_id, "target": pol["id"], "type": "TRIGGERS"})

                if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    nodes.append(cmd)
                    seen_ids.add(cmd["id"])
                    relationships.append({"source": record["pol"]["id"], "target": cmd["id"], "type": "INVOKES"})

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
                    relationships.append({"source": node_id, "target": cmd["id"], "type": "INVOKES"})

        # Deduplicate relationships
        unique_rels = []
        seen_rels = set()
        for rel in relationships:
            if rel.get("source") and rel.get("target"):
                key = (rel["source"], rel["target"], rel["type"])
                if key not in seen_rels:
                    seen_rels.add(key)
                    unique_rels.append(rel)

        return {"nodes": nodes, "relationships": unique_rels}


@router.get("/find-relations")
async def find_relations(
    request: Request,
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

    relationships: list[dict[str, Any]] = []
    seen = set()

    with get_session() as session:
        SmartLogger.log(
            "INFO",
            "Find relations requested: discovering relationships among canvas nodes.",
            category="api.graph.find_relations.request",
            params={**http_context(request), "inputs": {"node_ids": summarize_for_log(node_ids)}},
        )
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

    SmartLogger.log(
        "INFO",
        "Find relations returned.",
        category="api.graph.find_relations.done",
        params={**http_context(request), "summary": {"relationships": len(relationships)}},
    )
    return relationships


@router.get("/find-cross-bc-relations")
async def find_cross_bc_relations(
    request: Request,
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
        SmartLogger.log(
            "INFO",
            "Find cross-BC relations requested: checking TRIGGERS/INVOKES across new vs existing sets.",
            category="api.graph.find_cross_bc.request",
            params={
                **http_context(request),
                "inputs": {
                    "new_node_ids": summarize_for_log(new_node_ids),
                    "existing_node_ids": summarize_for_log(existing_node_ids),
                },
            },
        )
        result = session.run(query, new_ids=new_node_ids, existing_ids=existing_node_ids)
        record = result.single()

        if not record:
            SmartLogger.log(
                "INFO",
                "Find cross-BC relations empty: no matching cross-BC edges found.",
                category="api.graph.find_cross_bc.empty",
                params={**http_context(request)},
            )
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

        SmartLogger.log(
            "INFO",
            "Find cross-BC relations returned.",
            category="api.graph.find_cross_bc.done",
            params={**http_context(request), "summary": {"relationships": len(relationships)}},
        )
        return relationships


@router.get("/node-context/{node_id}")
async def get_node_context(node_id: str, request: Request) -> dict[str, Any]:
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
        SmartLogger.log(
            "INFO",
            "Node context requested: resolving parent BC for node.",
            category="api.graph.node_context.request",
            params={**http_context(request), "inputs": {"node_id": node_id}},
        )
        result = session.run(query, node_id=node_id)
        record = result.single()

        if not record:
            SmartLogger.log(
                "WARNING",
                "Node context not found: node_id missing or BC could not be resolved.",
                category="api.graph.node_context.not_found",
                params={**http_context(request), "inputs": {"node_id": node_id}},
            )
            return {"nodeId": node_id, "bcId": None}

        payload = dict(record["result"])
        SmartLogger.log(
            "INFO",
            "Node context returned.",
            category="api.graph.node_context.done",
            params={**http_context(request), "result": payload},
        )
        return payload


@router.get("/expand-with-bc/{node_id}")
async def expand_node_with_bc(node_id: str, request: Request) -> dict[str, Any]:
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
        SmartLogger.log(
            "INFO",
            "Expand-with-BC requested: expanding node and including its parent BC for grouping.",
            category="api.graph.expand_with_bc.request",
            params={**http_context(request), "inputs": {"node_id": node_id}},
        )
        ctx_result = session.run(context_query, node_id=node_id)
        ctx_record = ctx_result.single()

        if not ctx_record:
            SmartLogger.log(
                "WARNING",
                "Expand-with-BC aborted: node_id not found.",
                category="api.graph.expand_with_bc.not_found",
                params={**http_context(request), "inputs": {"node_id": node_id}},
            )
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        node_type = ctx_record["nodeType"]
        bc = ctx_record["bc"]
        main_node = dict(ctx_record["n"])
        main_node["type"] = node_type

        nodes: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
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
                    relationships.append({"source": node_id, "target": agg["id"], "type": "HAS_AGGREGATE"})

                if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    cmd["bcId"] = node_id
                    nodes.append(cmd)
                    seen_ids.add(cmd["id"])
                    if record["agg"]:
                        relationships.append({"source": record["agg"]["id"], "target": cmd["id"], "type": "HAS_COMMAND"})

                if record["evt"] and record["evt"]["id"] not in seen_ids:
                    evt = dict(record["evt"])
                    evt["type"] = "Event"
                    evt["bcId"] = node_id
                    nodes.append(evt)
                    seen_ids.add(evt["id"])
                    if record["cmd"]:
                        relationships.append({"source": record["cmd"]["id"], "target": evt["id"], "type": "EMITS"})

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
                        relationships.append({"source": record["triggerEventId"], "target": pol["id"], "type": "TRIGGERS"})
                    if record["invokeCommandId"]:
                        relationships.append({"source": pol["id"], "target": record["invokeCommandId"], "type": "INVOKES"})

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
                    relationships.append({"source": node_id, "target": cmd["id"], "type": "HAS_COMMAND"})

                if record["evt"] and record["evt"]["id"] not in seen_ids:
                    evt = dict(record["evt"])
                    evt["type"] = "Event"
                    evt["bcId"] = bc_id
                    nodes.append(evt)
                    seen_ids.add(evt["id"])
                    relationships.append({"source": record["cmd"]["id"], "target": evt["id"], "type": "EMITS"})

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

                        if record["triggerEventId"]:
                            relationships.append({"source": record["triggerEventId"], "target": pol["id"], "type": "TRIGGERS"})
                        if record["invokeCommandId"]:
                            relationships.append({"source": pol["id"], "target": record["invokeCommandId"], "type": "INVOKES"})

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
                    relationships.append({"source": node_id, "target": evt["id"], "type": "EMITS"})

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
                    relationships.append({"source": node_id, "target": pol["id"], "type": "TRIGGERS"})

                if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                    cmd = dict(record["cmd"])
                    cmd["type"] = "Command"
                    cmd["bcId"] = pol_bc_id
                    nodes.append(cmd)
                    seen_ids.add(cmd["id"])
                    relationships.append({"source": record["pol"]["id"], "target": cmd["id"], "type": "INVOKES"})

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
                    relationships.append({"source": node_id, "target": cmd["id"], "type": "INVOKES"})

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
            "bcContext": {"id": bc["id"], "name": bc["name"], "description": bc.get("description")} if bc else None,
        }


@router.get("/event-triggers/{event_id}")
async def get_event_triggers(event_id: str, request: Request) -> dict[str, Any]:
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
        SmartLogger.log(
            "INFO",
            "Event triggers requested: expanding policies triggered by this event (incl. BC context).",
            category="api.graph.event_triggers.request",
            params={**http_context(request), "inputs": {"event_id": event_id}},
        )
        result = session.run(query, event_id=event_id)

        nodes: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        seen_ids = set()

        for record in result:
            # Add BC
            if record["bc"] and record["bc"]["id"] not in seen_ids:
                bc = dict(record["bc"])
                bc["type"] = "BoundedContext"
                nodes.append(bc)
                seen_ids.add(bc["id"])

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
                relationships.append({"source": event_id, "target": pol["id"], "type": "TRIGGERS"})

            # Add Command
            if record["cmd"] and record["cmd"]["id"] not in seen_ids:
                cmd = dict(record["cmd"])
                cmd["type"] = "Command"
                cmd["bcId"] = bc_id
                nodes.append(cmd)
                seen_ids.add(cmd["id"])

                # Policy → INVOKES → Command
                if record["pol"]:
                    relationships.append({"source": record["pol"]["id"], "target": cmd["id"], "type": "INVOKES"})

                # Aggregate → HAS_COMMAND → Command
                if record["agg"]:
                    relationships.append({"source": record["agg"]["id"], "target": cmd["id"], "type": "HAS_COMMAND"})

            # Add Result Event
            if record["resultEvt"] and record["resultEvt"]["id"] not in seen_ids:
                evt = dict(record["resultEvt"])
                evt["type"] = "Event"
                evt["bcId"] = bc_id
                nodes.append(evt)
                seen_ids.add(evt["id"])

                # Command → EMITS → Event
                if record["cmd"]:
                    relationships.append({"source": record["cmd"]["id"], "target": evt["id"], "type": "EMITS"})

        # Deduplicate relationships
        unique_rels = []
        seen_rels = set()
        for rel in relationships:
            key = (rel["source"], rel["target"], rel["type"])
            if key not in seen_rels:
                seen_rels.add(key)
                unique_rels.append(rel)

        return {"sourceEventId": event_id, "nodes": nodes, "relationships": unique_rels}


