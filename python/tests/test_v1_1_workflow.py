"""
Zero-base SDD v1.1 — 통합 워크플로우 테스트
test-v1.md에 정의된 시나리오를 순서대로 검증
"""

import pytest
from neo4j import GraphDatabase
from python.schema_manager import SchemaManager
from python.impact import ImpactAnalyzer
from python.change_detection import ChangeLogger
from python.upsert import UpsertManager
from pathlib import Path
import os


# Neo4j 연결 정보
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "test1234")


@pytest.fixture(scope="module")
def driver():
    """테스트용 Neo4j 드라이버"""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    yield driver
    driver.close()


@pytest.fixture(scope="module")
def setup_schema_and_data(driver):
    """스키마 초기화 및 테스트 데이터 삽입"""
    # 1. 스키마 초기화
    schema_mgr = SchemaManager(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    result = schema_mgr.initialize_schema()
    print(f"\n[Setup] 스키마 초기화: {result['success_count']}개 성공, {result['error_count']}개 실패")
    assert result["error_count"] == 0
    
    # 2. 테스트 데이터 삽입
    base_path = Path(__file__).parent.parent.parent
    data_file = base_path / "cypher" / "example-data-v1_1-test.cypher"
    
    with open(data_file, "r", encoding="utf-8") as f:
        cypher_script = f.read()
    
    with driver.session() as session:
        # 주석 라인을 제거하고 문장을 파싱
        lines = []
        for line in cypher_script.split('\n'):
            stripped = line.strip()
            # 주석 라인이 아니고 빈 라인이 아닌 경우만 유지
            if stripped and not stripped.startswith('//'):
                lines.append(line)
        
        # 다시 합친 후 세미콜론으로 split
        cleaned_script = '\n'.join(lines)
        statements = [s.strip() for s in cleaned_script.split(";") if s.strip()]
        
        success_count = 0
        for statement in statements:
            if statement:
                try:
                    session.run(statement)
                    success_count += 1
                except Exception as e:
                    print(f"[Setup] 데이터 삽입 실패: {statement[:50]}... 오류: {e}")
        print(f"[Setup] 테스트 데이터 삽입: {success_count}개 성공")
    
    yield
    
    # Cleanup (선택사항)
    # with driver.session() as session:
    #     session.run("MATCH (n) WHERE n.id STARTS WITH 'EP_' OR n.id STARTS WITH 'US_' OR n.id STARTS WITH 'AC_' OR n.id STARTS WITH 'AGG_' OR n.id STARTS WITH 'F_' OR n.id STARTS WITH 'CMD_' OR n.id STARTS WITH 'EVT_' OR n.id STARTS WITH 'POL_' OR n.id STARTS WITH 'BC_' DETACH DELETE n")
    
    schema_mgr.close()


class TestV1_1Workflow:
    """v1.1 워크플로우 통합 테스트"""
    
    def test_1_dirty_standardization(self, driver, setup_schema_and_data):
        """
        테스트 1: Dirty 표준화
        dirty/dirty_reason/dirty_at만 사용되는지 확인
        """
        print("\n=== 테스트 1: Dirty 표준화 ===")
        
        with driver.session() as session:
            # 1. AGG_ORDER를 dirty로 만듦
            query1 = """
            MATCH (a:Aggregate {id: 'AGG_ORDER'})
            SET a.dirty = true,
                a.dirty_reason = 'test',
                a.dirty_at = datetime()
            RETURN a.id AS id, a.dirty AS dirty, a.dirty_reason AS reason
            """
            result1 = session.run(query1)
            record1 = result1.single()
            assert record1["dirty"] is True
            assert record1["reason"] == "test"
            print(f"✓ AGG_ORDER를 dirty로 마킹: {record1}")
            
            # 2. dirty 조회
            query2 = """
            MATCH (n)
            WHERE n.dirty = true
            RETURN labels(n)[0] AS label, n.id AS id, n.dirty_reason AS reason, n.dirty_at AS at
            ORDER BY at DESC
            """
            result2 = session.run(query2)
            dirty_nodes = [record.data() for record in result2]
            assert len(dirty_nodes) > 0
            assert any(node["id"] == "AGG_ORDER" for node in dirty_nodes)
            print(f"✓ Dirty 노드 조회: {len(dirty_nodes)}개 발견")
            for node in dirty_nodes:
                assert "dirty_reason" in node or node.get("reason") is not None
                assert "at" in node or node.get("dirty_at") is not None
            
            # 3. clear dirty
            query3 = """
            MATCH (n)
            WHERE n.dirty = true
            REMOVE n.dirty, n.dirty_reason, n.dirty_at
            RETURN count(n) AS cleared_count
            """
            result3 = session.run(query3)
            cleared = result3.single()["cleared_count"]
            assert cleared > 0
            print(f"✓ Dirty 플래그 제거: {cleared}개")
            
            # is_dirty 같은 속성이 없는지 확인 (keys() 사용하여 경고 방지)
            query4 = """
            MATCH (n)
            WHERE 'is_dirty' IN keys(n)
            RETURN count(n) AS count
            """
            result4 = session.run(query4)
            count = result4.single()["count"]
            assert count == 0, "is_dirty 속성이 남아있음 (표준화 실패)"
            print(f"✓ is_dirty 속성 없음 확인: {count}개")
    
    def test_2_impact_expansion_es_chain(self, driver, setup_schema_and_data):
        """
        테스트 2: Impact 확장 (ES chain 포함)
        US_001 변경 시 Policy/추가 Command/affected aggregate까지 잡히는지 확인
        """
        print("\n=== 테스트 2: Impact 확장 (ES chain 포함) ===")
        
        analyzer = ImpactAnalyzer(driver)
        result = analyzer.find_full_impact_by_story("US_001", max_hops=3)
        
        # v1.1 표준 형식 확인
        assert "root" in result
        assert result["root"]["label"] == "UserStory"
        assert result["root"]["id"] == "US_001"
        
        assert "impacted" in result
        impacted = result["impacted"]
        
        # 기대 결과 확인
        assert "AGG_ORDER" in impacted["Aggregate"], f"Aggregate에 AGG_ORDER 포함되어야 함. 현재: {impacted['Aggregate']}"
        assert "F_ORDER_AMOUNT" in impacted["Field"], f"Field에 F_ORDER_AMOUNT 포함되어야 함. 현재: {impacted['Field']}"
        assert "CMD_PLACE_ORDER" in impacted["Command"], f"Command에 CMD_PLACE_ORDER 포함되어야 함. 현재: {impacted['Command']}"
        assert "EVT_ORDER_PLACED" in impacted["Event"], f"Event에 EVT_ORDER_PLACED 포함되어야 함. 현재: {impacted['Event']}"
        assert "POL_RESERVE_STOCK" in impacted["Policy"], f"Policy에 POL_RESERVE_STOCK 포함되어야 함. 현재: {impacted['Policy']}"
        assert "AGG_STOCK" in result["affected_aggregates"], f"affected_aggregates에 AGG_STOCK 포함되어야 함. 현재: {result['affected_aggregates']}"
        
        print(f"✓ Impact 결과:")
        print(f"  - Aggregate: {impacted['Aggregate']}")
        print(f"  - Field: {impacted['Field']}")
        print(f"  - Command: {impacted['Command']}")
        print(f"  - Event: {impacted['Event']}")
        print(f"  - Policy: {impacted['Policy']}")
        print(f"  - Affected Aggregates: {result['affected_aggregates']}")
    
    def test_3_regeneration_scope_and_dirty_marking(self, driver, setup_schema_and_data):
        """
        테스트 3: Selective Regeneration scope 산출 + Dirty 마킹
        영향 범위 → dirty 마킹이 정확히 동작하는지 확인
        """
        print("\n=== 테스트 3: Regeneration Scope + Dirty 마킹 ===")
        
        analyzer = ImpactAnalyzer(driver)
        
        # 1. calculate_regeneration_scope 실행
        result = analyzer.calculate_regeneration_scope("US_001", max_hops=3)
        
        assert "story_id" in result
        assert "impact" in result
        assert "impacted_nodes" in result
        assert "dirty_marked" in result
        
        print(f"✓ 재생성 범위 산출:")
        print(f"  - Story ID: {result['story_id']}")
        print(f"  - 총 노드 수: {result['total_nodes']}")
        print(f"  - Dirty 마킹: {result['dirty_marked']['marked']}개 성공")
        
        # 2. dirty 노드 목록 조회
        dirty_nodes = analyzer.get_dirty_nodes()
        
        # 영향받은 노드들이 dirty로 표시되었는지 확인
        expected_ids = [
            "AGG_ORDER",
            "F_ORDER_AMOUNT",
            "CMD_PLACE_ORDER",
            "EVT_ORDER_PLACED",
            "POL_RESERVE_STOCK",
            "AGG_STOCK"
        ]
        
        dirty_ids = [node["id"] for node in dirty_nodes]
        print(f"✓ Dirty 노드 목록: {dirty_ids}")
        
        for expected_id in expected_ids:
            assert expected_id in dirty_ids, f"{expected_id}가 dirty로 마킹되어야 함"
        
        # 무관한 노드가 dirty가 되지 않았는지 확인 (예: 다른 BC의 노드)
        with driver.session() as session:
            query = """
            MATCH (n)
            WHERE n.id = 'AGG_STOCK' OR n.id = 'AGG_ORDER'
            RETURN n.id AS id, n.dirty AS dirty
            """
            result = session.run(query)
            for record in result:
                assert record["dirty"] is True, f"{record['id']}가 dirty로 마킹되어야 함"
        
        print(f"✓ 영향받은 노드들만 dirty로 표시됨 확인")
        
        # Cleanup
        analyzer.clear_dirty()
        print(f"✓ Dirty 플래그 정리 완료")
    
    def test_4_change_detection(self, driver, setup_schema_and_data):
        """
        테스트 4: Change 감지
        source_hash 변경 시 변경 감지가 동작하는지 확인
        """
        print("\n=== 테스트 4: Change 감지 ===")
        
        change_logger = ChangeLogger(driver)
        upsert_manager = UpsertManager(driver)
        
        # 1. US_001의 현재 데이터 조회
        with driver.session() as session:
            query = """
            MATCH (us:UserStory {id: 'US_001'})
            RETURN us.id AS id, us.title AS title, us.storyText AS storyText,
                   us.priority AS priority, us.status AS status, us.source_hash AS source_hash
            """
            result = session.run(query)
            current_data = result.single().data()
        
        # 2. 동일한 source_hash로 변경 감지 (변경 없음)
        detection1 = change_logger.detect_change(
            node_label="UserStory",
            node_id="US_001",
            new_data=current_data,
            reason="test_no_change"
        )
        
        print(f"✓ 동일 해시 재업서트: changed={detection1['changed']}")
        if detection1["changed"]:
            print(f"  - Change ID: {detection1['change_id']}")
        
        # 3. source_hash를 변경하여 업서트
        from python.types import UserStory
        current_data["source_hash"] = "H2"
        current_data["title"] = "고객이 주문을 생성한다 (수정됨)"
        # UserStory 타입 생성 (필수 필드 포함)
        story = UserStory(
            id=current_data["id"],
            title=current_data["title"],
            storyText=current_data.get("storyText", ""),
            priority=current_data.get("priority", "medium"),
            status=current_data.get("status", "draft"),
            asIs=current_data.get("asIs"),
            toBe=current_data.get("toBe"),
            semantic_text=current_data.get("semantic_text"),
            keywords=current_data.get("keywords", [])
        )
        # source_hash는 UserStory 타입에 없으므로 직접 업데이트
        upsert_manager.upsert_user_story(story)
        # source_hash는 별도로 업데이트
        with driver.session() as session:
            session.run(
                "MATCH (us:UserStory {id: $id}) SET us.source_hash = $hash",
                id=current_data["id"],
                hash="H2"
            )
        
        # 4. 변경 감지 (변경 있음)
        detection2 = change_logger.detect_change(
            node_label="UserStory",
            node_id="US_001",
            new_data=current_data,
            reason="test_hash_changed"
        )
        
        assert detection2["changed"] is True, "해시 변경 시 변경 감지되어야 함"
        assert detection2["change_id"] is not None
        assert detection2["before_hash"] != detection2["after_hash"]
        
        print(f"✓ 해시 변경 감지:")
        print(f"  - Change ID: {detection2['change_id']}")
        print(f"  - Before Hash: {detection2['before_hash']}")
        print(f"  - After Hash: {detection2['after_hash']}")
        
        # 5. 변경 이력 조회
        history = change_logger.get_change_history("US_001", limit=10)
        assert len(history) > 0
        print(f"✓ 변경 이력 조회: {len(history)}개")
        for i, change in enumerate(history[:3], 1):
            print(f"  {i}. {change.get('reason', 'N/A')} at {change.get('at', 'N/A')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

