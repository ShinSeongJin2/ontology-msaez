"""
Zero-base SDD v1.1 — Batch/Transaction 경계 도입
Run 단위 트랜잭션 관리
"""

from typing import List, Dict, Optional, Any
from datetime import datetime
from neo4j import GraphDatabase

from .types import Run
from .upsert import UpsertManager


class BatchManager:
    """배치 작업 및 트랜잭션 관리자"""
    
    def __init__(self, driver):
        """
        Args:
            driver: Neo4j 드라이버 인스턴스
        """
        self.driver = driver
        self.upsert = UpsertManager(driver)
    
    def execute_batch_write(self, operations: List[Dict[str, Any]], run_id: Optional[str] = None) -> Dict:
        """
        배치 작업을 단일 트랜잭션으로 실행
        
        Args:
            operations: 작업 목록, 각 작업은 {"type": str, "params": dict} 형태
                - type: "upsert_node", "link", "trace_link" 등
                - params: 작업별 파라미터
            run_id: Run ID (선택사항)
        
        Returns:
            실행 결과 통계
        """
        success_count = 0
        failed = []
        
        with self.driver.session() as session:
            try:
                # 트랜잭션 시작
                with session.begin_transaction() as tx:
                    for i, op in enumerate(operations):
                        try:
                            result = self._execute_operation(tx, op)
                            if result:
                                success_count += 1
                            else:
                                failed.append({"index": i, "operation": op, "error": "Operation returned False"})
                        except Exception as e:
                            failed.append({"index": i, "operation": op, "error": str(e)})
                            # 트랜잭션 롤백을 위해 예외 재발생
                            raise
                
                # 트랜잭션 커밋 (with 블록 종료 시 자동)
                return {
                    "total": len(operations),
                    "success": success_count,
                    "failed": failed,
                    "run_id": run_id
                }
            
            except Exception as e:
                # 트랜잭션 실패
                return {
                    "total": len(operations),
                    "success": success_count,
                    "failed": failed,
                    "run_id": run_id,
                    "transaction_error": str(e)
                }
    
    def _execute_operation(self, tx, operation: Dict[str, Any]) -> bool:
        """
        단일 작업 실행 (트랜잭션 내부)
        
        Args:
            tx: Neo4j 트랜잭션 객체
            operation: 작업 딕셔너리
        
        Returns:
            성공 여부
        """
        op_type = operation.get("type")
        params = operation.get("params", {})
        
        if op_type == "upsert_epic":
            return self._upsert_epic_tx(tx, params)
        elif op_type == "upsert_user_story":
            return self._upsert_user_story_tx(tx, params)
        elif op_type == "upsert_acceptance_criterion":
            return self._upsert_acceptance_criterion_tx(tx, params)
        elif op_type == "upsert_aggregate":
            return self._upsert_aggregate_tx(tx, params)
        elif op_type == "upsert_field":
            return self._upsert_field_tx(tx, params)
        elif op_type == "upsert_command":
            return self._upsert_command_tx(tx, params)
        elif op_type == "upsert_event":
            return self._upsert_event_tx(tx, params)
        elif op_type == "upsert_policy":
            return self._upsert_policy_tx(tx, params)
        elif op_type == "link_epic_to_story":
            return self._link_epic_to_story_tx(tx, params)
        elif op_type == "link_story_to_criterion":
            return self._link_story_to_criterion_tx(tx, params)
        elif op_type == "link_bc_to_aggregate":
            return self._link_bc_to_aggregate_tx(tx, params)
        else:
            raise ValueError(f"Unknown operation type: {op_type}")
    
    # 트랜잭션 내부 Upsert 메서드들
    def _upsert_epic_tx(self, tx, params: dict) -> bool:
        query = """
        MERGE (e:Epic {id: $id})
        SET e.title = $title,
            e.description = $description,
            e.priority = $priority,
            e.status = $status
        RETURN e
        """
        result = tx.run(query, **params)
        return result.single() is not None
    
    def _upsert_user_story_tx(self, tx, params: dict) -> bool:
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
        if params.get("keywords") is None:
            params["keywords"] = []
        result = tx.run(query, **params)
        return result.single() is not None
    
    def _upsert_acceptance_criterion_tx(self, tx, params: dict) -> bool:
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
        if params.get("keywords") is None:
            params["keywords"] = []
        result = tx.run(query, **params)
        return result.single() is not None
    
    def _upsert_aggregate_tx(self, tx, params: dict) -> bool:
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
        result = tx.run(query, **params)
        return result.single() is not None
    
    def _upsert_field_tx(self, tx, params: dict) -> bool:
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
        result = tx.run(query, **params)
        return result.single() is not None
    
    def _upsert_command_tx(self, tx, params: dict) -> bool:
        query = """
        MERGE (cmd:Command {id: $id})
        SET cmd.name = $name,
            cmd.description = $description,
            cmd.syncMode = $syncMode,
            cmd.source = $source,
            cmd.template_key = $template_key
        RETURN cmd
        """
        result = tx.run(query, **params)
        return result.single() is not None
    
    def _upsert_event_tx(self, tx, params: dict) -> bool:
        query = """
        MERGE (evt:Event {id: $id})
        SET evt.name = $name,
            evt.description = $description,
            evt.category = $category,
            evt.reliability = $reliability,
            evt.payload_schema_ref = $payload_schema_ref
        RETURN evt
        """
        result = tx.run(query, **params)
        return result.single() is not None
    
    def _upsert_policy_tx(self, tx, params: dict) -> bool:
        query = """
        MERGE (pol:Policy {id: $id})
        SET pol.name = $name,
            pol.description = $description,
            pol.kind = $kind,
            pol.conditionExpr = $conditionExpr
        RETURN pol
        """
        result = tx.run(query, **params)
        return result.single() is not None
    
    # 트랜잭션 내부 Link 메서드들
    def _link_epic_to_story_tx(self, tx, params: dict) -> bool:
        query = """
        MATCH (e:Epic {id: $epic_id}), (us:UserStory {id: $story_id})
        MERGE (e)-[:HAS_STORY]->(us)
        RETURN e, us
        """
        result = tx.run(query, **params)
        return result.single() is not None
    
    def _link_story_to_criterion_tx(self, tx, params: dict) -> bool:
        query = """
        MATCH (us:UserStory {id: $story_id}), (ac:AcceptanceCriterion {id: $ac_id})
        MERGE (us)-[:HAS_CRITERION]->(ac)
        RETURN us, ac
        """
        result = tx.run(query, **params)
        return result.single() is not None
    
    def _link_bc_to_aggregate_tx(self, tx, params: dict) -> bool:
        query = """
        MATCH (bc:BoundedContext {id: $bc_id}), (agg:Aggregate {id: $agg_id})
        MERGE (bc)-[:HAS_AGGREGATE]->(agg)
        RETURN bc, agg
        """
        result = tx.run(query, **params)
        return result.single() is not None
    
    def upsert_bundle(
        self,
        run_id: str,
        nodes: List[Dict],
        relationships: List[Dict]
    ) -> Dict:
        """
        Story 1건 + AC N건 + Aggregate/Field/Command/Event/Policy 업서트를
        단일 트랜잭션으로 처리
        
        Args:
            run_id: Run ID
            nodes: 노드 목록, 각 노드는 {"type": str, "data": dict} 형태
            relationships: 관계 목록, 각 관계는 {"type": str, "from": str, "to": str, "params": dict} 형태
        
        Returns:
            실행 결과
        """
        operations = []
        
        # 노드 업서트 작업 추가
        for node in nodes:
            node_type = node.get("type")
            node_data = node.get("data", {})
            operations.append({
                "type": f"upsert_{node_type}",
                "params": node_data
            })
        
        # 관계 링크 작업 추가
        for rel in relationships:
            rel_type = rel.get("type")
            from_id = rel.get("from")
            to_id = rel.get("to")
            rel_params = rel.get("params", {})
            
            if rel_type == "HAS_STORY":
                operations.append({
                    "type": "link_epic_to_story",
                    "params": {"epic_id": from_id, "story_id": to_id, **rel_params}
                })
            elif rel_type == "HAS_CRITERION":
                operations.append({
                    "type": "link_story_to_criterion",
                    "params": {"story_id": from_id, "ac_id": to_id, **rel_params}
                })
            elif rel_type == "HAS_AGGREGATE":
                operations.append({
                    "type": "link_bc_to_aggregate",
                    "params": {"bc_id": from_id, "agg_id": to_id, **rel_params}
                })
            # 추가 관계 타입은 필요시 확장
        
        return self.execute_batch_write(operations, run_id=run_id)

