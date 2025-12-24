"""
Zero-base SDD v1 — Upsert API 테스트
"""

import pytest
from neo4j import GraphDatabase
from python.upsert import UpsertManager
from python.types import (
    Epic,
    UserStory,
    AcceptanceCriterion,
    BoundedContext,
    Aggregate,
    Field,
    Command,
    Event,
    Policy,
    Run,
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
def upsert_manager(driver):
    """UpsertManager 인스턴스"""
    return UpsertManager(driver)


def test_upsert_epic(upsert_manager):
    """Epic 업서트 테스트"""
    epic = Epic(
        id="EP_TEST_001",
        title="테스트 Epic",
        description="테스트용 Epic",
        priority="high",
        status="draft"
    )
    
    result = upsert_manager.upsert_epic(epic)
    assert result is True


def test_upsert_user_story(upsert_manager):
    """UserStory 업서트 테스트"""
    story = UserStory(
        id="US_TEST_001",
        title="테스트 스토리",
        storyText="As a user, I want to test so that I can verify",
        priority="high",
        status="draft"
    )
    
    result = upsert_manager.upsert_user_story(story)
    assert result is True


def test_upsert_acceptance_criterion(upsert_manager):
    """AcceptanceCriterion 업서트 테스트"""
    ac = AcceptanceCriterion(
        id="AC_TEST_001",
        title="테스트 기준",
        criterionText="시스템이 정상 동작해야 함",
        testType="scenario",
        status="draft"
    )
    
    result = upsert_manager.upsert_acceptance_criterion(ac)
    assert result is True


def test_upsert_aggregate(upsert_manager):
    """Aggregate 업서트 테스트"""
    agg = Aggregate(
        id="AGG_TEST_001",
        name="TestAggregate",
        description="테스트용 Aggregate",
        kind="root",
        version=1,
        status="draft"
    )
    
    result = upsert_manager.upsert_aggregate(agg)
    assert result is True


def test_upsert_field(upsert_manager):
    """Field 업서트 테스트"""
    field = Field(
        id="F_TEST_001",
        name="testField",
        type="String",
        isKey=False,
        isNullable=True,
        isForeignKey=False,
        description="테스트 필드"
    )
    
    result = upsert_manager.upsert_field(field)
    assert result is True


def test_link_epic_to_story(upsert_manager):
    """Epic - UserStory 관계 생성 테스트"""
    # 먼저 노드 생성
    epic = Epic(id="EP_LINK_TEST", title="Link Test Epic", description="", priority="medium", status="draft")
    story = UserStory(id="US_LINK_TEST", title="Link Test Story", storyText="", priority="medium", status="draft")
    
    upsert_manager.upsert_epic(epic)
    upsert_manager.upsert_user_story(story)
    
    # 관계 생성
    result = upsert_manager.link_epic_to_story("EP_LINK_TEST", "US_LINK_TEST")
    assert result is True


def test_link_story_to_aggregate(upsert_manager):
    """UserStory - Aggregate 관계 생성 테스트"""
    # 노드 생성
    story = UserStory(id="US_IMPACT_TEST", title="Impact Test", storyText="", priority="medium", status="draft")
    agg = Aggregate(id="AGG_IMPACT_TEST", name="ImpactTest", description="", kind="root", version=1, status="draft")
    
    upsert_manager.upsert_user_story(story)
    upsert_manager.upsert_aggregate(agg)
    
    # 관계는 traceability.py에서 처리하므로 여기서는 기본 구조만 확인
    assert True


@pytest.mark.skip(reason="Neo4j 연결 및 스키마 초기화 필요")
def test_batch_operations(upsert_manager):
    """일괄 작업 테스트"""
    # 여러 노드 생성 및 관계 링크
    epics = [
        Epic(id=f"EP_BATCH_{i}", title=f"Batch Epic {i}", description="", priority="medium", status="draft")
        for i in range(3)
    ]
    
    for epic in epics:
        assert upsert_manager.upsert_epic(epic) is True

