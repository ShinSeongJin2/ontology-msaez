"""
Zero-base SDD v1 — Traceability 링크 생성
IMPACTS, COVERS 등의 추적 링크를 생성/갱신하는 함수
"""

from typing import Optional
from datetime import datetime
from neo4j import GraphDatabase

from .types import TraceRelationship


class TraceabilityManager:
    """Traceability 링크 관리자"""
    
    def __init__(self, driver):
        """
        Args:
            driver: Neo4j 드라이버 인스턴스
        """
        self.driver = driver
    
    # ==========================================
    # IMPACTS 링크 (Requirements → Domain)
    # ==========================================
    
    def link_story_to_aggregate(
        self,
        story_id: str,
        agg_id: str,
        confidence: float = 1.0,
        rationale: str = "",
        evidence_ref: Optional[str] = None
    ) -> bool:
        """
        UserStory -[:IMPACTS_AGGREGATE]-> Aggregate
        
        Args:
            story_id: UserStory ID
            agg_id: Aggregate ID
            confidence: 신뢰도 (0.0 ~ 1.0)
            rationale: 근거 설명
            evidence_ref: 증거 참조 (로그/파일 키 등)
        """
        with self.driver.session() as session:
            query = """
            MATCH (us:UserStory {id: $story_id}), (agg:Aggregate {id: $agg_id})
            MERGE (us)-[r:IMPACTS_AGGREGATE]->(agg)
            SET r.confidence = $confidence,
                r.rationale = $rationale,
                r.evidence_ref = $evidence_ref,
                r.created_at = datetime()
            RETURN us, agg, r
            """
            result = session.run(
                query,
                story_id=story_id,
                agg_id=agg_id,
                confidence=confidence,
                rationale=rationale,
                evidence_ref=evidence_ref
            )
            return result.single() is not None
    
    def link_criterion_to_field(
        self,
        ac_id: str,
        field_id: str,
        confidence: float = 1.0,
        rationale: str = "",
        evidence_ref: Optional[str] = None
    ) -> bool:
        """
        AcceptanceCriterion -[:IMPACTS_FIELD]-> Field
        
        Args:
            ac_id: AcceptanceCriterion ID
            field_id: Field ID
            confidence: 신뢰도 (0.0 ~ 1.0)
            rationale: 근거 설명
            evidence_ref: 증거 참조
        """
        with self.driver.session() as session:
            query = """
            MATCH (ac:AcceptanceCriterion {id: $ac_id}), (f:Field {id: $field_id})
            MERGE (ac)-[r:IMPACTS_FIELD]->(f)
            SET r.confidence = $confidence,
                r.rationale = $rationale,
                r.evidence_ref = $evidence_ref,
                r.created_at = datetime()
            RETURN ac, f, r
            """
            result = session.run(
                query,
                ac_id=ac_id,
                field_id=field_id,
                confidence=confidence,
                rationale=rationale,
                evidence_ref=evidence_ref
            )
            return result.single() is not None
    
    # ==========================================
    # COVERS 링크 (Requirements → Behavior)
    # ==========================================
    
    def link_criterion_to_command(
        self,
        ac_id: str,
        cmd_id: str,
        confidence: float = 1.0,
        rationale: str = "",
        evidence_ref: Optional[str] = None
    ) -> bool:
        """
        AcceptanceCriterion -[:COVERS_COMMAND]-> Command
        
        Args:
            ac_id: AcceptanceCriterion ID
            cmd_id: Command ID
            confidence: 신뢰도 (0.0 ~ 1.0)
            rationale: 근거 설명
            evidence_ref: 증거 참조
        """
        with self.driver.session() as session:
            query = """
            MATCH (ac:AcceptanceCriterion {id: $ac_id}), (cmd:Command {id: $cmd_id})
            MERGE (ac)-[r:COVERS_COMMAND]->(cmd)
            SET r.confidence = $confidence,
                r.rationale = $rationale,
                r.evidence_ref = $evidence_ref,
                r.created_at = datetime()
            RETURN ac, cmd, r
            """
            result = session.run(
                query,
                ac_id=ac_id,
                cmd_id=cmd_id,
                confidence=confidence,
                rationale=rationale,
                evidence_ref=evidence_ref
            )
            return result.single() is not None
    
    def link_criterion_to_event(
        self,
        ac_id: str,
        evt_id: str,
        confidence: float = 1.0,
        rationale: str = "",
        evidence_ref: Optional[str] = None
    ) -> bool:
        """
        AcceptanceCriterion -[:COVERS_EVENT]-> Event
        
        Args:
            ac_id: AcceptanceCriterion ID
            evt_id: Event ID
            confidence: 신뢰도 (0.0 ~ 1.0)
            rationale: 근거 설명
            evidence_ref: 증거 참조
        """
        with self.driver.session() as session:
            query = """
            MATCH (ac:AcceptanceCriterion {id: $ac_id}), (evt:Event {id: $evt_id})
            MERGE (ac)-[r:COVERS_EVENT]->(evt)
            SET r.confidence = $confidence,
                r.rationale = $rationale,
                r.evidence_ref = $evidence_ref,
                r.created_at = datetime()
            RETURN ac, evt, r
            """
            result = session.run(
                query,
                ac_id=ac_id,
                evt_id=evt_id,
                confidence=confidence,
                rationale=rationale,
                evidence_ref=evidence_ref
            )
            return result.single() is not None
    
    # ==========================================
    # Batch 링크 생성 (편의 함수)
    # ==========================================
    
    def batch_link_story_impacts(
        self,
        story_id: str,
        aggregate_ids: list[str],
        default_confidence: float = 0.9,
        default_rationale: str = "Story impacts aggregate"
    ) -> dict:
        """
        UserStory → 여러 Aggregate에 대한 IMPACTS 링크 일괄 생성
        
        Returns:
            성공/실패 통계
        """
        success_count = 0
        failed = []
        
        for agg_id in aggregate_ids:
            try:
                if self.link_story_to_aggregate(
                    story_id, agg_id,
                    confidence=default_confidence,
                    rationale=default_rationale
                ):
                    success_count += 1
                else:
                    failed.append(agg_id)
            except Exception as e:
                failed.append((agg_id, str(e)))
        
        return {
            "total": len(aggregate_ids),
            "success": success_count,
            "failed": failed
        }
    
    def batch_link_criterion_covers(
        self,
        ac_id: str,
        command_ids: Optional[list[str]] = None,
        event_ids: Optional[list[str]] = None,
        default_confidence: float = 0.95,
        default_rationale: str = "Criterion covers behavior"
    ) -> dict:
        """
        AcceptanceCriterion → Command/Event에 대한 COVERS 링크 일괄 생성
        
        Returns:
            성공/실패 통계
        """
        command_ids = command_ids or []
        event_ids = event_ids or []
        
        success_count = 0
        failed = []
        
        for cmd_id in command_ids:
            try:
                if self.link_criterion_to_command(
                    ac_id, cmd_id,
                    confidence=default_confidence,
                    rationale=default_rationale
                ):
                    success_count += 1
                else:
                    failed.append(("command", cmd_id))
            except Exception as e:
                failed.append(("command", cmd_id, str(e)))
        
        for evt_id in event_ids:
            try:
                if self.link_criterion_to_event(
                    ac_id, evt_id,
                    confidence=default_confidence,
                    rationale=default_rationale
                ):
                    success_count += 1
                else:
                    failed.append(("event", evt_id))
            except Exception as e:
                failed.append(("event", evt_id, str(e)))
        
        return {
            "total": len(command_ids) + len(event_ids),
            "success": success_count,
            "failed": failed
        }

