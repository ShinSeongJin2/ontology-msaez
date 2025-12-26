#!/usr/bin/env python3
"""
Neo4j 스키마 및 샘플 데이터 자동 로더 (비대화형)
Usage: python3 load_all.py
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


def execute_cypher_statements(driver, content: str, description: str):
    """Cypher 문장들을 파싱하고 실행"""
    print(f"\n{'='*60}")
    print(f"📂 {description}")
    print('='*60)
    
    statements = []
    current_statement = []
    
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('//') or not stripped:
            continue
        current_statement.append(line)
        if stripped.endswith(';'):
            statements.append('\n'.join(current_statement))
            current_statement = []
    
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
                if success_count % 10 == 0:
                    print(f"   ✓ {success_count} statements executed...")
            except Exception as e:
                error_count += 1
                error_msg = str(e)
                # 이미 존재하는 제약조건/인덱스는 무시
                if "already exists" in error_msg.lower() or "equivalent" in error_msg.lower():
                    success_count += 1
                    error_count -= 1
                else:
                    print(f"   ✗ Error: {error_msg[:80]}")
    
    print(f"   ✅ Completed: {success_count} statements")
    return success_count, error_count


def main():
    print("\n" + "="*60)
    print("🚀 Event Storming Impact Analysis - Auto Loader")
    print("="*60)
    print(f"   URI: {NEO4J_URI}")
    print(f"   User: {NEO4J_USER}")
    
    # Neo4j 연결
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        print("   ✅ Connected to Neo4j\n")
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        print("\n💡 Neo4j Desktop에서 데이터베이스가 실행 중인지 확인하세요.")
        sys.exit(1)
    
    try:
        # 기존 데이터 삭제
        print("🗑️  Clearing existing data...")
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        print("   ✓ Database cleared")
        
        # 로드할 파일들
        files_to_load = [
            ("schema/01_constraints.cypher", "Constraints (유일성 제약조건)"),
            ("schema/02_indexes.cypher", "Indexes (검색 인덱스)"),
            ("seed/sample_data.cypher", "Sample Data (주문 취소 시나리오)"),
        ]
        
        total_success = 0
        total_errors = 0
        
        for filepath, description in files_to_load:
            full_path = PROJECT_ROOT / filepath
            if full_path.exists():
                content = full_path.read_text(encoding='utf-8')
                success, errors = execute_cypher_statements(driver, content, description)
                total_success += success
                total_errors += errors
            else:
                print(f"\n⚠️  File not found: {filepath}")
        
        # 통계 출력
        print("\n" + "="*60)
        print("📊 Database Statistics")
        print("="*60)
        
        with driver.session() as session:
            result = session.run("""
                MATCH (n)
                RETURN labels(n)[0] as label, count(n) as count
                ORDER BY label
            """)
            print("\n📦 Nodes:")
            for record in result:
                print(f"   • {record['label']}: {record['count']}")
            
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as type, count(r) as count
                ORDER BY type
            """)
            print("\n🔗 Relationships:")
            for record in result:
                print(f"   • {record['type']}: {record['count']}")
        
        # 최종 결과
        print("\n" + "="*60)
        print("🎉 Loading Complete!")
        print("="*60)
        print(f"   Total: {total_success} statements executed")
        
        # 영향도 분석 예제 쿼리 실행
        print("\n" + "="*60)
        print("🔍 Impact Analysis Demo: UserStory US-001 (주문 취소)")
        print("="*60)
        
        with driver.session() as session:
            result = session.run("""
                MATCH (us:UserStory {id: "US-001"})
                RETURN us.role + " wants to " + us.action as story
            """)
            for record in result:
                print(f"\n📝 Story: {record['story']}")
            
            result = session.run("""
                MATCH (us:UserStory {id: "US-001"})-[:IMPLEMENTS]->(target)
                RETURN labels(target)[0] as type, target.name as name
            """)
            print("\n🎯 Implements:")
            for record in result:
                print(f"   • {record['type']}: {record['name']}")
            
            result = session.run("""
                MATCH (evt:Event {name: "OrderCancelled"})<-[:SUBSCRIBES]-(ms:Microservice)
                RETURN ms.name as service
            """)
            print("\n⚠️  OrderCancelled 이벤트 변경 시 영향받는 서비스:")
            for record in result:
                print(f"   • {record['service']}")
        
        print("\n💡 Neo4j Browser에서 확인: http://localhost:7474")
        print('   쿼리 예: MATCH (n) RETURN n LIMIT 100')
        
    finally:
        driver.close()


if __name__ == "__main__":
    main()

