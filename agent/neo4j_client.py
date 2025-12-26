"""
Neo4j Client for Event Storming Graph Operations

This module provides CRUD operations for the Event Storming ontology:
- UserStory, BoundedContext, Aggregate, Command, Event, Policy
- All relationship types (IMPLEMENTS, HAS_AGGREGATE, HAS_COMMAND, etc.)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase

load_dotenv()


@dataclass
class Neo4jConfig:
    """Neo4j connection configuration."""

    uri: str = field(default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    user: str = field(default_factory=lambda: os.getenv("NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD", "12345msaez"))


class Neo4jClient:
    """Neo4j client for Event Storming graph operations."""

    def __init__(self, config: Neo4jConfig | None = None):
        self.config = config or Neo4jConfig()
        self._driver: Driver | None = None

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.config.uri, auth=(self.config.user, self.config.password)
            )
        return self._driver

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None

    @contextmanager
    def session(self):
        """Context manager for Neo4j sessions."""
        session = self.driver.session()
        try:
            yield session
        finally:
            session.close()

    def verify_connection(self) -> bool:
        """Verify Neo4j connection."""
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    # =========================================================================
    # User Story Operations
    # =========================================================================

    def get_all_user_stories(self) -> list[dict[str, Any]]:
        """Fetch all user stories from Neo4j."""
        query = """
        MATCH (us:UserStory)
        OPTIONAL MATCH (us)-[:IMPLEMENTS]->(target)
        WITH us, collect(DISTINCT {type: labels(target)[0], name: target.name, id: target.id}) as implemented_in
        RETURN {
            id: us.id,
            role: us.role,
            action: us.action,
            benefit: us.benefit,
            priority: us.priority,
            status: us.status,
            implemented_in: implemented_in
        } as user_story
        ORDER BY user_story.id
        """
        with self.session() as session:
            result = session.run(query)
            return [dict(record["user_story"]) for record in result]

    def get_unprocessed_user_stories(self) -> list[dict[str, Any]]:
        """Fetch user stories not yet assigned to a Bounded Context."""
        query = """
        MATCH (us:UserStory)
        WHERE NOT (us)-[:IMPLEMENTS]->(:BoundedContext)
        RETURN us {.id, .role, .action, .benefit, .priority, .status} as user_story
        ORDER BY us.priority DESC, us.id
        """
        with self.session() as session:
            result = session.run(query)
            return [dict(record["user_story"]) for record in result]

    def get_user_stories_by_bc(self, bc_id: str) -> list[dict[str, Any]]:
        """Fetch user stories assigned to a specific Bounded Context."""
        query = """
        MATCH (us:UserStory)-[:IMPLEMENTS]->(bc:BoundedContext {id: $bc_id})
        RETURN us {.id, .role, .action, .benefit, .priority, .status} as user_story
        ORDER BY us.id
        """
        with self.session() as session:
            result = session.run(query, bc_id=bc_id)
            return [dict(record["user_story"]) for record in result]

    def create_user_story(
        self,
        id: str,
        role: str,
        action: str,
        benefit: str | None = None,
        priority: str = "medium",
        status: str = "draft",
    ) -> dict[str, Any]:
        """Create a new user story."""
        query = """
        CREATE (us:UserStory {
            id: $id,
            role: $role,
            action: $action,
            benefit: $benefit,
            priority: $priority,
            status: $status
        })
        RETURN us {.id, .role, .action, .benefit, .priority, .status} as user_story
        """
        with self.session() as session:
            result = session.run(
                query,
                id=id,
                role=role,
                action=action,
                benefit=benefit,
                priority=priority,
                status=status,
            )
            return dict(result.single()["user_story"])

    # =========================================================================
    # Bounded Context Operations
    # =========================================================================

    def get_all_bounded_contexts(self) -> list[dict[str, Any]]:
        """Fetch all bounded contexts with their aggregates."""
        query = """
        MATCH (bc:BoundedContext)
        OPTIONAL MATCH (bc)-[:HAS_AGGREGATE]->(agg:Aggregate)
        WITH bc, collect(DISTINCT agg {.id, .name}) as aggregates
        RETURN {
            id: bc.id,
            name: bc.name,
            description: bc.description,
            owner: bc.owner,
            aggregates: aggregates
        } as bounded_context
        ORDER BY bounded_context.name
        """
        with self.session() as session:
            result = session.run(query)
            return [dict(record["bounded_context"]) for record in result]

    def create_bounded_context(
        self,
        id: str,
        name: str,
        description: str | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Create a new bounded context."""
        query = """
        MERGE (bc:BoundedContext {id: $id})
        SET bc.name = $name,
            bc.description = $description,
            bc.owner = $owner
        RETURN bc {.id, .name, .description, .owner} as bounded_context
        """
        with self.session() as session:
            result = session.run(
                query, id=id, name=name, description=description, owner=owner
            )
            return dict(result.single()["bounded_context"])

    def link_user_story_to_bc(
        self, user_story_id: str, bc_id: str, confidence: float = 0.9
    ) -> bool:
        """Link a user story to a bounded context via IMPLEMENTS relationship."""
        query = """
        MATCH (us:UserStory {id: $user_story_id})
        MATCH (bc:BoundedContext {id: $bc_id})
        MERGE (us)-[r:IMPLEMENTS]->(bc)
        SET r.confidence = $confidence,
            r.createdAt = datetime()
        RETURN us.id, bc.id
        """
        with self.session() as session:
            result = session.run(
                query, user_story_id=user_story_id, bc_id=bc_id, confidence=confidence
            )
            return result.single() is not None

    # =========================================================================
    # Aggregate Operations
    # =========================================================================

    def get_aggregates_by_bc(self, bc_id: str) -> list[dict[str, Any]]:
        """Fetch aggregates belonging to a bounded context."""
        query = """
        MATCH (bc:BoundedContext {id: $bc_id})-[:HAS_AGGREGATE]->(agg:Aggregate)
        OPTIONAL MATCH (agg)-[:HAS_COMMAND]->(cmd:Command)
        WITH agg, collect(DISTINCT cmd {.id, .name}) as commands
        RETURN {
            id: agg.id,
            name: agg.name,
            rootEntity: agg.rootEntity,
            invariants: agg.invariants,
            commands: commands
        } as aggregate
        ORDER BY aggregate.name
        """
        with self.session() as session:
            result = session.run(query, bc_id=bc_id)
            return [dict(record["aggregate"]) for record in result]

    def create_aggregate(
        self,
        id: str,
        name: str,
        bc_id: str,
        root_entity: str | None = None,
        invariants: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new aggregate and link it to a bounded context.
        
        IMPORTANT: One Aggregate belongs to exactly ONE Bounded Context.
        If an aggregate with the same ID already exists and belongs to a different BC,
        this will raise an error.
        """
        # First, check if aggregate exists and belongs to a different BC
        check_query = """
        OPTIONAL MATCH (existing:Aggregate {id: $id})<-[:HAS_AGGREGATE]-(otherBC:BoundedContext)
        WHERE otherBC.id <> $bc_id
        RETURN otherBC.id as existing_bc
        """
        with self.session() as session:
            check_result = session.run(check_query, id=id, bc_id=bc_id)
            record = check_result.single()
            if record and record["existing_bc"]:
                raise ValueError(
                    f"Aggregate {id} already belongs to BC {record['existing_bc']}. "
                    f"An Aggregate can only belong to ONE Bounded Context."
                )
        
        # Create or update the aggregate
        query = """
        MATCH (bc:BoundedContext {id: $bc_id})
        MERGE (agg:Aggregate {id: $id})
        SET agg.name = $name,
            agg.rootEntity = $root_entity,
            agg.invariants = $invariants
        MERGE (bc)-[:HAS_AGGREGATE {isPrimary: false}]->(agg)
        RETURN agg {.id, .name, .rootEntity, .invariants} as aggregate
        """
        with self.session() as session:
            result = session.run(
                query,
                id=id,
                name=name,
                bc_id=bc_id,
                root_entity=root_entity or name,
                invariants=invariants or [],
            )
            return dict(result.single()["aggregate"])

    def link_user_story_to_aggregate(
        self, user_story_id: str, aggregate_id: str, confidence: float = 0.9
    ) -> bool:
        """Link a user story to an aggregate via IMPLEMENTS relationship."""
        query = """
        MATCH (us:UserStory {id: $user_story_id})
        MATCH (agg:Aggregate {id: $aggregate_id})
        MERGE (us)-[r:IMPLEMENTS]->(agg)
        SET r.confidence = $confidence,
            r.createdAt = datetime()
        RETURN us.id, agg.id
        """
        with self.session() as session:
            result = session.run(
                query, user_story_id=user_story_id, aggregate_id=aggregate_id, confidence=confidence
            )
            return result.single() is not None

    def link_user_story_to_command(
        self, user_story_id: str, command_id: str, confidence: float = 0.9
    ) -> bool:
        """Link a user story to a command via IMPLEMENTS relationship."""
        query = """
        MATCH (us:UserStory {id: $user_story_id})
        MATCH (cmd:Command {id: $command_id})
        MERGE (us)-[r:IMPLEMENTS]->(cmd)
        SET r.confidence = $confidence,
            r.createdAt = datetime()
        RETURN us.id, cmd.id
        """
        with self.session() as session:
            result = session.run(
                query, user_story_id=user_story_id, command_id=command_id, confidence=confidence
            )
            return result.single() is not None

    def link_user_story_to_event(
        self, user_story_id: str, event_id: str, confidence: float = 0.9
    ) -> bool:
        """Link a user story to an event via IMPLEMENTS relationship."""
        query = """
        MATCH (us:UserStory {id: $user_story_id})
        MATCH (evt:Event {id: $event_id})
        MERGE (us)-[r:IMPLEMENTS]->(evt)
        SET r.confidence = $confidence,
            r.createdAt = datetime()
        RETURN us.id, evt.id
        """
        with self.session() as session:
            result = session.run(
                query, user_story_id=user_story_id, event_id=event_id, confidence=confidence
            )
            return result.single() is not None

    # =========================================================================
    # Command Operations
    # =========================================================================

    def create_command(
        self,
        id: str,
        name: str,
        aggregate_id: str,
        actor: str = "user",
        input_schema: str | None = None,
    ) -> dict[str, Any]:
        """Create a new command and link it to an aggregate."""
        query = """
        MATCH (agg:Aggregate {id: $aggregate_id})
        MERGE (cmd:Command {id: $id})
        SET cmd.name = $name,
            cmd.actor = $actor,
            cmd.inputSchema = $input_schema
        MERGE (agg)-[:HAS_COMMAND]->(cmd)
        RETURN cmd {.id, .name, .actor, .inputSchema} as command
        """
        with self.session() as session:
            result = session.run(
                query,
                id=id,
                name=name,
                aggregate_id=aggregate_id,
                actor=actor,
                input_schema=input_schema,
            )
            return dict(result.single()["command"])

    def get_commands_by_aggregate(self, aggregate_id: str) -> list[dict[str, Any]]:
        """Fetch commands belonging to an aggregate."""
        query = """
        MATCH (agg:Aggregate {id: $aggregate_id})-[:HAS_COMMAND]->(cmd:Command)
        OPTIONAL MATCH (cmd)-[:EMITS]->(evt:Event)
        WITH cmd, collect(DISTINCT evt {.id, .name}) as emits
        RETURN {
            id: cmd.id,
            name: cmd.name,
            actor: cmd.actor,
            inputSchema: cmd.inputSchema,
            emits: emits
        } as command
        ORDER BY command.name
        """
        with self.session() as session:
            result = session.run(query, aggregate_id=aggregate_id)
            return [dict(record["command"]) for record in result]

    # =========================================================================
    # Event Operations
    # =========================================================================

    def create_event(
        self,
        id: str,
        name: str,
        command_id: str,
        version: str = "1.0.0",
        schema: str | None = None,
    ) -> dict[str, Any]:
        """Create a new event and link it to a command via EMITS."""
        query = """
        MATCH (cmd:Command {id: $command_id})
        MERGE (evt:Event {id: $id})
        SET evt.name = $name,
            evt.version = $version,
            evt.schema = $schema,
            evt.isBreaking = false
        MERGE (cmd)-[:EMITS {isGuaranteed: true}]->(evt)
        RETURN evt {.id, .name, .version, .schema} as event
        """
        with self.session() as session:
            result = session.run(
                query,
                id=id,
                name=name,
                command_id=command_id,
                version=version,
                schema=schema,
            )
            return dict(result.single()["event"])

    # =========================================================================
    # Policy Operations
    # =========================================================================

    def create_policy(
        self,
        id: str,
        name: str,
        bc_id: str,
        trigger_event_id: str,
        invoke_command_id: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a policy with TRIGGERS and INVOKES relationships."""
        query = """
        MATCH (bc:BoundedContext {id: $bc_id})
        MATCH (evt:Event {id: $trigger_event_id})
        MATCH (cmd:Command {id: $invoke_command_id})
        MERGE (pol:Policy {id: $id})
        SET pol.name = $name,
            pol.description = $description
        MERGE (bc)-[:HAS_POLICY]->(pol)
        MERGE (evt)-[:TRIGGERS {priority: 1, isEnabled: true}]->(pol)
        MERGE (pol)-[:INVOKES {isAsync: true}]->(cmd)
        RETURN pol {.id, .name, .description} as policy
        """
        with self.session() as session:
            result = session.run(
                query,
                id=id,
                name=name,
                bc_id=bc_id,
                trigger_event_id=trigger_event_id,
                invoke_command_id=invoke_command_id,
                description=description,
            )
            return dict(result.single()["policy"])

    # =========================================================================
    # Graph Traversal & Analysis
    # =========================================================================

    def get_full_event_chain(self) -> list[dict[str, Any]]:
        """Get the full event chain: Command -> Event -> Policy -> Command."""
        query = """
        MATCH (cmd1:Command)-[:EMITS]->(evt:Event)-[:TRIGGERS]->(pol:Policy)-[:INVOKES]->(cmd2:Command)
        MATCH (bc1:BoundedContext)-[:HAS_AGGREGATE]->(agg1:Aggregate)-[:HAS_COMMAND]->(cmd1)
        MATCH (bc2:BoundedContext)-[:HAS_POLICY]->(pol)
        RETURN {
            source_bc: bc1.name,
            source_command: cmd1.name,
            event: evt.name,
            target_bc: bc2.name,
            policy: pol.name,
            target_command: cmd2.name
        } as chain
        """
        with self.session() as session:
            result = session.run(query)
            return [dict(record["chain"]) for record in result]

    def get_impact_analysis(self, event_name: str) -> dict[str, Any]:
        """Analyze the impact of changing a specific event."""
        query = """
        MATCH (evt:Event {name: $event_name})-[:TRIGGERS]->(pol:Policy)<-[:HAS_POLICY]-(bc:BoundedContext)
        MATCH (pol)-[:INVOKES]->(cmd:Command)
        WITH evt, collect({bc: bc.name, policy: pol.name, command: cmd.name}) as impacts
        RETURN {
            event: evt.name,
            version: evt.version,
            affected_count: size(impacts),
            impacts: impacts
        } as analysis
        """
        with self.session() as session:
            result = session.run(query, event_name=event_name)
            record = result.single()
            return dict(record["analysis"]) if record else {}

    def get_graph_statistics(self) -> dict[str, int]:
        """Get statistics about the current graph."""
        query = """
        MATCH (n)
        WITH labels(n)[0] as label, count(n) as count
        RETURN collect({label: label, count: count}) as nodes
        """
        with self.session() as session:
            result = session.run(query)
            nodes = result.single()["nodes"]
            return {item["label"]: item["count"] for item in nodes}


# Singleton instance
_client: Neo4jClient | None = None


def get_neo4j_client() -> Neo4jClient:
    """Get the singleton Neo4j client instance."""
    global _client
    if _client is None:
        _client = Neo4jClient()
    return _client

