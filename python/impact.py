"""
Zero-base SDD v1 — Impact 탐색 및 Change 감지
부분 수정(Selective Regeneration)을 위한 영향 범위 탐색 및 Dirty Marking
"""

from typing import List, Dict, Set, Optional
from datetime import datetime
from neo4j import GraphDatabase


class ImpactAnalyzer:
    """영향 범위 분석기"""
    
    def __init__(self, driver):
        """
        Args:
            driver: Neo4j 드라이버 인스턴스
        """
        self.driver = driver
    
    # ==========================================
    # Impact 탐색 쿼리
    # ==========================================
    
    def find_impacted_aggregates_by_story(self, story_id: str) -> List[Dict]:
        """
        UserStory 변경 시 영향받는 Aggregate 찾기
        
        Returns:
            Aggregate 노드 정보 리스트
        """
        with self.driver.session() as session:
            query = """
            MATCH (us:UserStory {id: $story_id})-[:IMPACTS_AGGREGATE]->(agg:Aggregate)
            RETURN agg.id AS id,
                   agg.name AS name,
                   agg.status AS status,
                   agg.version AS version
            ORDER BY agg.name
            """
            result = session.run(query, story_id=story_id)
            return [record.data() for record in result]
    
    def find_impacted_fields_by_criterion(self, ac_id: str) -> List[Dict]:
        """
        AcceptanceCriterion 변경 시 영향받는 Field 찾기
        
        Returns:
            Field 노드 정보 리스트
        """
        with self.driver.session() as session:
            query = """
            MATCH (ac:AcceptanceCriterion {id: $ac_id})-[:IMPACTS_FIELD]->(f:Field)
            RETURN f.id AS id,
                   f.name AS name,
                   f.type AS type,
                   f.description AS description
            ORDER BY f.name
            """
            result = session.run(query, ac_id=ac_id)
            return [record.data() for record in result]
    
    def find_impacted_behavior_by_criterion(
        self,
        ac_id: str,
        include_commands: bool = True,
        include_events: bool = True
    ) -> Dict:
        """
        AcceptanceCriterion 변경 시 영향받는 Command/Event 찾기
        
        Args:
            ac_id: AcceptanceCriterion ID
            include_commands: Command 포함 여부
            include_events: Event 포함 여부
        
        Returns:
            commands, events 리스트를 포함한 딕셔너리
        """
        commands = []
        events = []
        
        with self.driver.session() as session:
            if include_commands:
                cmd_query = """
                MATCH (ac:AcceptanceCriterion {id: $ac_id})-[:COVERS_COMMAND]->(cmd:Command)
                RETURN cmd.id AS id,
                       cmd.name AS name,
                       cmd.description AS description
                ORDER BY cmd.name
                """
                result = session.run(cmd_query, ac_id=ac_id)
                commands = [record.data() for record in result]
            
            if include_events:
                evt_query = """
                MATCH (ac:AcceptanceCriterion {id: $ac_id})-[:COVERS_EVENT]->(evt:Event)
                RETURN evt.id AS id,
                       evt.name AS name,
                       evt.category AS category,
                       evt.description AS description
                ORDER BY evt.name
                """
                result = session.run(evt_query, ac_id=ac_id)
                events = [record.data() for record in result]
        
        return {
            "commands": commands,
            "events": events
        }
    
    def find_full_impact_by_story(self, story_id: str, max_hops: int = 3) -> Dict:
        """
        UserStory 변경 시 전체 영향 범위 탐색 (v1.1 표준화)
        Story → AC → Aggregate/Field/Command/Event/Policy + ES 체인 확장
        
        Args:
            story_id: UserStory ID
            max_hops: ES 체인 탐색 최대 hop 수 (기본 3)
        
        Returns:
            표준 출력 형식:
            {
                "root": {"label": "UserStory", "id": "US_001"},
                "impacted": {
                    "Aggregate": ["AGG_ORDER"],
                    "Field": ["F_ORDER_AMOUNT"],
                    "Command": ["CMD_PLACE_ORDER"],
                    "Event": ["EVT_ORDER_PLACED"],
                    "Policy": ["POL_RESERVE_STOCK"]
                },
                "affected_aggregates": ["AGG_STOCK"]
            }
        """
        with self.driver.session() as session:
            # 1. Story에서 직접 영향받는 Aggregate
            agg_query = """
            MATCH (us:UserStory {id: $story_id})-[:IMPACTS_AGGREGATE]->(agg:Aggregate)
            RETURN DISTINCT agg.id AS id
            """
            agg_result = session.run(agg_query, story_id=story_id)
            direct_aggregates = [record["id"] for record in agg_result]
            
            # 2. Story의 AC들이 영향받는 Field/Command/Event
            ac_query = """
            MATCH (us:UserStory {id: $story_id})-[:HAS_CRITERION]->(ac:AcceptanceCriterion)
            OPTIONAL MATCH (ac)-[:IMPACTS_FIELD]->(f:Field)
            OPTIONAL MATCH (ac)-[:COVERS_COMMAND]->(cmd:Command)
            OPTIONAL MATCH (ac)-[:COVERS_EVENT]->(evt:Event)
            RETURN DISTINCT 
                collect(DISTINCT f.id) AS field_ids,
                collect(DISTINCT cmd.id) AS command_ids,
                collect(DISTINCT evt.id) AS event_ids
            """
            ac_result = session.run(ac_query, story_id=story_id)
            single_record = ac_result.single()
            ac_data = single_record.data() if single_record else {}
            
            fields = [fid for fid in (ac_data.get("field_ids") or []) if fid]
            commands = [cid for cid in (ac_data.get("command_ids") or []) if cid]
            events = [eid for eid in (ac_data.get("event_ids") or []) if eid]
            
            # 3. ES 체인 확장: Command → Event → Policy → Command
            policies = set()
            affected_aggregates = set(direct_aggregates)
            # Command에서 시작하는 ES 체인 탐색 (최대 max_hops 깊이)
            visited_commands = set(commands)
            visited_events = set(events)
            current_commands = list(commands)
            
            # 초기에 이미 있는 events에서 Policy 찾기
            for evt_id in events:
                policy_query = """
                MATCH (evt:Event {id: $evt_id})<-[:LISTENS_EVENT]-(pol:Policy)
                RETURN DISTINCT pol.id AS id
                """
                policy_result = session.run(policy_query, evt_id=evt_id)
                for pol_record in policy_result:
                    pol_id = pol_record["id"]
                    policies.add(pol_id)
            
            for hop in range(max_hops):
                if not current_commands:
                    break
                
                next_commands = []
                # 현재 Command들이 emit하는 Event 찾기
                for cmd_id in current_commands:
                    emits_query = """
                    MATCH (cmd:Command {id: $cmd_id})-[:EMITS_EVENT]->(evt:Event)
                    RETURN DISTINCT evt.id AS id
                    """
                    emits_result = session.run(emits_query, cmd_id=cmd_id)
                    for record in emits_result:
                        evt_id = record["id"]
                        is_new_event = evt_id not in visited_events
                        if is_new_event:
                            visited_events.add(evt_id)
                            events.append(evt_id)
                        
                        # Event를 듣는 Policy 찾기 (새로운 Event든 기존 Event든 모두 확인)
                        policy_query = """
                        MATCH (evt:Event {id: $evt_id})<-[:LISTENS_EVENT]-(pol:Policy)
                        RETURN DISTINCT pol.id AS id
                        """
                        policy_result = session.run(policy_query, evt_id=evt_id)
                        for pol_record in policy_result:
                            pol_id = pol_record["id"]
                            policies.add(pol_id)
                            
                            # Policy가 trigger하는 Command 찾기
                            triggers_query = """
                            MATCH (pol:Policy {id: $pol_id})-[:TRIGGERS_COMMAND]->(nextCmd:Command)
                            RETURN DISTINCT nextCmd.id AS id
                            """
                            triggers_result = session.run(triggers_query, pol_id=pol_id)
                            for cmd_record in triggers_result:
                                next_cmd_id = cmd_record["id"]
                                if next_cmd_id not in visited_commands:
                                    visited_commands.add(next_cmd_id)
                                    commands.append(next_cmd_id)
                                    next_commands.append(next_cmd_id)
                
                current_commands = next_commands
            
            # Event에서 시작하는 AFFECTS_AGGREGATE 탐색
            for evt_id in events:
                affects_query = """
                MATCH (evt:Event {id: $evt_id})-[:AFFECTS_AGGREGATE]->(agg:Aggregate)
                RETURN DISTINCT agg.id AS id
                """
                affects_result = session.run(affects_query, evt_id=evt_id)
                for record in affects_result:
                    affected_aggregates.add(record["id"])
            
            # 중복 제거
            aggregates = list(set(direct_aggregates))
            fields = list(set(fields))
            commands = list(set(commands))
            events = list(set(events))
            policies = list(policies)
            affected_aggregates = list(affected_aggregates)
        
        return {
            "root": {"label": "UserStory", "id": story_id},
            "impacted": {
                "Aggregate": aggregates,
                "Field": fields,
                "Command": commands,
                "Event": events,
                "Policy": policies
            },
            "affected_aggregates": affected_aggregates
        }
    
    def find_es_chain_by_command(self, cmd_id: str, max_hops: int = 3) -> Dict:
        """
        Command → Event → Policy → Command 체인 탐색
        
        Args:
            cmd_id: 시작 Command ID
            max_hops: 최대 탐색 깊이
        
        Returns:
            ES 체인 정보
        """
        with self.driver.session() as session:
            query = """
            MATCH path = (start:Command {id: $cmd_id})
            -[:EMITS_EVENT*0..1]->(evt:Event)
            <-[:LISTENS_EVENT]-(pol:Policy)
            -[:TRIGGERS_COMMAND]->(nextCmd:Command)
            WHERE length(path) <= $max_hops
            RETURN DISTINCT
                start.id AS start_command_id,
                start.name AS start_command_name,
                evt.id AS event_id,
                evt.name AS event_name,
                pol.id AS policy_id,
                pol.name AS policy_name,
                nextCmd.id AS next_command_id,
                nextCmd.name AS next_command_name
            LIMIT 50
            """
            result = session.run(query, cmd_id=cmd_id, max_hops=max_hops * 2)
            chains = [record.data() for record in result]
            
            return {
                "start_command": cmd_id,
                "chains": chains,
                "total_chains": len(chains)
            }
    
    # ==========================================
    # Change 감지 및 Dirty Marking
    # ==========================================
    
    def detect_source_hash_changes(self, node_label: str) -> List[Dict]:
        """
        source_hash가 변경된 노드 찾기 (현재 버전과 비교 필요 시 사용)
        
        Args:
            node_label: 노드 레이블 (예: "Aggregate", "Field")
        
        Returns:
            변경된 노드 정보 리스트
        """
        # 실제 구현에서는 이전 버전과 비교하거나 Change 로그를 확인
        # v1에서는 간단히 source_hash가 있는 노드를 반환
        with self.driver.session() as session:
            query = f"""
            MATCH (n:{node_label})
            WHERE n.source_hash IS NOT NULL
            RETURN n.id AS id,
                   n.name AS name,
                   n.source_hash AS source_hash,
                   n.version AS version
            ORDER BY n.name
            """
            result = session.run(query)
            return [record.data() for record in result]
    
    def mark_dirty(self, node_ids: List[str], node_label: str = "", reason: str = "") -> Dict:
        """
        노드에 dirty 플래그 표시 (재생성 필요 표시)
        
        Args:
            node_ids: dirty로 표시할 노드 ID 리스트
            node_label: 노드 레이블 (빈 문자열이면 모든 레이블 검색)
            reason: dirty 표시 이유
        
        Returns:
            표시 결과 통계
        """
        marked_count = 0
        failed = []
        
        with self.driver.session() as session:
            for node_id in node_ids:
                try:
                    if node_label:
                        query = f"""
                        MATCH (n:{node_label} {{id: $node_id}})
                        SET n.dirty = true,
                            n.dirty_reason = $reason,
                            n.dirty_at = datetime()
                        RETURN n.id AS id
                        """
                    else:
                        # 모든 레이블에서 검색 (id 전역 유니크 가정)
                        query = """
                        MATCH (n {id: $node_id})
                        SET n.dirty = true,
                            n.dirty_reason = $reason,
                            n.dirty_at = datetime()
                        RETURN n.id AS id
                        """
                    result = session.run(query, node_id=node_id, reason=reason)
                    if result.single():
                        marked_count += 1
                    else:
                        failed.append(node_id)
                except Exception as e:
                    failed.append((node_id, str(e)))
        
        return {
            "total": len(node_ids),
            "marked": marked_count,
            "failed": failed
        }
    
    def clear_dirty(self, node_ids: Optional[List[str]] = None, node_label: Optional[str] = None) -> Dict:
        """
        노드의 dirty 플래그 제거
        
        Args:
            node_ids: 특정 노드 ID 리스트 (None이면 모든 노드)
            node_label: 특정 레이블만 (None이면 모든 레이블)
        
        Returns:
            제거 결과 통계
        """
        with self.driver.session() as session:
            if node_ids and node_label:
                # 특정 노드들만
                query = f"""
                MATCH (n:{node_label})
                WHERE n.id IN $node_ids AND n.dirty = true
                REMOVE n.dirty, n.dirty_reason, n.dirty_at
                RETURN count(n) AS cleared_count
                """
                result = session.run(query, node_ids=node_ids)
            elif node_label:
                # 특정 레이블 모두
                query = f"""
                MATCH (n:{node_label})
                WHERE n.dirty = true
                REMOVE n.dirty, n.dirty_reason, n.dirty_at
                RETURN count(n) AS cleared_count
                """
                result = session.run(query)
            else:
                # 모든 노드
                query = """
                MATCH (n)
                WHERE n.dirty = true
                REMOVE n.dirty, n.dirty_reason, n.dirty_at
                RETURN count(n) AS cleared_count
                """
                result = session.run(query)
            
            single_record = result.single()
            cleared = single_record["cleared_count"] if single_record else 0
            
            return {
                "cleared_count": cleared
            }
    
    def get_dirty_nodes(self, node_label: Optional[str] = None) -> List[Dict]:
        """
        dirty로 표시된 노드 목록 조회
        
        Args:
            node_label: 특정 레이블만 (None이면 모든 레이블)
        
        Returns:
            dirty 노드 정보 리스트
        """
        with self.driver.session() as session:
            if node_label:
                query = f"""
                MATCH (n:{node_label})
                WHERE n.dirty = true
                RETURN labels(n)[0] AS label,
                       n.id AS id,
                       n.name AS name,
                       n.dirty_reason AS reason,
                       n.dirty_at AS dirty_at
                ORDER BY n.dirty_at DESC
                """
            else:
                query = """
                MATCH (n)
                WHERE n.dirty = true
                RETURN labels(n)[0] AS label,
                       n.id AS id,
                       n.name AS name,
                       n.dirty_reason AS reason,
                       n.dirty_at AS dirty_at
                ORDER BY n.dirty_at DESC
                """
            result = session.run(query)
            return [record.data() for record in result]
    
    # ==========================================
    # 부분 수정 범위 산출
    # ==========================================
    
    def calculate_regeneration_scope(self, story_id: str, max_hops: int = 3) -> Dict:
        """
        UserStory 변경 시 재생성 필요 범위 산출 (v1.1 표준화)
        
        Args:
            story_id: UserStory ID
            max_hops: ES 체인 탐색 최대 hop 수
        
        Returns:
            재생성 대상 노드 ID 리스트 및 Dirty 마킹 결과
        """
        impact = self.find_full_impact_by_story(story_id, max_hops=max_hops)
        
        # 영향받는 모든 노드 ID 수집 (v1.1 표준 형식)
        impacted = impact.get("impacted", {})
        all_node_ids = {
            "aggregates": impacted.get("Aggregate", []),
            "fields": impacted.get("Field", []),
            "commands": impacted.get("Command", []),
            "events": impacted.get("Event", []),
            "policies": impacted.get("Policy", []),
        }
        affected_aggregates = impact.get("affected_aggregates", [])
        
        # 모든 영향받는 노드 ID 수집 (id 전역 유니크 가정)
        all_ids = (
            all_node_ids["aggregates"] +
            all_node_ids["fields"] +
            all_node_ids["commands"] +
            all_node_ids["events"] +
            all_node_ids["policies"] +
            affected_aggregates
        )
        
        # 중복 제거
        all_ids = list(set(all_ids))
        
        # Dirty 마킹 (id 전역 유니크 가정)
        mark_result = self.mark_dirty(
            all_ids,
            node_label="",  # id 전역 유니크 가정으로 라벨 생략 가능
            reason=f"Impacted by story change: {story_id}"
        )
        
        return {
            "story_id": story_id,
            "impact": impact,
            "impacted_nodes": all_node_ids,
            "affected_aggregates": affected_aggregates,
            "total_nodes": len(all_ids),
            "dirty_marked": mark_result
        }

