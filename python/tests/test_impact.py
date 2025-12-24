"""
Zero-base SDD v1 — Impact 분석 테스트
"""

import pytest
from neo4j import GraphDatabase
from python.impact import ImpactAnalyzer
from python.upsert import UpsertManager
from python.traceability import TraceabilityManager
from python.types import (
    Epic,
    UserStory,
    AcceptanceCriterion,
    Aggregate,
    Field,
    Command,
    Event,
    Policy,
)


@pytest.fixture(scope="module")
def driver():
    """테스트용 Neo4j 드라이버"""
    import os
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")
    
    driver = GraphDatabase.driver(uri, auth=(user, password))
    yield driver
    driver.close()


@pytest.fixture
def impact_analyzer(driver):
    """ImpactAnalyzer 인스턴스"""
    return ImpactAnalyzer(driver)


@pytest.fixture
def upsert_manager(driver):
    """UpsertManager 인스턴스"""
    return UpsertManager(driver)


@pytest.fixture
def trace_manager(driver):
    """TraceabilityManager 인스턴스"""
    return TraceabilityManager(driver)


@pytest.fixture
def sample_data(upsert_manager, trace_manager):
    """테스트용 샘플 데이터 생성"""
    # Epic, Story, AC 생성
    epic = Epic(id="EP_IMPACT_TEST", title="Impact Test Epic", description="", priority="high", status="draft")
    story = UserStory(
        id="US_IMPACT_TEST",
        title="Impact Test Story",
        storyText="As a user, I want to test impact",
        priority="high",
        status="draft"
    )
    ac = AcceptanceCriterion(
        id="AC_IMPACT_TEST",
        title="Impact Test Criterion",
        criterionText="시스템이 정상 동작",
        testType="scenario",
        status="draft"
    )
    
    # Domain 요소 생성
    agg = Aggregate(id="AGG_IMPACT_TEST", name="ImpactTestAgg", description="", kind="root", version=1, status="draft")
    field = Field(id="F_IMPACT_TEST", name="impactField", type="String", description="영향 필드")
    
    # Behavior 요소 생성
    cmd = Command(id="CMD_IMPACT_TEST", name="ImpactCommand", description="", syncMode="sync", source="API")
    evt = Event(id="EVT_IMPACT_TEST", name="ImpactEvent", description="", category="DomainEvent", reliability="at-least-once")
    
    # 노드 생성
    upsert_manager.upsert_epic(epic)
    upsert_manager.upsert_user_story(story)
    upsert_manager.upsert_acceptance_criterion(ac)
    upsert_manager.upsert_aggregate(agg)
    upsert_manager.upsert_field(field)
    upsert_manager.upsert_command(cmd)
    upsert_manager.upsert_event(evt)
    
    # 구조 관계
    upsert_manager.link_epic_to_story("EP_IMPACT_TEST", "US_IMPACT_TEST")
    upsert_manager.link_story_to_criterion("US_IMPACT_TEST", "AC_IMPACT_TEST")
    
    # Trace 링크
    trace_manager.link_story_to_aggregate("US_IMPACT_TEST", "AGG_IMPACT_TEST", confidence=0.9)
    trace_manager.link_criterion_to_field("AC_IMPACT_TEST", "F_IMPACT_TEST", confidence=1.0)
    trace_manager.link_criterion_to_command("AC_IMPACT_TEST", "CMD_IMPACT_TEST", confidence=0.95)
    trace_manager.link_criterion_to_event("AC_IMPACT_TEST", "EVT_IMPACT_TEST", confidence=0.9)
    
    return {
        "epic_id": "EP_IMPACT_TEST",
        "story_id": "US_IMPACT_TEST",
        "ac_id": "AC_IMPACT_TEST",
        "agg_id": "AGG_IMPACT_TEST",
        "field_id": "F_IMPACT_TEST",
        "cmd_id": "CMD_IMPACT_TEST",
        "evt_id": "EVT_IMPACT_TEST",
    }


def test_find_impacted_aggregates_by_story(impact_analyzer, sample_data):
    """Story → Aggregate 영향 탐색 테스트"""
    result = impact_analyzer.find_impacted_aggregates_by_story(sample_data["story_id"])
    
    assert isinstance(result, list)
    assert len(result) > 0
    assert any(agg["id"] == sample_data["agg_id"] for agg in result)


def test_find_impacted_fields_by_criterion(impact_analyzer, sample_data):
    """Criterion → Field 영향 탐색 테스트"""
    result = impact_analyzer.find_impacted_fields_by_criterion(sample_data["ac_id"])
    
    assert isinstance(result, list)
    assert len(result) > 0
    assert any(f["id"] == sample_data["field_id"] for f in result)


def test_find_impacted_behavior_by_criterion(impact_analyzer, sample_data):
    """Criterion → Command/Event 영향 탐색 테스트"""
    result = impact_analyzer.find_impacted_behavior_by_criterion(sample_data["ac_id"])
    
    assert "commands" in result
    assert "events" in result
    assert isinstance(result["commands"], list)
    assert isinstance(result["events"], list)
    assert any(c["id"] == sample_data["cmd_id"] for c in result["commands"])
    assert any(e["id"] == sample_data["evt_id"] for e in result["events"])


def test_find_full_impact_by_story(impact_analyzer, sample_data):
    """Story → 전체 영향 범위 탐색 테스트 (v1.1 표준 형식)"""
    result = impact_analyzer.find_full_impact_by_story(sample_data["story_id"])
    
    # v1.1 표준 출력 형식 확인
    assert "root" in result
    assert result["root"]["label"] == "UserStory"
    assert result["root"]["id"] == sample_data["story_id"]
    
    assert "impacted" in result
    impacted = result["impacted"]
    assert "Aggregate" in impacted
    assert "Field" in impacted
    assert "Command" in impacted
    assert "Event" in impacted
    assert "Policy" in impacted
    
    assert "affected_aggregates" in result
    
    # Story에서 직접 연결된 Aggregate 확인
    assert sample_data["agg_id"] in impacted["Aggregate"]
    
    # AC를 통한 간접 영향 확인
    assert sample_data["field_id"] in impacted["Field"]
    assert sample_data["cmd_id"] in impacted["Command"]
    assert sample_data["evt_id"] in impacted["Event"]


def test_mark_dirty(impact_analyzer, upsert_manager):
    """Dirty 마킹 테스트"""
    # 노드 생성
    agg = Aggregate(
        id="AGG_DIRTY_TEST",
        name="DirtyTest",
        description="",
        kind="root",
        version=1,
        status="draft"
    )
    upsert_manager.upsert_aggregate(agg)
    
    # Dirty 마킹
    result = impact_analyzer.mark_dirty(
        ["AGG_DIRTY_TEST"],
        node_label="Aggregate",
        reason="테스트를 위한 dirty 마킹"
    )
    
    assert result["total"] == 1
    assert result["marked"] == 1
    assert len(result["failed"]) == 0


def test_get_dirty_nodes(impact_analyzer, upsert_manager):
    """Dirty 노드 조회 테스트"""
    # Dirty 마킹된 노드 조회
    result = impact_analyzer.get_dirty_nodes()
    
    assert isinstance(result, list)
    # 이전 테스트에서 마킹한 노드가 있을 수 있음
    assert all("id" in node for node in result)
    assert all("label" in node for node in result)


def test_clear_dirty(impact_analyzer, upsert_manager):
    """Dirty 플래그 제거 테스트"""
    # Dirty 마킹
    impact_analyzer.mark_dirty(["AGG_DIRTY_TEST"], node_label="Aggregate", reason="테스트")
    
    # Dirty 제거
    result = impact_analyzer.clear_dirty(
        node_ids=["AGG_DIRTY_TEST"],
        node_label="Aggregate"
    )
    
    assert result["cleared_count"] >= 0


def test_calculate_regeneration_scope(impact_analyzer, sample_data):
    """재생성 범위 산출 테스트 (v1.1 표준 형식)"""
    result = impact_analyzer.calculate_regeneration_scope(sample_data["story_id"])
    
    assert "story_id" in result
    assert "impact" in result
    assert "impacted_nodes" in result
    assert "affected_aggregates" in result
    assert "total_nodes" in result
    assert "dirty_marked" in result
    
    assert result["story_id"] == sample_data["story_id"]
    assert result["total_nodes"] > 0
    
    # v1.1 형식 확인
    impacted_nodes = result["impacted_nodes"]
    assert "aggregates" in impacted_nodes
    assert "fields" in impacted_nodes
    assert "commands" in impacted_nodes
    assert "events" in impacted_nodes
    assert "policies" in impacted_nodes


@pytest.mark.skip(reason="ES 체인 데이터 설정 필요")
def test_find_es_chain_by_command(impact_analyzer):
    """Event Storming 체인 탐색 테스트"""
    # ES 체인이 설정된 데이터가 필요
    result = impact_analyzer.find_es_chain_by_command("CMD_TEST", max_hops=3)
    
    assert "start_command" in result
    assert "chains" in result
    assert "total_chains" in result

