"""
Zero-base SDD v1 — SchemaManager 테스트
"""

import pytest
from neo4j import GraphDatabase
from python.schema_manager import SchemaManager


@pytest.fixture(scope="module")
def schema_manager():
    """테스트용 SchemaManager 인스턴스"""
    # 실제 Neo4j 연결이 필요하므로 환경 변수 또는 설정 파일 사용
    import os
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")
    
    manager = SchemaManager(uri, user, password)
    yield manager
    manager.close()


def test_initialize_schema(schema_manager):
    """스키마 초기화 테스트"""
    result = schema_manager.initialize_schema()
    
    assert result["success_count"] > 0
    assert result["error_count"] == 0
    assert "schema_file" in result


def test_verify_constraints(schema_manager):
    """제약 조건 검증 테스트 (v1.1 구조 기반)"""
    result = schema_manager.verify_constraints()
    
    assert "total" in result
    assert "expected_count" in result
    assert "constraints" in result
    assert "expected_structure" in result
    assert "existing_structure" in result
    assert "missing" in result  # 구조 기반 missing
    assert "extra" in result
    assert "all_present" in result
    assert isinstance(result["missing"], list)


def test_verify_indexes(schema_manager):
    """인덱스 검증 테스트 (v1.1 구조 기반)"""
    result = schema_manager.verify_indexes()
    
    assert "total" in result
    assert "expected_count" in result
    assert "indexes" in result
    assert "expected_structure" in result
    assert "existing_structure" in result
    assert "missing" in result  # 구조 기반 missing
    assert "extra" in result
    assert "all_present" in result
    assert isinstance(result["missing"], list)


def test_verify_schema(schema_manager):
    """전체 스키마 검증 테스트"""
    result = schema_manager.verify_schema()
    
    assert "constraints" in result
    assert "indexes" in result
    assert "is_valid" in result
    assert isinstance(result["is_valid"], bool)


@pytest.mark.skip(reason="Neo4j 연결 필요")
def test_run_example_data(schema_manager):
    """예제 데이터 삽입 테스트"""
    result = schema_manager.run_example_data()
    
    assert result["success_count"] > 0
    assert "data_file" in result

