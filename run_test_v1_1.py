#!/usr/bin/env python3
"""
Zero-base SDD v1.1 — 통합 워크플로우 테스트 실행 스크립트
test-v1.md에 정의된 시나리오를 순서대로 검증
"""

import sys
from pathlib import Path
from neo4j import GraphDatabase
from python.schema_manager import SchemaManager
from python.impact import ImpactAnalyzer
from python.change_detection import ChangeLogger
from python.upsert import UpsertManager
import os


# Neo4j 연결 정보
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "test1234")


def print_section(title):
    """섹션 제목 출력"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def setup_schema_and_data(driver):
    """스키마 초기화 및 테스트 데이터 삽입"""
    print_section("0. 준비: 스키마 초기화 및 테스트 데이터 삽입")
    
    # 1. 스키마 초기화
    schema_mgr = SchemaManager(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    result = schema_mgr.initialize_schema()
    print(f"✓ 스키마 초기화: {result['success_count']}개 성공, {result['error_count']}개 실패")
    if result['error_count'] > 0:
        print("  경고: 일부 제약 조건 생성 실패")
        for error in result['errors']:
            print(f"    - {error}")
    schema_mgr.close()
    
    # 2. 테스트 데이터 삽입
    base_path = Path(__file__).parent
    data_file = base_path / "cypher" / "example-data-v1_1-test.cypher"
    
    if not data_file.exists():
        print(f"✗ 테스트 데이터 파일을 찾을 수 없음: {data_file}")
        return False
    
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
                    print(f"✗ 데이터 삽입 실패: {statement[:50]}... 오류: {e}")
        print(f"✓ 테스트 데이터 삽입: {success_count}개 성공")
    
    return True


def test_1_dirty_standardization(driver):
    """테스트 1: Dirty 표준화"""
    print_section("1. Dirty 표준화 테스트")
    
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
        if not record1:
            print("✗ AGG_ORDER 노드를 찾을 수 없음")
            return False
        assert record1["dirty"] is True
        assert record1["reason"] == "test"
        print(f"✓ AGG_ORDER를 dirty로 마킹: {record1['id']}")
        
        # 2. dirty 조회
        query2 = """
        MATCH (n)
        WHERE n.dirty = true
        RETURN labels(n)[0] AS label, n.id AS id, n.dirty_reason AS reason, n.dirty_at AS at
        ORDER BY at DESC
        """
        result2 = session.run(query2)
        dirty_nodes = [record.data() for record in result2]
        print(f"✓ Dirty 노드 조회: {len(dirty_nodes)}개 발견")
        for node in dirty_nodes[:5]:  # 처음 5개만 출력
            print(f"  - {node.get('label', 'Unknown')}.{node.get('id', 'N/A')}: {node.get('reason', 'N/A')}")
        
        # 3. clear dirty
        query3 = """
        MATCH (n)
        WHERE n.dirty = true
        REMOVE n.dirty, n.dirty_reason, n.dirty_at
        RETURN count(n) AS cleared_count
        """
        result3 = session.run(query3)
        cleared = result3.single()["cleared_count"]
        print(f"✓ Dirty 플래그 제거: {cleared}개")
        
        # is_dirty 같은 속성이 없는지 확인 (keys() 사용하여 경고 방지)
        query4 = """
        MATCH (n)
        WHERE 'is_dirty' IN keys(n)
        RETURN count(n) AS count
        """
        result4 = session.run(query4)
        count = result4.single()["count"]
        if count > 0:
            print(f"✗ is_dirty 속성이 남아있음: {count}개 (표준화 실패)")
            return False
        print(f"✓ is_dirty 속성 없음 확인")
    
    return True


def test_2_impact_expansion_es_chain(driver):
    """테스트 2: Impact 확장 (ES chain 포함)"""
    print_section("2. Impact 확장 (ES chain 포함) 테스트")
    
    # 디버깅: 관계 확인
    with driver.session() as session:
        # IMPACTS_AGGREGATE 관계 확인
        imp_agg = session.run(
            "MATCH (us:UserStory {id: 'US_001'})-[r:IMPACTS_AGGREGATE]->(agg:Aggregate) RETURN count(r) AS count"
        ).single()["count"]
        print(f"  [디버깅] IMPACTS_AGGREGATE 관계 수: {imp_agg}")
        
        # COVERS_COMMAND 관계 확인
        cov_cmd = session.run(
            "MATCH (ac:AcceptanceCriterion {id: 'AC_001'})-[r:COVERS_COMMAND]->(cmd:Command) RETURN count(r) AS count"
        ).single()["count"]
        print(f"  [디버깅] COVERS_COMMAND 관계 수: {cov_cmd}")
    
    analyzer = ImpactAnalyzer(driver)
    result = analyzer.find_full_impact_by_story("US_001", max_hops=3)
    
    # v1.1 표준 형식 확인
    assert "root" in result
    assert result["root"]["label"] == "UserStory"
    assert result["root"]["id"] == "US_001"
    
    assert "impacted" in result
    impacted = result["impacted"]
    
    # 기대 결과 확인
    expected_results = {
        "Aggregate": ("AGG_ORDER", impacted.get("Aggregate", [])),
        "Field": ("F_ORDER_AMOUNT", impacted.get("Field", [])),
        "Command": ("CMD_PLACE_ORDER", impacted.get("Command", [])),
        "Event": ("EVT_ORDER_PLACED", impacted.get("Event", [])),
        "Policy": ("POL_RESERVE_STOCK", impacted.get("Policy", [])),
    }
    
    all_passed = True
    print(f"✓ Impact 결과 (v1.1 표준 형식):")
    print(f"  - Root: {result['root']['label']}.{result['root']['id']}")
    print(f"\n  Impacted 노드:")
    
    for label, (expected_id, actual_list) in expected_results.items():
        if expected_id in actual_list:
            print(f"    ✓ {label}: {expected_id} 포함")
        else:
            print(f"    ✗ {label}: {expected_id} 누락 (현재: {actual_list})")
            all_passed = False
    
    affected = result.get("affected_aggregates", [])
    if "AGG_STOCK" in affected:
        print(f"    ✓ Affected Aggregates: AGG_STOCK 포함")
    else:
        print(f"    ✗ Affected Aggregates: AGG_STOCK 누락 (현재: {affected})")
        all_passed = False
    
    return all_passed


def test_3_regeneration_scope_and_dirty_marking(driver):
    """테스트 3: Selective Regeneration scope 산출 + Dirty 마킹"""
    print_section("3. Regeneration Scope + Dirty 마킹 테스트")
    
    analyzer = ImpactAnalyzer(driver)
    
    # 1. calculate_regeneration_scope 실행
    result = analyzer.calculate_regeneration_scope("US_001", max_hops=3)
    
    print(f"✓ 재생성 범위 산출:")
    print(f"  - Story ID: {result['story_id']}")
    print(f"  - 총 노드 수: {result['total_nodes']}")
    print(f"  - Dirty 마킹: {result['dirty_marked']['marked']}개 성공")
    
    # 2. dirty 노드 목록 조회
    dirty_nodes = analyzer.get_dirty_nodes()
    
    expected_ids = [
        "AGG_ORDER",
        "F_ORDER_AMOUNT",
        "CMD_PLACE_ORDER",
        "EVT_ORDER_PLACED",
        "POL_RESERVE_STOCK",
        "AGG_STOCK"
    ]
    
    dirty_ids = [node["id"] for node in dirty_nodes]
    print(f"\n✓ Dirty 노드 목록: {len(dirty_ids)}개")
    print(f"  {dirty_ids}")
    
    all_passed = True
    for expected_id in expected_ids:
        if expected_id in dirty_ids:
            print(f"  ✓ {expected_id} dirty 마킹됨")
        else:
            print(f"  ✗ {expected_id} dirty 마킹 누락")
            all_passed = False
    
    # Cleanup
    analyzer.clear_dirty()
    print(f"\n✓ Dirty 플래그 정리 완료")
    
    return all_passed


def test_4_change_detection(driver):
    """테스트 4: Change 감지"""
    print_section("4. Change 감지 테스트")
    
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
        record = result.single()
        if not record:
            print("✗ US_001 노드를 찾을 수 없음")
            return False
        current_data = dict(record)
    
    # 2. 동일한 source_hash로 변경 감지 (변경 없음)
    detection1 = change_logger.detect_change(
        node_label="UserStory",
        node_id="US_001",
        new_data=current_data,
        reason="test_no_change"
    )
    
    print(f"✓ 동일 source_hash 재업서트: changed={detection1['changed']}")
    if detection1["changed"]:
        print(f"  ⚠️  source_hash가 동일한데 changed=True (의도 확인 필요)")
    else:
        print(f"  ✓ source_hash 동일 시 변경 없음 확인 (올바름)")
    
    # 3. title 변경 (아직 노드에 반영하지 않음)
    from python.types import UserStory
    modified_data = current_data.copy()
    modified_data["title"] = "고객이 주문을 생성한다 (수정됨)"
    modified_data["source_hash"] = "H2_MODIFIED"  # 새로운 source_hash
    
    # 4. 변경 감지 (source_hash가 변경되었으므로 changed=True여야 함)
    # 주의: 아직 노드에 반영하지 않았으므로, 노드의 source_hash는 "H2", new_data의 source_hash는 "H2_MODIFIED"
    detection2 = change_logger.detect_change(
        node_label="UserStory",
        node_id="US_001",
        new_data=modified_data,
        reason="test_hash_changed"
    )
    
    if not detection2["changed"]:
        print(f"✗ source_hash 변경 시 변경 감지 실패")
        print(f"  - 노드의 source_hash: H2 (예상)")
        print(f"  - new_data의 source_hash: H2_MODIFIED")
        return False
    
    # 5. 노드 업데이트 (변경 감지 후 실제 반영)
    story = UserStory(
        id=modified_data["id"],
        title=modified_data["title"],
        storyText=modified_data.get("storyText", ""),
        priority=modified_data.get("priority", "medium"),
        status=modified_data.get("status", "draft"),
        asIs=modified_data.get("asIs"),
        toBe=modified_data.get("toBe"),
        semantic_text=modified_data.get("semantic_text"),
        keywords=modified_data.get("keywords", [])
    )
    upsert_manager.upsert_user_story(story)
    # source_hash는 별도로 업데이트
    with driver.session() as session:
        session.run(
            "MATCH (us:UserStory {id: $id}) SET us.source_hash = $hash",
            id=modified_data["id"],
            hash="H2_MODIFIED"
        )
    
    print(f"✓ source_hash 변경 감지:")
    print(f"  - Change ID: {detection2['change_id']}")
    print(f"  - Before Hash: {detection2['before_hash']}")
    print(f"  - After Hash: {detection2['after_hash']}")
    
    # 6. 변경 이력 조회
    history = change_logger.get_change_history("US_001", limit=10)
    print(f"\n✓ 변경 이력 조회: {len(history)}개")
    for i, change in enumerate(history[:3], 1):
        print(f"  {i}. {change.get('reason', 'N/A')} at {change.get('at', 'N/A')}")
    
    return True


def main():
    """메인 실행 함수"""
    print("\n" + "=" * 60)
    print("  Zero-base SDD v1.1 — 통합 워크플로우 테스트")
    print("=" * 60)
    
    # Neo4j 연결
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        print(f"\n✓ Neo4j 연결 성공: {NEO4J_URI}")
    except Exception as e:
        print(f"\n✗ Neo4j 연결 실패: {e}")
        print(f"  URI: {NEO4J_URI}")
        print(f"  USER: {NEO4J_USER}")
        sys.exit(1)
    
    try:
        # 준비
        if not setup_schema_and_data(driver):
            print("\n✗ 준비 단계 실패")
            sys.exit(1)
        
        # 테스트 실행
        results = []
        results.append(("Dirty 표준화", test_1_dirty_standardization(driver)))
        results.append(("Impact 확장", test_2_impact_expansion_es_chain(driver)))
        results.append(("Regeneration Scope + Dirty 마킹", test_3_regeneration_scope_and_dirty_marking(driver)))
        results.append(("Change 감지", test_4_change_detection(driver)))
        
        # 결과 요약
        print_section("테스트 결과 요약")
        all_passed = True
        for name, passed in results:
            status = "✓ 통과" if passed else "✗ 실패"
            print(f"  {status}: {name}")
            if not passed:
                all_passed = False
        
        if all_passed:
            print("\n🎉 모든 테스트 통과!")
            sys.exit(0)
        else:
            print("\n⚠️  일부 테스트 실패")
            sys.exit(1)
    
    except Exception as e:
        print(f"\n✗ 테스트 실행 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    finally:
        driver.close()


if __name__ == "__main__":
    main()

