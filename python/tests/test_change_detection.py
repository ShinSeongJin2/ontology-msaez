"""
Zero-base SDD v1.1 — Change Detection 테스트
"""

import pytest
from neo4j import GraphDatabase
from python.change_detection import ChangeLogger
from python.upsert import UpsertManager
from python.types import Aggregate, Field


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
def change_logger(driver):
    """ChangeLogger 인스턴스"""
    return ChangeLogger(driver)


@pytest.fixture
def upsert_manager(driver):
    """UpsertManager 인스턴스"""
    return UpsertManager(driver)


def test_detect_change_first_time(change_logger, upsert_manager):
    """최초 변경 감지 테스트"""
    # 노드 생성
    agg = Aggregate(
        id="AGG_CHANGE_TEST",
        name="ChangeTest",
        description="테스트용 Aggregate",
        kind="root",
        version=1,
        status="draft"
    )
    upsert_manager.upsert_aggregate(agg)
    
    # 변경 감지
    result = change_logger.detect_change(
        node_label="Aggregate",
        node_id="AGG_CHANGE_TEST",
        new_data=agg.__dict__,
        reason="initial_create"
    )
    
    assert result["changed"] is True
    assert result["change_id"] is not None
    assert result["before_hash"] is None  # 첫 생성이므로
    assert result["after_hash"] is not None


def test_detect_change_no_change(change_logger, upsert_manager):
    """변경 없음 감지 테스트"""
    # 노드 생성
    agg = Aggregate(
        id="AGG_NO_CHANGE_TEST",
        name="NoChangeTest",
        description="변경 없음 테스트",
        kind="root",
        version=1,
        status="draft"
    )
    upsert_manager.upsert_aggregate(agg)
    
    # 첫 변경 감지
    change_logger.detect_change(
        node_label="Aggregate",
        node_id="AGG_NO_CHANGE_TEST",
        new_data=agg.__dict__,
        reason="initial"
    )
    
    # 동일한 데이터로 다시 감지 (변경 없음)
    result = change_logger.detect_change(
        node_label="Aggregate",
        node_id="AGG_NO_CHANGE_TEST",
        new_data=agg.__dict__,
        reason="check"
    )
    
    assert result["changed"] is False
    assert result["change_id"] is None
    assert result["before_hash"] == result["after_hash"]


def test_detect_change_modified(change_logger, upsert_manager):
    """변경 감지 테스트"""
    # 노드 생성
    agg = Aggregate(
        id="AGG_MODIFIED_TEST",
        name="ModifiedTest",
        description="원본",
        kind="root",
        version=1,
        status="draft"
    )
    upsert_manager.upsert_aggregate(agg)
    
    # 첫 변경 감지
    first_result = change_logger.detect_change(
        node_label="Aggregate",
        node_id="AGG_MODIFIED_TEST",
        new_data=agg.__dict__,
        reason="initial"
    )
    
    # 데이터 수정
    agg.description = "수정됨"
    agg.version = 2
    upsert_manager.upsert_aggregate(agg)
    
    # 변경 감지 (변경 있음)
    second_result = change_logger.detect_change(
        node_label="Aggregate",
        node_id="AGG_MODIFIED_TEST",
        new_data=agg.__dict__,
        reason="modified"
    )
    
    assert second_result["changed"] is True
    assert second_result["change_id"] is not None
    assert second_result["before_hash"] == first_result["after_hash"]
    assert second_result["after_hash"] != first_result["after_hash"]


def test_get_change_history(change_logger, upsert_manager):
    """변경 이력 조회 테스트"""
    # 노드 생성 및 여러 번 변경
    agg = Aggregate(
        id="AGG_HISTORY_TEST",
        name="HistoryTest",
        description="v1",
        kind="root",
        version=1,
        status="draft"
    )
    upsert_manager.upsert_aggregate(agg)
    
    change_logger.detect_change(
        node_label="Aggregate",
        node_id="AGG_HISTORY_TEST",
        new_data=agg.__dict__,
        reason="v1"
    )
    
    agg.description = "v2"
    agg.version = 2
    upsert_manager.upsert_aggregate(agg)
    change_logger.detect_change(
        node_label="Aggregate",
        node_id="AGG_HISTORY_TEST",
        new_data=agg.__dict__,
        reason="v2"
    )
    
    # 변경 이력 조회
    history = change_logger.get_change_history("AGG_HISTORY_TEST", limit=10)
    
    assert len(history) >= 2
    assert all("id" in record for record in history)
    assert all("at" in record for record in history)
    assert all("reason" in record for record in history)
    assert all("before_hash" in record for record in history)
    assert all("after_hash" in record for record in history)


def test_find_changed_nodes(change_logger, upsert_manager):
    """변경된 노드 목록 조회 테스트"""
    # 여러 노드 생성 및 변경
    agg1 = Aggregate(id="AGG_CHANGED_1", name="Changed1", description="", kind="root", version=1, status="draft")
    agg2 = Aggregate(id="AGG_CHANGED_2", name="Changed2", description="", kind="root", version=1, status="draft")
    
    upsert_manager.upsert_aggregate(agg1)
    upsert_manager.upsert_aggregate(agg2)
    
    change_logger.detect_change("Aggregate", "AGG_CHANGED_1", agg1.__dict__, reason="test1")
    change_logger.detect_change("Aggregate", "AGG_CHANGED_2", agg2.__dict__, reason="test2")
    
    # 변경된 노드 목록 조회
    changed_nodes = change_logger.find_changed_nodes(node_label="Aggregate")
    
    assert len(changed_nodes) >= 2
    assert all("node_id" in node for node in changed_nodes)
    assert all("label" in node for node in changed_nodes)
    assert all("last_changed_at" in node for node in changed_nodes)
    
    node_ids = [node["node_id"] for node in changed_nodes]
    assert "AGG_CHANGED_1" in node_ids
    assert "AGG_CHANGED_2" in node_ids

