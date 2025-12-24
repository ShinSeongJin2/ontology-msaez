"""
Zero-base SDD v1 — Upsert API
id 기반으로 노드를 MERGE하는 함수군
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from neo4j import GraphDatabase

from .types import (
    BoundedContext,
    Aggregate,
    Entity,
    ValueObject,
    Field,
    Command,
    Event,
    Policy,
    Epic,
    UserStory,
    AcceptanceCriterion,
    Run,
)


class UpsertManager:
    """노드 및 관계 Upsert 관리자"""
    
    def __init__(self, driver):
        """
        Args:
            driver: Neo4j 드라이버 인스턴스
        """
        self.driver = driver
    
    # ==========================================
    # Requirements Upsert
    # ==========================================
    
    def upsert_epic(self, epic: Epic) -> bool:
        """Epic 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (e:Epic {id: $id})
            SET e.title = $title,
                e.description = $description,
                e.priority = $priority,
                e.status = $status
            RETURN e
            """
            result = session.run(query, **epic.__dict__)
            return result.single() is not None
    
    def upsert_user_story(self, story: UserStory) -> bool:
        """UserStory 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (us:UserStory {id: $id})
            SET us.title = $title,
                us.storyText = $storyText,
                us.priority = $priority,
                us.status = $status,
                us.asIs = $asIs,
                us.toBe = $toBe,
                us.semantic_text = $semantic_text,
                us.keywords = $keywords
            RETURN us
            """
            params = story.__dict__.copy()
            # None 값을 처리하기 위해 변환
            if params.get("keywords") is None:
                params["keywords"] = []
            result = session.run(query, **params)
            return result.single() is not None
    
    def upsert_acceptance_criterion(self, ac: AcceptanceCriterion) -> bool:
        """AcceptanceCriterion 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (ac:AcceptanceCriterion {id: $id})
            SET ac.title = $title,
                ac.criterionText = $criterionText,
                ac.testType = $testType,
                ac.status = $status,
                ac.semantic_text = $semantic_text,
                ac.keywords = $keywords
            RETURN ac
            """
            params = ac.__dict__.copy()
            if params.get("keywords") is None:
                params["keywords"] = []
            result = session.run(query, **params)
            return result.single() is not None
    
    # ==========================================
    # Domain Upsert
    # ==========================================
    
    def upsert_bounded_context(self, bc: BoundedContext) -> bool:
        """BoundedContext 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (bc:BoundedContext {id: $id})
            SET bc.name = $name,
                bc.description = $description,
                bc.domain = $domain,
                bc.kind = $kind,
                bc.status = $status,
                bc.version = $version,
                bc.source_hash = $source_hash
            RETURN bc
            """
            result = session.run(query, **bc.__dict__)
            return result.single() is not None
    
    def upsert_aggregate(self, agg: Aggregate) -> bool:
        """Aggregate 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (agg:Aggregate {id: $id})
            SET agg.name = $name,
                agg.description = $description,
                agg.kind = $kind,
                agg.version = $version,
                agg.status = $status,
                agg.source_hash = $source_hash
            RETURN agg
            """
            result = session.run(query, **agg.__dict__)
            return result.single() is not None
    
    def upsert_entity(self, entity: Entity) -> bool:
        """Entity 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (e:Entity {id: $id})
            SET e.name = $name,
                e.description = $description,
                e.status = $status,
                e.version = $version
            RETURN e
            """
            result = session.run(query, **entity.__dict__)
            return result.single() is not None
    
    def upsert_value_object(self, vo: ValueObject) -> bool:
        """ValueObject 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (vo:ValueObject {id: $id})
            SET vo.name = $name,
                vo.description = $description,
                vo.status = $status,
                vo.version = $version
            RETURN vo
            """
            result = session.run(query, **vo.__dict__)
            return result.single() is not None
    
    def upsert_field(self, field: Field) -> bool:
        """Field 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (f:Field {id: $id})
            SET f.name = $name,
                f.type = $type,
                f.isKey = $isKey,
                f.isNullable = $isNullable,
                f.isForeignKey = $isForeignKey,
                f.description = $description,
                f.source_hash = $source_hash
            RETURN f
            """
            result = session.run(query, **field.__dict__)
            return result.single() is not None
    
    # ==========================================
    # Behavior Upsert
    # ==========================================
    
    def upsert_command(self, cmd: Command) -> bool:
        """Command 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (cmd:Command {id: $id})
            SET cmd.name = $name,
                cmd.description = $description,
                cmd.syncMode = $syncMode,
                cmd.source = $source,
                cmd.template_key = $template_key
            RETURN cmd
            """
            result = session.run(query, **cmd.__dict__)
            return result.single() is not None
    
    def upsert_event(self, evt: Event) -> bool:
        """Event 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (evt:Event {id: $id})
            SET evt.name = $name,
                evt.description = $description,
                evt.category = $category,
                evt.reliability = $reliability,
                evt.payload_schema_ref = $payload_schema_ref
            RETURN evt
            """
            result = session.run(query, **evt.__dict__)
            return result.single() is not None
    
    def upsert_policy(self, policy: Policy) -> bool:
        """Policy 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (pol:Policy {id: $id})
            SET pol.name = $name,
                pol.description = $description,
                pol.kind = $kind,
                pol.conditionExpr = $conditionExpr
            RETURN pol
            """
            result = session.run(query, **policy.__dict__)
            return result.single() is not None
    
    # ==========================================
    # Metadata Upsert
    # ==========================================
    
    def upsert_run(self, run: Run) -> bool:
        """Run 메타데이터 노드 업서트"""
        with self.driver.session() as session:
            query = """
            MERGE (r:Run {id: $id})
            SET r.run_type = $run_type,
                r.prompt_version = $prompt_version,
                r.model = $model,
                r.started_at = $started_at,
                r.finished_at = $finished_at,
                r.status = $status,
                r.metadata = $metadata
            RETURN r
            """
            params = run.__dict__.copy()
            # datetime을 ISO 형식 문자열로 변환
            if params.get("started_at") and isinstance(params["started_at"], datetime):
                params["started_at"] = params["started_at"].isoformat()
            if params.get("finished_at") and isinstance(params["finished_at"], datetime):
                params["finished_at"] = params["finished_at"].isoformat()
            result = session.run(query, **params)
            return result.single() is not None
    
    # ==========================================
    # Structural Relationships
    # ==========================================
    
    def link_epic_to_story(self, epic_id: str, story_id: str) -> bool:
        """Epic -[:HAS_STORY]-> UserStory"""
        with self.driver.session() as session:
            query = """
            MATCH (e:Epic {id: $epic_id}), (us:UserStory {id: $story_id})
            MERGE (e)-[:HAS_STORY]->(us)
            RETURN e, us
            """
            result = session.run(query, epic_id=epic_id, story_id=story_id)
            return result.single() is not None
    
    def link_story_to_criterion(self, story_id: str, ac_id: str) -> bool:
        """UserStory -[:HAS_CRITERION]-> AcceptanceCriterion"""
        with self.driver.session() as session:
            query = """
            MATCH (us:UserStory {id: $story_id}), (ac:AcceptanceCriterion {id: $ac_id})
            MERGE (us)-[:HAS_CRITERION]->(ac)
            RETURN us, ac
            """
            result = session.run(query, story_id=story_id, ac_id=ac_id)
            return result.single() is not None
    
    def link_bc_to_aggregate(self, bc_id: str, agg_id: str) -> bool:
        """BoundedContext -[:HAS_AGGREGATE]-> Aggregate"""
        with self.driver.session() as session:
            query = """
            MATCH (bc:BoundedContext {id: $bc_id}), (agg:Aggregate {id: $agg_id})
            MERGE (bc)-[:HAS_AGGREGATE]->(agg)
            RETURN bc, agg
            """
            result = session.run(query, bc_id=bc_id, agg_id=agg_id)
            return result.single() is not None
    
    def link_aggregate_to_entity(self, agg_id: str, entity_id: str) -> bool:
        """Aggregate -[:HAS_ENTITY]-> Entity"""
        with self.driver.session() as session:
            query = """
            MATCH (agg:Aggregate {id: $agg_id}), (e:Entity {id: $entity_id})
            MERGE (agg)-[:HAS_ENTITY]->(e)
            RETURN agg, e
            """
            result = session.run(query, agg_id=agg_id, entity_id=entity_id)
            return result.single() is not None
    
    def link_aggregate_to_value_object(self, agg_id: str, vo_id: str) -> bool:
        """Aggregate -[:HAS_VALUE_OBJECT]-> ValueObject"""
        with self.driver.session() as session:
            query = """
            MATCH (agg:Aggregate {id: $agg_id}), (vo:ValueObject {id: $vo_id})
            MERGE (agg)-[:HAS_VALUE_OBJECT]->(vo)
            RETURN agg, vo
            """
            result = session.run(query, agg_id=agg_id, vo_id=vo_id)
            return result.single() is not None
    
    def link_to_field(self, parent_id: str, field_id: str, parent_label: str) -> bool:
        """
        Aggregate/Entity/ValueObject -[:HAS_FIELD]-> Field
        
        Args:
            parent_id: 부모 노드 ID
            field_id: Field 노드 ID
            parent_label: "Aggregate", "Entity", "ValueObject" 중 하나
        """
        with self.driver.session() as session:
            query = f"""
            MATCH (parent:{parent_label} {{id: $parent_id}}), (f:Field {{id: $field_id}})
            MERGE (parent)-[:HAS_FIELD]->(f)
            RETURN parent, f
            """
            result = session.run(query, parent_id=parent_id, field_id=field_id)
            return result.single() is not None
    
    # ==========================================
    # Event Storming Relationships
    # ==========================================
    
    def link_aggregate_to_command(self, agg_id: str, cmd_id: str) -> bool:
        """Aggregate -[:HANDLES_COMMAND]-> Command"""
        with self.driver.session() as session:
            query = """
            MATCH (agg:Aggregate {id: $agg_id}), (cmd:Command {id: $cmd_id})
            MERGE (agg)-[:HANDLES_COMMAND]->(cmd)
            RETURN agg, cmd
            """
            result = session.run(query, agg_id=agg_id, cmd_id=cmd_id)
            return result.single() is not None
    
    def link_command_to_event(self, cmd_id: str, evt_id: str) -> bool:
        """Command -[:EMITS_EVENT]-> Event"""
        with self.driver.session() as session:
            query = """
            MATCH (cmd:Command {id: $cmd_id}), (evt:Event {id: $evt_id})
            MERGE (cmd)-[:EMITS_EVENT]->(evt)
            RETURN cmd, evt
            """
            result = session.run(query, cmd_id=cmd_id, evt_id=evt_id)
            return result.single() is not None
    
    def link_aggregate_to_event(self, agg_id: str, evt_id: str) -> bool:
        """Aggregate -[:EMITS_EVENT]-> Event (선택적 명시적 표현)"""
        with self.driver.session() as session:
            query = """
            MATCH (agg:Aggregate {id: $agg_id}), (evt:Event {id: $evt_id})
            MERGE (agg)-[:EMITS_EVENT]->(evt)
            RETURN agg, evt
            """
            result = session.run(query, agg_id=agg_id, evt_id=evt_id)
            return result.single() is not None
    
    def link_policy_to_event(self, pol_id: str, evt_id: str) -> bool:
        """Policy -[:LISTENS_EVENT]-> Event"""
        with self.driver.session() as session:
            query = """
            MATCH (pol:Policy {id: $pol_id}), (evt:Event {id: $evt_id})
            MERGE (pol)-[:LISTENS_EVENT]->(evt)
            RETURN pol, evt
            """
            result = session.run(query, pol_id=pol_id, evt_id=evt_id)
            return result.single() is not None
    
    def link_policy_to_command(self, pol_id: str, cmd_id: str) -> bool:
        """Policy -[:TRIGGERS_COMMAND]-> Command"""
        with self.driver.session() as session:
            query = """
            MATCH (pol:Policy {id: $pol_id}), (cmd:Command {id: $cmd_id})
            MERGE (pol)-[:TRIGGERS_COMMAND]->(cmd)
            RETURN pol, cmd
            """
            result = session.run(query, pol_id=pol_id, cmd_id=cmd_id)
            return result.single() is not None
    
    def link_event_to_aggregate(self, evt_id: str, agg_id: str) -> bool:
        """Event -[:AFFECTS_AGGREGATE]-> Aggregate"""
        with self.driver.session() as session:
            query = """
            MATCH (evt:Event {id: $evt_id}), (agg:Aggregate {id: $agg_id})
            MERGE (evt)-[:AFFECTS_AGGREGATE]->(agg)
            RETURN evt, agg
            """
            result = session.run(query, evt_id=evt_id, agg_id=agg_id)
            return result.single() is not None
    
    # ==========================================
    # Reference Relationships
    # ==========================================
    
    def link_aggregate_reference(self, from_agg_id: str, to_agg_id: str, via_field: Optional[str] = None) -> bool:
        """Aggregate -[:REFERS_TO_AGGREGATE]-> Aggregate"""
        with self.driver.session() as session:
            if via_field:
                query = """
                MATCH (from:Aggregate {id: $from_id}), (to:Aggregate {id: $to_id})
                MERGE (from)-[r:REFERS_TO_AGGREGATE]->(to)
                SET r.viaField = $via_field
                RETURN from, to, r
                """
                result = session.run(query, from_id=from_agg_id, to_id=to_agg_id, via_field=via_field)
            else:
                query = """
                MATCH (from:Aggregate {id: $from_id}), (to:Aggregate {id: $to_id})
                MERGE (from)-[:REFERS_TO_AGGREGATE]->(to)
                RETURN from, to
                """
                result = session.run(query, from_id=from_agg_id, to_id=to_agg_id)
            return result.single() is not None
    
    def link_field_reference(self, from_field_id: str, to_field_id: str) -> bool:
        """Field -[:REFERS_TO_FIELD]-> Field"""
        with self.driver.session() as session:
            query = """
            MATCH (from:Field {id: $from_id}), (to:Field {id: $to_id})
            MERGE (from)-[:REFERS_TO_FIELD]->(to)
            RETURN from, to
            """
            result = session.run(query, from_id=from_field_id, to_id=to_field_id)
            return result.single() is not None

