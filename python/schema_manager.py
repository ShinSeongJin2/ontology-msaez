"""
Zero-base SDD v1 — 스키마 관리 유틸리티
Neo4j 스키마 초기화 및 검증을 위한 유틸리티 함수
"""

from pathlib import Path
from typing import Optional, Union
from neo4j import GraphDatabase, Session


class SchemaManager:
    """Neo4j 스키마 관리자"""
    
    def __init__(self, uri: str, user: str, password: str):
        """
        Args:
            uri: Neo4j 데이터베이스 URI (예: "bolt://localhost:7687")
            user: Neo4j 사용자명
            password: Neo4j 비밀번호
        """
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
    
    def close(self):
        """드라이버 연결 종료"""
        self.driver.close()
    
    def initialize_schema(self, schema_file: Optional[Union[str, Path]] = None) -> dict:
        """
        스키마 초기화 (제약 조건 생성)
        
        Args:
            schema_file: Cypher 스키마 파일 경로 (None이면 기본 경로 사용)
        
        Returns:
            실행 결과 딕셔너리 (success_count, error_count, errors)
        """
        if schema_file is None:
            # 기본 경로 사용
            base_path = Path(__file__).parent.parent
            schema_file = base_path / "cypher" / "schema-init.cypher"
        
        schema_file = Path(schema_file)
        
        with open(schema_file, "r", encoding="utf-8") as f:
            cypher_script = f.read()
        
        success_count = 0
        error_count = 0
        errors = []
        
        with self.driver.session() as session:
            # Cypher 스크립트를 세미콜론으로 분리하여 실행
            # 주의: 주석 처리된 라인과 빈 라인은 제외
            statements = []
            for s in cypher_script.split(";"):
                s = s.strip()
                if s and not s.startswith("//"):
                    statements.append(s)
            
            for statement in statements:
                if statement:
                    try:
                        session.run(statement)
                        success_count += 1
                        print(f"✓ 실행 완료: {statement[:50]}...")
                    except Exception as e:
                        error_count += 1
                        error_msg = f"실행 실패: {statement[:50]}... 오류: {e}"
                        errors.append(error_msg)
                        print(f"✗ {error_msg}")
        
        return {
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
            "schema_file": str(schema_file)
        }
    
    def verify_constraints(self) -> dict:
        """
        제약 조건 검증 (v1.1 구조 기반 검증)
        제약 조건 이름이 아니라 (Label, property) 구조로 검증
        
        Returns:
            제약 조건 목록과 상태를 포함한 딕셔너리
        """
        # v1.1: 구조 기반 기대 제약 조건 (Label, property, type)
        expected_structure = [
            ("BoundedContext", "id", "UNIQUENESS"),
            ("Aggregate", "id", "UNIQUENESS"),
            ("Entity", "id", "UNIQUENESS"),
            ("ValueObject", "id", "UNIQUENESS"),
            ("Field", "id", "UNIQUENESS"),
            ("Command", "id", "UNIQUENESS"),
            ("Event", "id", "UNIQUENESS"),
            ("Policy", "id", "UNIQUENESS"),
            ("Epic", "id", "UNIQUENESS"),
            ("UserStory", "id", "UNIQUENESS"),
            ("AcceptanceCriterion", "id", "UNIQUENESS"),
            ("Run", "id", "UNIQUENESS"),
            ("Change", "id", "UNIQUENESS"),  # v1.1 추가
        ]
        
        with self.driver.session() as session:
            result = session.run("""
                SHOW CONSTRAINTS
                YIELD name, type, entityType, properties
                RETURN name, type, entityType, properties
                ORDER BY entityType, properties[0]
            """)
            
            constraints = [record.data() for record in result]
            
            # 구조 기반 매칭 (entityType = Label, properties[0] = property)
            existing_structure = set()
            for c in constraints:
                entity_type = c.get("entityType", "").strip("`")
                props = c.get("properties", [])
                constraint_type = c.get("type", "")
                if props and entity_type:
                    # UNIQUENESS 타입만 확인
                    if constraint_type == "UNIQUENESS":
                        prop = props[0].strip("`")
                        existing_structure.add((entity_type, prop, constraint_type))
            
            # 기대 구조와 실제 구조 비교
            expected_set = set(expected_structure)
            missing = expected_set - existing_structure
            extra = existing_structure - expected_set
        
        return {
            "total": len(constraints),
            "expected_count": len(expected_structure),
            "constraints": constraints,
            "expected_structure": expected_structure,
            "existing_structure": list(existing_structure),
            "missing": list(missing),
            "extra": list(extra),
            "all_present": len(missing) == 0,
        }
    
    def verify_indexes(self) -> dict:
        """
        인덱스 검증 (v1.1 구조 기반 검증)
        인덱스 이름이 아니라 (Label, property) 구조로 검증
        
        Returns:
            인덱스 목록과 상태를 포함한 딕셔너리
        """
        # v1.1: 구조 기반 기대 인덱스 (Label, property)
        expected_structure = [
            ("BoundedContext", "name"),
            ("Aggregate", "name"),
            ("Command", "name"),
            ("Event", "name"),
            ("Epic", "title"),
            ("UserStory", "title"),
        ]
        
        with self.driver.session() as session:
            result = session.run("""
                SHOW INDEXES
                YIELD name, type, entityType, properties
                WHERE type = 'BTREE'
                RETURN name, type, entityType, properties
                ORDER BY entityType, properties[0]
            """)
            
            indexes = [record.data() for record in result]
            
            # 구조 기반 매칭
            existing_structure = set()
            for idx in indexes:
                entity_type = idx.get("entityType", "").strip("`")
                props = idx.get("properties", [])
                if props and entity_type:
                    prop = props[0].strip("`")
                    existing_structure.add((entity_type, prop))
            
            # 기대 구조와 실제 구조 비교
            expected_set = set(expected_structure)
            missing = expected_set - existing_structure
            extra = existing_structure - expected_set
        
        return {
            "total": len(indexes),
            "expected_count": len(expected_structure),
            "indexes": indexes,
            "expected_structure": expected_structure,
            "existing_structure": list(existing_structure),
            "missing": list(missing),
            "extra": list(extra),
            "all_present": len(missing) == 0,
        }
    
    def verify_schema(self) -> dict:
        """
        스키마 전체 검증 (제약 조건 + 인덱스 중심)
        노드 레이블/관계 타입은 데이터가 있어야 나타나므로 v1 검증 대상에서 제외
        
        Returns:
            검증 결과 딕셔너리
        """
        constraints = self.verify_constraints()
        indexes = self.verify_indexes()
        
        return {
            "constraints": constraints,
            "indexes": indexes,
            "is_valid": constraints["all_present"] and indexes["all_present"],
        }
    
    def run_example_data(self, data_file: Optional[Union[str, Path]] = None) -> dict:
        """
        예제 데이터 삽입
        
        Args:
            data_file: Cypher 데이터 파일 경로 (None이면 기본 경로 사용)
        
        Returns:
            실행 결과 딕셔너리
        """
        if data_file is None:
            base_path = Path(__file__).parent.parent
            data_file = base_path / "cypher" / "example-data.cypher"
        
        data_file = Path(data_file)
        
        with open(data_file, "r", encoding="utf-8") as f:
            cypher_script = f.read()
        
        success_count = 0
        error_count = 0
        errors = []
        
        with self.driver.session() as session:
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
            
            for statement in statements:
                if statement:
                    try:
                        session.run(statement)
                        success_count += 1
                        print(f"✓ 데이터 삽입 완료: {statement[:50]}...")
                    except Exception as e:
                        error_count += 1
                        error_msg = f"데이터 삽입 실패: {statement[:50]}... 오류: {e}"
                        errors.append(error_msg)
                        print(f"✗ {error_msg}")
        
        return {
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
            "data_file": str(data_file)
        }


def main():
    """CLI 사용 예제"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Zero-base SDD v1 스키마 관리")
    parser.add_argument("--uri", default="bolt://localhost:7687", help="Neo4j URI")
    parser.add_argument("--user", default="neo4j", help="Neo4j 사용자명")
    parser.add_argument("--password", required=True, help="Neo4j 비밀번호")
    parser.add_argument("--action", choices=["init", "verify", "example"], default="verify", help="실행할 작업")
    
    args = parser.parse_args()
    
    manager = SchemaManager(args.uri, args.user, args.password)
    
    try:
        if args.action == "init":
            print("스키마 초기화 중...")
            manager.initialize_schema()
            print("✓ 스키마 초기화 완료")
        
        elif args.action == "verify":
            print("스키마 검증 중...")
            result = manager.verify_schema()
            
            print("\n제약 조건 검증 (구조 기반):")
            constraints = result["constraints"]
            print(f"  예상: {constraints['expected_count']}개, 실제: {constraints['total']}개")
            if constraints["all_present"]:
                print("  ✓ 모든 제약 조건이 존재합니다")
            else:
                print(f"  ✗ 누락된 제약 조건 (구조): {constraints['missing']}")
            
            print("\n인덱스 검증 (구조 기반):")
            indexes = result["indexes"]
            print(f"  예상: {indexes['expected_count']}개, 실제: {indexes['total']}개")
            if indexes["all_present"]:
                print("  ✓ 모든 인덱스가 존재합니다")
            else:
                print(f"  ✗ 누락된 인덱스 (구조): {indexes['missing']}")
            
            print(f"\n전체 검증 결과: {'✓ 통과' if result['is_valid'] else '✗ 실패'}")
        
        elif args.action == "example":
            print("예제 데이터 삽입 중...")
            manager.run_example_data()
            print("✓ 예제 데이터 삽입 완료")
    
    finally:
        manager.close()


if __name__ == "__main__":
    main()

