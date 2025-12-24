# Zero-base SDD v1 — Graph Schema Implementation

제로베이스 SDD(Spec-Driven Development) v1 그래프 스키마 구현입니다.

## 프로젝트 구조

```
.
├── spec/
│   └── spec-v1.md          # 스펙 문서 (그래프 스키마 설계)
├── cypher/
│   ├── schema-init.cypher  # Neo4j 스키마 초기화 스크립트 (제약 조건)
│   └── example-data.cypher # 예제 데이터 삽입 스크립트
├── python/
│   ├── __init__.py
│   ├── types.py            # 타입 정의 (dataclass)
│   ├── schema_manager.py   # 스키마 관리 유틸리티
│   ├── upsert.py           # Upsert API (노드 생성/갱신)
│   ├── traceability.py     # Traceability 링크 생성
│   ├── impact.py           # Impact 탐색 및 Change 감지
│   └── tests/              # 테스트 코드
│       ├── test_schema_manager.py
│       ├── test_upsert.py
│       ├── test_traceability.py
│       └── test_impact.py
├── requirements.txt        # Python 의존성
└── README.md
```

## 시작하기

### 1. Neo4j 설치 및 실행

Neo4j가 설치되어 있고 실행 중이어야 합니다.

```bash
# Neo4j Desktop 또는 Community Edition 사용
# 기본 URI: bolt://localhost:7687
```

### 2. Python 환경 설정

```bash
pip install -r requirements.txt
```

### 3. 스키마 초기화

#### 방법 1: Python 스크립트 사용

```bash
python -m python.schema_manager --action init --password YOUR_PASSWORD
```

#### 방법 2: Cypher 스크립트 직접 실행

Neo4j Browser 또는 cypher-shell에서 `cypher/schema-init.cypher` 파일의 내용을 실행합니다.

### 4. 스키마 검증

```bash
python -m python.schema_manager --action verify --password YOUR_PASSWORD
```

스키마 검증은 **제약 조건(Constraints)과 인덱스(Indexes) 중심**으로 이루어집니다.
노드 레이블/관계 타입은 실제 데이터가 있어야 나타나므로 v1 검증 대상에서 제외됩니다.

### 5. 테스트 실행

```bash
# 환경 변수 설정 (필요 시)
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=your_password

# 테스트 실행
pytest python/tests/
```

### 6. 예제 데이터 삽입 (선택사항)

```bash
python -m python.schema_manager --action example --password YOUR_PASSWORD
```

또는 Neo4j Browser에서 `cypher/example-data.cypher` 파일의 내용을 실행합니다.

## 스키마 구조

### 노드 레이블

- **Requirements**: `Epic`, `UserStory`, `AcceptanceCriterion`
- **Domain**: `BoundedContext`, `Aggregate`, `Entity`, `ValueObject`, `Field`
- **Behavior**: `Command`, `Event`, `Policy`
- **Metadata**: `Run` (운영 메타데이터)

### 주요 관계

- **구조 관계**: `HAS_AGGREGATE`, `HAS_ENTITY`, `HAS_VALUE_OBJECT`, `HAS_FIELD`
- **참조 관계**: `REFERS_TO_AGGREGATE`, `REFERS_TO_FIELD`
- **Event Storming**: `HANDLES_COMMAND`, `EMITS_EVENT`, `LISTENS_EVENT`, `TRIGGERS_COMMAND`, `AFFECTS_AGGREGATE`
- **Traceability**: `IMPACTS_AGGREGATE`, `IMPACTS_FIELD`, `COVERS_COMMAND`, `COVERS_EVENT`

자세한 내용은 [spec/spec-v1.md](spec/spec-v1.md)를 참조하세요.

## 주요 기능

### 1. Upsert API (`upsert.py`)

id 기반으로 노드를 MERGE하는 함수군입니다.

```python
from neo4j import GraphDatabase
from python.upsert import UpsertManager
from python.types import UserStory, Aggregate

driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password"))
upsert = UpsertManager(driver)

# 노드 생성
story = UserStory(
    id="US_001",
    title="주문 생성",
    storyText="As a customer, I want to create an order",
    priority="high",
    status="draft"
)
upsert.upsert_user_story(story)

agg = Aggregate(
    id="AGG_ORDER",
    name="Order",
    description="주문 집합체",
    kind="root",
    version=1,
    status="draft"
)
upsert.upsert_aggregate(agg)
```

### 2. Traceability 링크 생성 (`traceability.py`)

Requirements → Domain/Behavior 추적 링크를 생성합니다.

```python
from python.traceability import TraceabilityManager

trace = TraceabilityManager(driver)

# Story → Aggregate 영향 링크
trace.link_story_to_aggregate(
    story_id="US_001",
    agg_id="AGG_ORDER",
    confidence=0.9,
    rationale="스토리가 Aggregate에 직접 영향"
)

# Criterion → Field/Command/Event 검증 링크
trace.link_criterion_to_field(
    ac_id="AC_001",
    field_id="F_ORDER_AMOUNT",
    confidence=1.0,
    rationale="기준이 필드를 직접 검증"
)
```

### 3. Impact 탐색 및 Change 감지 (`impact.py`)

부분 수정(Selective Regeneration)을 위한 영향 범위 분석입니다.

```python
from python.impact import ImpactAnalyzer

analyzer = ImpactAnalyzer(driver)

# Story 변경 시 영향받는 모든 노드 찾기
impact = analyzer.find_full_impact_by_story("US_001")
print(f"Aggregates: {impact['aggregates']}")
print(f"Fields: {impact['fields']}")
print(f"Commands: {impact['commands']}")
print(f"Events: {impact['events']}")

# Dirty 마킹 (재생성 필요 표시)
analyzer.mark_dirty(
    node_ids=["AGG_ORDER", "F_ORDER_AMOUNT"],
    node_label="Aggregate",
    reason="Story 변경으로 인한 영향"
)

# 재생성 범위 산출
scope = analyzer.calculate_regeneration_scope("US_001")
print(f"총 {scope['total_nodes']}개 노드 재생성 필요")
```

### 4. 전체 워크플로우 예제

```python
from neo4j import GraphDatabase
from python import (
    SchemaManager,
    UpsertManager,
    TraceabilityManager,
    ImpactAnalyzer,
    UserStory,
    Aggregate,
    Field,
    Command,
    Event,
)

driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password"))

# 1. 스키마 초기화 (최초 1회)
schema_mgr = SchemaManager("bolt://localhost:7687", "neo4j", "password")
schema_mgr.initialize_schema()

# 2. 노드 생성
upsert = UpsertManager(driver)
upsert.upsert_user_story(UserStory(id="US_001", title="주문 생성", storyText="...", priority="high", status="draft"))
upsert.upsert_aggregate(Aggregate(id="AGG_ORDER", name="Order", description="...", kind="root", version=1, status="draft"))

# 3. Trace 링크 생성
trace = TraceabilityManager(driver)
trace.link_story_to_aggregate("US_001", "AGG_ORDER", confidence=0.9, rationale="스토리 영향")

# 4. 변경 영향 분석
analyzer = ImpactAnalyzer(driver)
impact = analyzer.find_full_impact_by_story("US_001")
scope = analyzer.calculate_regeneration_scope("US_001")
```

### Neo4j 쿼리 예제

```cypher
// 특정 Epic에 연결된 모든 Aggregate 찾기
MATCH (e:Epic)-[:HAS_STORY]->(us:UserStory)-[:IMPACTS_AGGREGATE]->(agg:Aggregate)
WHERE e.id = 'EP_001'
RETURN e, us, agg

// Event Storming 체인 탐색
MATCH (cmd:Command)-[:EMITS_EVENT]->(evt:Event)<-[:LISTENS_EVENT]-(pol:Policy)
      -[:TRIGGERS_COMMAND]->(nextCmd:Command)
RETURN cmd, evt, pol, nextCmd

// Field의 근거가 된 AcceptanceCriterion 찾기
MATCH (ac:AcceptanceCriterion)-[:IMPACTS_FIELD]->(f:Field)
WHERE f.id = 'F_ORDER_AMOUNT'
RETURN ac, f
```

## v1 완성 정의(DoD) 체크리스트

### 스키마 정의
- [x] 위 Label/Relationship 목록이 문서로 확정되어 있으며, 그래프에 적용 가능한 상태
- [x] `id` 유일 제약이 적용되어 중복 없이 MERGE 가능
- [x] 제약 조건/인덱스 검증 로직 구현 (데이터 존재 여부와 무관)

### 핵심 기능
- [x] **Upsert API**: Story/AC/BC/Aggregate/Command/Event를 id 기반으로 MERGE하는 함수군
- [x] **Trace Link 생성**: IMPACTS / COVERS / ES 체인 관계 생성/갱신
- [x] **Impact 탐색 쿼리**: US/AC 변경 → 영향 노드 리스트업
- [x] **Change 감지**: source_hash 변경 시 영향 탐색 대상 결정
- [x] **Dirty Marking**: 영향 노드에 dirty 플래그 반영
- [x] **부분 수정 범위 산출**: 재생성 필요 범위 자동 계산

### 운영 메타
- [x] Run 메타데이터 노드 스키마 추가 (어떤 실행이 무엇을 만들었는지 추적)

### 테스트
- [x] pytest 기반 테스트 코드 작성
- [x] SchemaManager, Upsert, Traceability, Impact 분석 테스트

### 검증 가능성 (데이터 의존)
- [ ] 최소 Trace 링크(`IMPACTS_AGGREGATE`, `IMPACTS_FIELD`, `COVERS_*`)가 생성되어 역추적 가능 (예제 데이터 포함)
- [ ] ES 체인(Command→Event→Policy→Command→Aggregate 영향)이 탐색 가능 (예제 데이터 포함)

---

## v1.1 개선 사항 (Implementation Improvement)

v1.1에서는 운영성 및 정합성을 개선했습니다. 자세한 내용은 [CHANGELOG-v1.1.md](CHANGELOG-v1.1.md)를 참조하세요.

### 주요 개선 사항

1. **Dirty Marking 표준화**: `is_dirty` → `dirty`로 통일
2. **Impact 탐색 표준화**: ES 체인 확장 (Policy, AFFECTS_AGGREGATE 포함)
3. **Change Detection 구현**: Change 노드 방식으로 이전 값과 비교 가능
4. **id 정책 확정**: 전역 유니크 채택, 라벨 생략 조회 지원
5. **Batch/Transaction 경계**: Run 단위 트랜잭션 관리
6. **Schema 검증 안정화**: 구조 기반 검증 (제약 이름 변경에 영향 없음)

### 새로운 모듈

- `change_detection.py`: ChangeLogger 클래스 (변경 감지)
- `batch.py`: BatchManager 클래스 (배치/트랜잭션 관리)

## 라이선스

이 프로젝트는 스펙 문서에 따라 구현되었습니다.

