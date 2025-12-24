"""
Zero-base SDD v1 — Traceability 링크 테스트
"""

import pytest
from neo4j import GraphDatabase
from python.traceability import TraceabilityManager
from python.upsert import UpsertManager
from python.types import UserStory, AcceptanceCriterion, Aggregate, Field, Command, Event


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
def trace_manager(driver):
    """TraceabilityManager 인스턴스"""
    return TraceabilityManager(driver)


@pytest.fixture
def upsert_manager(driver):
    """UpsertManager 인스턴스"""
    return UpsertManager(driver)


def test_link_story_to_aggregate(trace_manager, upsert_manager):
    """UserStory → Aggregate IMPACTS 링크 테스트"""
    # 노드 생성
    story = UserStory(
        id="US_TRACE_TEST",
        title="Trace Test Story",
        storyText="As a user...",
        priority="high",
        status="draft"
    )
    agg = Aggregate(
        id="AGG_TRACE_TEST",
        name="TraceTestAggregate",
        description="테스트용 Aggregate",
        kind="root",
        version=1,
        status="draft"
    )
    
    upsert_manager.upsert_user_story(story)
    upsert_manager.upsert_aggregate(agg)
    
    # Trace 링크 생성
    result = trace_manager.link_story_to_aggregate(
        "US_TRACE_TEST",
        "AGG_TRACE_TEST",
        confidence=0.9,
        rationale="스토리가 Aggregate에 직접 영향"
    )
    
    assert result is True


def test_link_criterion_to_field(trace_manager, upsert_manager):
    """AcceptanceCriterion → Field IMPACTS 링크 테스트"""
    # 노드 생성
    ac = AcceptanceCriterion(
        id="AC_FIELD_TEST",
        title="Field Test Criterion",
        criterionText="필드가 정확해야 함",
        testType="scenario",
        status="draft"
    )
    field = Field(
        id="F_FIELD_TEST",
        name="testField",
        type="String",
        description="테스트 필드"
    )
    
    upsert_manager.upsert_acceptance_criterion(ac)
    upsert_manager.upsert_field(field)
    
    # Trace 링크 생성
    result = trace_manager.link_criterion_to_field(
        "AC_FIELD_TEST",
        "F_FIELD_TEST",
        confidence=1.0,
        rationale="기준이 필드를 직접 검증"
    )
    
    assert result is True


def test_link_criterion_to_command(trace_manager, upsert_manager):
    """AcceptanceCriterion → Command COVERS 링크 테스트"""
    # 노드 생성
    ac = AcceptanceCriterion(
        id="AC_CMD_TEST",
        title="Command Test Criterion",
        criterionText="명령이 정상 실행되어야 함",
        testType="scenario",
        status="draft"
    )
    cmd = Command(
        id="CMD_TEST",
        name="TestCommand",
        description="테스트 명령",
        syncMode="sync",
        source="API"
    )
    
    upsert_manager.upsert_acceptance_criterion(ac)
    upsert_manager.upsert_command(cmd)
    
    # Trace 링크 생성
    result = trace_manager.link_criterion_to_command(
        "AC_CMD_TEST",
        "CMD_TEST",
        confidence=0.95,
        rationale="기준이 명령을 검증"
    )
    
    assert result is True


def test_link_criterion_to_event(trace_manager, upsert_manager):
    """AcceptanceCriterion → Event COVERS 링크 테스트"""
    # 노드 생성
    ac = AcceptanceCriterion(
        id="AC_EVT_TEST",
        title="Event Test Criterion",
        criterionText="이벤트가 발생해야 함",
        testType="scenario",
        status="draft"
    )
    evt = Event(
        id="EVT_TEST",
        name="TestEvent",
        description="테스트 이벤트",
        category="DomainEvent",
        reliability="at-least-once"
    )
    
    upsert_manager.upsert_acceptance_criterion(ac)
    upsert_manager.upsert_event(evt)
    
    # Trace 링크 생성
    result = trace_manager.link_criterion_to_event(
        "AC_EVT_TEST",
        "EVT_TEST",
        confidence=0.9,
        rationale="기준이 이벤트를 검증"
    )
    
    assert result is True


def test_batch_link_story_impacts(trace_manager, upsert_manager):
    """일괄 Story → Aggregate 링크 테스트"""
    # 노드 생성
    story = UserStory(
        id="US_BATCH_TEST",
        title="Batch Test",
        storyText="",
        priority="medium",
        status="draft"
    )
    agg1 = Aggregate(id="AGG_BATCH_1", name="Batch1", description="", kind="root", version=1, status="draft")
    agg2 = Aggregate(id="AGG_BATCH_2", name="Batch2", description="", kind="root", version=1, status="draft")
    
    upsert_manager.upsert_user_story(story)
    upsert_manager.upsert_aggregate(agg1)
    upsert_manager.upsert_aggregate(agg2)
    
    # 일괄 링크 생성
    result = trace_manager.batch_link_story_impacts(
        "US_BATCH_TEST",
        ["AGG_BATCH_1", "AGG_BATCH_2"],
        default_confidence=0.85,
        default_rationale="일괄 영향 분석"
    )
    
    assert result["total"] == 2
    assert result["success"] == 2
    assert len(result["failed"]) == 0


def test_batch_link_criterion_covers(trace_manager, upsert_manager):
    """일괄 Criterion → Command/Event 링크 테스트"""
    # 노드 생성
    ac = AcceptanceCriterion(
        id="AC_BATCH_COVERS",
        title="Batch Covers Test",
        criterionText="",
        testType="scenario",
        status="draft"
    )
    cmd = Command(id="CMD_BATCH", name="BatchCmd", description="", syncMode="sync", source="API")
    evt = Event(id="EVT_BATCH", name="BatchEvt", description="", category="DomainEvent", reliability="at-least-once")
    
    upsert_manager.upsert_acceptance_criterion(ac)
    upsert_manager.upsert_command(cmd)
    upsert_manager.upsert_event(evt)
    
    # 일괄 링크 생성
    result = trace_manager.batch_link_criterion_covers(
        "AC_BATCH_COVERS",
        command_ids=["CMD_BATCH"],
        event_ids=["EVT_BATCH"],
        default_confidence=0.95
    )
    
    assert result["total"] == 2
    assert result["success"] == 2
    assert len(result["failed"]) == 0

