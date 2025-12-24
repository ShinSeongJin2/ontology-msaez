"""
Zero-base SDD v1.1 — Batch/Transaction 관리 테스트
"""

import pytest
from neo4j import GraphDatabase
from python.batch import BatchManager
from python.types import (
    Epic,
    UserStory,
    AcceptanceCriterion,
    Aggregate,
    BoundedContext,
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
def batch_manager(driver):
    """BatchManager 인스턴스"""
    return BatchManager(driver)


def test_execute_batch_write(batch_manager):
    """배치 작업 실행 테스트"""
    operations = [
        {
            "type": "upsert_epic",
            "params": {
                "id": "EP_BATCH_TEST",
                "title": "Batch Test Epic",
                "description": "배치 테스트용 Epic",
                "priority": "high",
                "status": "draft"
            }
        },
        {
            "type": "upsert_user_story",
            "params": {
                "id": "US_BATCH_TEST",
                "title": "Batch Test Story",
                "storyText": "As a user, I want to test batch",
                "priority": "high",
                "status": "draft"
            }
        },
        {
            "type": "link_epic_to_story",
            "params": {
                "epic_id": "EP_BATCH_TEST",
                "story_id": "US_BATCH_TEST"
            }
        }
    ]
    
    result = batch_manager.execute_batch_write(operations, run_id="RUN_BATCH_TEST")
    
    assert result["total"] == 3
    assert result["success"] == 3
    assert len(result["failed"]) == 0
    assert result["run_id"] == "RUN_BATCH_TEST"


def test_upsert_bundle(batch_manager):
    """Bundle 업서트 테스트 (Story + AC + Aggregate)"""
    nodes = [
        {
            "type": "epic",
            "data": {
                "id": "EP_BUNDLE_TEST",
                "title": "Bundle Test Epic",
                "description": "Bundle 테스트",
                "priority": "medium",
                "status": "draft"
            }
        },
        {
            "type": "user_story",
            "data": {
                "id": "US_BUNDLE_TEST",
                "title": "Bundle Test Story",
                "storyText": "As a user...",
                "priority": "medium",
                "status": "draft"
            }
        },
        {
            "type": "acceptance_criterion",
            "data": {
                "id": "AC_BUNDLE_TEST",
                "title": "Bundle Test AC",
                "criterionText": "시스템이 동작해야 함",
                "testType": "scenario",
                "status": "draft"
            }
        },
        {
            "type": "aggregate",
            "data": {
                "id": "AGG_BUNDLE_TEST",
                "name": "BundleTestAgg",
                "description": "Bundle 테스트용 Aggregate",
                "kind": "root",
                "version": 1,
                "status": "draft"
            }
        }
    ]
    
    relationships = [
        {
            "type": "HAS_STORY",
            "from": "EP_BUNDLE_TEST",
            "to": "US_BUNDLE_TEST"
        },
        {
            "type": "HAS_CRITERION",
            "from": "US_BUNDLE_TEST",
            "to": "AC_BUNDLE_TEST"
        }
    ]
    
    result = batch_manager.upsert_bundle(
        run_id="RUN_BUNDLE_TEST",
        nodes=nodes,
        relationships=relationships
    )
    
    assert result["total"] > 0
    assert result["success"] > 0
    assert len(result["failed"]) == 0


def test_batch_write_transaction_rollback(batch_manager):
    """트랜잭션 롤백 테스트 (실패 시)"""
    operations = [
        {
            "type": "upsert_epic",
            "params": {
                "id": "EP_ROLLBACK_TEST",
                "title": "Rollback Test",
                "description": "롤백 테스트",
                "priority": "high",
                "status": "draft"
            }
        },
        {
            "type": "upsert_user_story",
            "params": {
                "id": "US_ROLLBACK_TEST",
                "title": "Rollback Test",
                "storyText": "Test",
                "priority": "high",
                "status": "draft"
            }
        },
        {
            "type": "unknown_operation",  # 존재하지 않는 작업 타입 (실패 유도)
            "params": {}
        }
    ]
    
    result = batch_manager.execute_batch_write(operations)
    
    # 트랜잭션 실패 시 롤백되어야 함
    assert "transaction_error" in result or len(result["failed"]) > 0

