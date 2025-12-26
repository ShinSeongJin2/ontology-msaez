#!/usr/bin/env python3
"""
Neo4j 스키마 및 샘플 데이터 로더
Usage: python load_schema.py
"""

from neo4j import GraphDatabase
from pathlib import Path
import sys

# Neo4j 연결 설정
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "12345msaez"

# 프로젝트 루트 경로
PROJECT_ROOT = Path(__file__).parent.parent


def load_cypher_file(driver, filepath: Path, description: str):
    """Cypher 파일을 읽어서 실행"""
    print(f"\n{'='*60}")
    print(f"📂 Loading: {description}")
    print(f"   File: {filepath.name}")
    print('='*60)
    
    content = filepath.read_text(encoding='utf-8')
    
    # 주석과 빈 줄을 제외한 실제 쿼리문 추출
    # 세미콜론으로 구분된 각 문장을 개별 실행
    statements = []
    current_statement = []
    
    for line in content.split('\n'):
        stripped = line.strip()
        # 주석이나 빈 줄 건너뛰기
        if stripped.startswith('//') or not stripped:
            continue
        current_statement.append(line)
        if stripped.endswith(';'):
            statements.append('\n'.join(current_statement))
            current_statement = []
    
    # 마지막 문장 (세미콜론 없는 경우)
    if current_statement:
        statements.append('\n'.join(current_statement))
    
    success_count = 0
    error_count = 0
    
    with driver.session() as session:
        for i, stmt in enumerate(statements, 1):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                session.run(stmt)
                success_count += 1
                # 진행 상황 표시 (10개마다)
                if success_count % 10 == 0:
                    print(f"   ✓ {success_count} statements executed...")
            except Exception as e:
                error_count += 1
                print(f"   ✗ Error in statement {i}: {str(e)[:80]}")
    
    print(f"\n   ✅ Success: {success_count} statements")
    if error_count > 0:
        print(f"   ❌ Errors: {error_count} statements")
    
    return success_count, error_count


def clear_database(driver):
    """기존 데이터 삭제 (선택적)"""
    print("\n⚠️  Clearing existing data...")
    with driver.session() as session:
        # 모든 관계와 노드 삭제
        session.run("MATCH (n) DETACH DELETE n")
    print("   ✓ Database cleared")


def show_statistics(driver):
    """데이터베이스 통계 출력"""
    print("\n" + "="*60)
    print("📊 Database Statistics")
    print("="*60)
    
    with driver.session() as session:
        # 노드 수 집계
        result = session.run("""
            MATCH (n)
            RETURN labels(n)[0] as label, count(n) as count
            ORDER BY label
        """)
        print("\n📦 Nodes:")
        for record in result:
            print(f"   • {record['label']}: {record['count']}")
        
        # 관계 수 집계
        result = session.run("""
            MATCH ()-[r]->()
            RETURN type(r) as type, count(r) as count
            ORDER BY type
        """)
        print("\n🔗 Relationships:")
        for record in result:
            print(f"   • {record['type']}: {record['count']}")


def main():
    print("\n" + "="*60)
    print("🚀 Event Storming Impact Analysis - Schema Loader")
    print("="*60)
    print(f"   URI: {NEO4J_URI}")
    print(f"   User: {NEO4J_USER}")
    
    # Neo4j 연결
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        print("   ✅ Connected to Neo4j")
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        print("\n💡 Neo4j Desktop에서 데이터베이스가 실행 중인지 확인하세요.")
        sys.exit(1)
    
    try:
        # 기존 데이터 삭제 여부 확인
        response = input("\n🗑️  Clear existing data before loading? (y/N): ").strip().lower()
        if response == 'y':
            clear_database(driver)
        
        # 스키마 파일 로드 순서
        schema_files = [
            (PROJECT_ROOT / "schema" / "01_constraints.cypher", "Constraints (유일성 제약조건)"),
            (PROJECT_ROOT / "schema" / "02_indexes.cypher", "Indexes (검색 인덱스)"),
        ]
        
        # 샘플 데이터 로드 여부 확인
        load_sample = input("\n📦 Load sample data (주문 취소 시나리오)? (Y/n): ").strip().lower()
        if load_sample != 'n':
            schema_files.append(
                (PROJECT_ROOT / "seed" / "sample_data.cypher", "Sample Data (주문 취소 시나리오)")
            )
        
        # 파일 순차 로드
        total_success = 0
        total_errors = 0
        
        for filepath, description in schema_files:
            if filepath.exists():
                success, errors = load_cypher_file(driver, filepath, description)
                total_success += success
                total_errors += errors
            else:
                print(f"\n⚠️  File not found: {filepath}")
        
        # 통계 출력
        show_statistics(driver)
        
        # 최종 결과
        print("\n" + "="*60)
        print("🎉 Loading Complete!")
        print("="*60)
        print(f"   Total Success: {total_success} statements")
        print(f"   Total Errors: {total_errors} statements")
        print("\n💡 Neo4j Browser에서 다음 쿼리로 확인하세요:")
        print('   MATCH (n) RETURN n LIMIT 50')
        
    finally:
        driver.close()


if __name__ == "__main__":
    main()

