"""
Zero-base SDD v1.1 — Change Detection (변경 감지)
옵션 A: Change 노드 방식
"""

from typing import Optional, Dict, List
from datetime import datetime
from neo4j import GraphDatabase
import hashlib
import json


class ChangeLogger:
    """변경 감지 및 로깅 (Change 노드 방식)"""
    
    def __init__(self, driver):
        """
        Args:
            driver: Neo4j 드라이버 인스턴스
        """
        self.driver = driver
    
    def _generate_hash(self, data: dict) -> str:
        """
        노드 데이터에서 변경 감지용 해시 생성
        
        주의: source_hash 속성 자체는 해시 계산에서 제외합니다.
        (source_hash는 변경 감지의 결과이지, 변경 감지의 기준이 아니기 때문)
        
        Args:
            data: 노드 속성 딕셔너리 (source_hash 포함 가능)
        
        Returns:
            SHA256 해시 문자열 (source_hash 제외한 모든 속성 기준)
        """
        # source_hash 자체는 제외하고 해시 생성
        # 이유: source_hash는 변경 감지 결과를 저장하는 속성이므로,
        # 변경 감지 기준에서 제외해야 함
        data_for_hash = {k: v for k, v in data.items() if k != "source_hash"}
        data_str = json.dumps(data_for_hash, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data_str.encode("utf-8")).hexdigest()
    
    def detect_change(
        self,
        node_label: str,
        node_id: str,
        new_data: dict,
        reason: str = "upsert"
    ) -> Dict:
        """
        노드 변경 감지 및 Change 노드 생성
        
        변경 감지 기준 (v1.1 의도 B - 권장):
        - source_hash가 같으면 changed=False (변경 없음)
        - source_hash가 다르거나 없으면 changed=True (변경 있음 또는 첫 생성)
        - source_hash를 제외한 모든 속성의 해시를 계산하여 Change 노드에 저장
        
        통합 파이프라인 안정성:
        - 동일한 source_hash로 재업서트 시 불필요한 재생성을 방지
        - source_hash 변경 시에만 재생성 트리거
        
        Args:
            node_label: 노드 레이블
            node_id: 노드 ID
            new_data: 새로운 노드 속성 딕셔너리 (source_hash 포함)
            reason: 변경 이유
        
        Returns:
            {
                "changed": bool,  # True: source_hash 변경 또는 첫 생성, False: source_hash 동일
                "change_id": str (changed=True인 경우), None (changed=False인 경우)
                "before_hash": str (이전 해시, 첫 생성 시 None)
                "after_hash": str (현재 해시, 항상 반환)
            }
        """
        # source_hash를 제외한 모든 속성의 해시 계산 (Change 노드 저장용)
        new_hash = self._generate_hash(new_data)
        new_source_hash = new_data.get("source_hash")
        
        with self.driver.session() as session:
            # 현재 노드의 source_hash 조회
            current_node_query = """
            MATCH (n {id: $node_id})
            RETURN n.source_hash AS source_hash
            """
            result = session.run(current_node_query, node_id=node_id)
            current_node = result.single()
            current_source_hash = current_node["source_hash"] if current_node else None
            
            # 기존 노드의 마지막 Change 노드 찾기 (해시 저장용)
            last_change_query = """
            MATCH (n {id: $node_id})<-[:CHANGED]-(ch:Change)
            RETURN ch.id AS change_id,
                   ch.after_hash AS before_hash
            ORDER BY ch.at DESC
            LIMIT 1
            """
            result = session.run(last_change_query, node_id=node_id)
            last_change = result.single()
            before_hash = last_change["before_hash"] if last_change else None
            
            # source_hash 비교로 변경 여부 결정
            if current_source_hash is not None and new_source_hash is not None:
                # source_hash가 모두 있으면 직접 비교
                changed = (current_source_hash != new_source_hash)
            elif current_source_hash is None and new_source_hash is None:
                # source_hash가 모두 없으면 해시 비교 (하위 호환성)
                changed = (before_hash != new_hash) if before_hash else True
            else:
                # 하나만 있으면 변경된 것으로 간주
                changed = True
            
            if changed:
                # Change 노드 생성 (source_hash가 변경되었거나 첫 생성)
                change_id = f"CHG_{node_label}_{node_id}_{int(datetime.now().timestamp())}"
                
                change_query = """
                MATCH (n {id: $node_id})
                CREATE (ch:Change {
                    id: $change_id,
                    at: datetime(),
                    reason: $reason,
                    label: $node_label,
                    node_id: $node_id,
                    before_hash: $before_hash,
                    after_hash: $after_hash
                })
                CREATE (ch)-[:CHANGED]->(n)
                RETURN ch.id AS change_id
                """
                session.run(
                    change_query,
                    node_id=node_id,
                    change_id=change_id,
                    reason=reason,
                    node_label=node_label,
                    before_hash=before_hash,
                    after_hash=new_hash
                )
                
                return {
                    "changed": True,
                    "change_id": change_id,
                    "before_hash": before_hash,
                    "after_hash": new_hash
                }
            else:
                # source_hash가 동일하므로 변경 없음 (불필요한 재생성 방지)
                return {
                    "changed": False,
                    "change_id": None,
                    "before_hash": before_hash,
                    "after_hash": new_hash
                }
    
    def get_change_history(self, node_id: str, limit: int = 10) -> List[Dict]:
        """
        노드의 변경 이력 조회
        
        Args:
            node_id: 노드 ID
            limit: 조회할 변경 이력 수
        
        Returns:
            Change 노드 정보 리스트
        """
        with self.driver.session() as session:
            query = """
            MATCH (n {id: $node_id})<-[:CHANGED]-(ch:Change)
            RETURN ch.id AS id,
                   ch.at AS at,
                   ch.reason AS reason,
                   ch.before_hash AS before_hash,
                   ch.after_hash AS after_hash
            ORDER BY ch.at DESC
            LIMIT $limit
            """
            result = session.run(query, node_id=node_id, limit=limit)
            return [record.data() for record in result]
    
    def find_changed_nodes(
        self,
        node_label: Optional[str] = None,
        since: Optional[datetime] = None
    ) -> List[Dict]:
        """
        변경된 노드 목록 조회
        
        Args:
            node_label: 특정 레이블만 (None이면 모든 레이블)
            since: 특정 시점 이후 (None이면 전체)
        
        Returns:
            변경된 노드 정보 리스트
        """
        with self.driver.session() as session:
            if node_label and since:
                query = """
                MATCH (ch:Change)-[:CHANGED]->(n)
                WHERE ch.label = $node_label AND ch.at >= $since
                RETURN DISTINCT ch.node_id AS node_id,
                       ch.label AS label,
                       ch.at AS last_changed_at,
                       ch.reason AS reason
                ORDER BY ch.at DESC
                """
                result = session.run(query, node_label=node_label, since=since)
            elif node_label:
                query = """
                MATCH (ch:Change)-[:CHANGED]->(n)
                WHERE ch.label = $node_label
                WITH ch.node_id AS node_id, ch.label AS label, 
                     max(ch.at) AS last_changed_at,
                     collect(ch.reason)[0] AS reason
                RETURN node_id, label, last_changed_at, reason
                ORDER BY last_changed_at DESC
                """
                result = session.run(query, node_label=node_label)
            elif since:
                query = """
                MATCH (ch:Change)-[:CHANGED]->(n)
                WHERE ch.at >= $since
                WITH ch.node_id AS node_id, ch.label AS label,
                     max(ch.at) AS last_changed_at,
                     collect(ch.reason)[0] AS reason
                RETURN node_id, label, last_changed_at, reason
                ORDER BY last_changed_at DESC
                """
                result = session.run(query, since=since)
            else:
                query = """
                MATCH (ch:Change)-[:CHANGED]->(n)
                WITH ch.node_id AS node_id, ch.label AS label,
                     max(ch.at) AS last_changed_at,
                     collect(ch.reason)[0] AS reason
                RETURN node_id, label, last_changed_at, reason
                ORDER BY last_changed_at DESC
                """
                result = session.run(query)
            
            return [record.data() for record in result]

