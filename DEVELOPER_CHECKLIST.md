# Zero-base SDD v1.2 통합 개발 체크리스트

## 🎯 목표
v1.1(impact/dirty 결정) → Context Builder → v1.2(Phase A/B 통합 실행) 순서로 진행

---

## 📍 현재 위치
- ✅ v1.1 완료 (ImpactAnalyzer, Dirty 마킹, Change 감지)
- ✅ Context Builder 구현됨 (`python/example/regeneration_context_builder.py`)
- 🔄 v1.2 진입 준비 단계

---

## Phase 0: Context Builder 안정화 및 검증

### 0.1 Context Builder 구조 검증
- [ ] `RegenerationContextBuilder` 클래스가 올바르게 import 가능한지 확인
- [ ] `build_phase_a_aggregate_context` 메서드 시그니처 확인
- [ ] 반환값 구조가 v1.2 스펙 §6.1과 일치하는지 확인

### 0.2 Neo4j 연결 테스트
```bash
# 테스트 데이터로 Context Builder 실행
python3 python/example/regeneration_context_builder.py \
  --uri bolt://localhost:7687 \
  --user neo4j \
  --password <password> \
  --story US_001 \
  --dirty AGG_ORDER F_ORDER_AMOUNT
```

- [ ] Neo4j 연결 성공
- [ ] Story 조회 성공
- [ ] Criteria 조회 성공
- [ ] Aggregate snapshot 조회 성공
- [ ] BC hint 추론 성공
- [ ] 출력 JSON 구조 검증

### 0.3 Edge Case 처리
- [ ] dirty_node_ids가 빈 리스트일 때 (fallback to IMPACTS_AGGREGATE)
- [ ] BC가 존재하지 않을 때 (bc_hint = None)
- [ ] Aggregate가 존재하지 않을 때 (빈 snapshot 반환)
- [ ] Criteria가 없을 때 (빈 배열 반환)

### 0.4 Explain 필드 검증
- [ ] `explain.dirty_groups`가 올바르게 분류되는지
- [ ] `explain.fallback_used`가 정확한지
- [ ] `explain.bc_hint_source`가 명확한지

---

## Phase 1: Adapter 구현 및 레거시 연결

### 1.1 Adapter 기본 구조 확인
- [ ] `AggregateDraftGeneratorAdapter` 클래스 존재 확인
- [ ] `run` 메서드 시그니처 확인
- [ ] Context Builder와의 연결 확인

### 1.2 레거시 생성기 import 경로 확인
```python
# aggregate_draft_generator_adapter.py에서
from python.project_generator.workflows.aggregate_draft.aggregate_draft_generator import AggregateDraftGenerator
```

- [ ] import 경로 정확한지 확인
- [ ] 생성기 초기화 방법 확인 (`__init__` 시그니처)
- [ ] 생성기 실행 방법 확인 (`run` 또는 `generate` 메서드)

### 1.3 입력 변환 구현
**목표**: Context Builder 출력 → 레거시 생성기 입력 형식

레거시 생성기 입력 형식 (추정):
```python
{
  'bounded_context': {...},
  'description': "...",
  'accumulated_drafts': {...},
  'analysis_result': {...}
}
```

- [ ] Context의 `requirements.story` → 레거시 형식 변환
- [ ] Context의 `requirements.criteria` → 레거시 형식 변환
- [ ] Context의 `context.bounded_context_hint` → BC 정보 구성
- [ ] Context의 `context.existing_aggregate_snapshot` → accumulated_drafts 구성

### 1.4 출력 변환 구현
**목표**: 레거시 생성기 출력 → SDD 계약 형식 (v1.2 §6.2)

SDD 계약 출력 형식:
```json
{
  "aggregates": [...],
  "trace": {
    "story_to_aggregate": [...],
    "ac_to_field": [...]
  }
}
```

- [ ] 레거시 출력에서 aggregates 추출
- [ ] 레거시 출력에서 trace 정보 추출 (또는 재구성)
- [ ] SDD 계약 형식으로 변환

### 1.5 Mock 생성기 테스트
- [ ] Mock 생성기 구현 (입력 그대로 반환)
- [ ] Adapter가 Mock 생성기와 정상 작동하는지 확인
- [ ] 출력 형식이 SDD 계약과 일치하는지 확인

---

## Phase 2: v1.2 통합 (Firebase Queue + Neo4j SoT)

### 2.1 Phase 분기 로직 구현

**위치**: Orchestrator 또는 Job Producer

**로직**:
```python
def determine_phase_sequence(dirty_labels: Set[str]) -> List[str]:
    has_structure = any(l in dirty_labels for l in ["Aggregate", "Field"])
    has_behavior = any(l in dirty_labels for l in ["Command", "Event", "Policy"])
    
    if has_structure and has_behavior:
        return ["A", "B"]
    elif has_structure:
        return ["A"]
    elif has_behavior:
        return ["B"]
    else:
        return []
```

- [ ] `ImpactAnalyzer.calculate_regeneration_scope` 결과에서 dirty labels 추출
- [ ] Phase 분기 로직 구현
- [ ] 단위 테스트 작성

### 2.2 Firebase Payload 변경 (Producer)

**기존** (추정):
```json
{
  "jobType": "PHASE_A_AGG_DRAFT",
  "requirements": {...},  // 전체 JSON
  "boundedContext": {...}  // 전체 JSON
}
```

**변경 후**:
```json
{
  "jobType": "PHASE_A_AGG_DRAFT",
  "projectId": "PRJ_001",
  "rootStoryId": "US_001",
  "dirtyNodeIds": ["AGG_ORDER", "F_ORDER_AMOUNT"],
  "phase": "A",
  "mode": "dirty",
  "runId": "RUN_20251223_001"
}
```

- [ ] Payload 생성 로직 수정
- [ ] `projectId`, `rootStoryId`, `dirtyNodeIds` 추가
- [ ] `phase`, `mode`, `runId` 추가
- [ ] 전체 JSON 제거

### 2.3 Consumer 수정 (Neo4j 조회 + Context Builder)

**워커 실행 흐름**:
1. Payload에서 `rootStoryId`, `dirtyNodeIds` 추출
2. Context Builder로 입력 컨텍스트 구성
3. Adapter로 생성기 실행
4. 결과 Neo4j 업서트

- [ ] Payload 파싱 로직 추가
- [ ] Context Builder 호출
- [ ] Adapter 호출 (또는 직접 생성기 호출)
- [ ] 결과 처리

### 2.4 결과 Neo4j 업서트 구현

**필요 작업**:
1. UpsertManager로 노드 생성/업데이트
2. TraceabilityManager로 Trace 링크 생성
3. ImpactAnalyzer.clear_dirty로 dirty 플래그 제거
4. (권장) Run 노드 생성 및 TOUCHED 관계

- [ ] UpsertManager.upsert_aggregate 호출
- [ ] UpsertManager.upsert_field 호출
- [ ] TraceabilityManager.link_story_to_aggregate 호출
- [ ] TraceabilityManager.link_criterion_to_field 호출
- [ ] ImpactAnalyzer.clear_dirty 호출 (업서트된 노드만)
- [ ] Run 노드 생성 (권장)
- [ ] (Run)-[:TOUCHED]->(Aggregate/Field) 관계 생성 (권장)

### 2.5 Run/Job 메타 노드 관리 (권장)

**Run 노드 생성**:
- [ ] `Run` 타입 정의 확인 (`python/types.py`)
- [ ] Run 노드 생성 로직 (id, phase, agent, status 등)
- [ ] (Run)-[:TOUCHED]->(n) 관계 생성
- [ ] 상태 업데이트 (queued → running → completed/failed)

**Job 노드 생성** (선택):
- [ ] `Job` 타입 정의
- [ ] Job 노드 생성
- [ ] (Job)-[:STARTED_RUN]->(Run) 관계 생성
- [ ] Firebase job status ↔ Neo4j Job status 동기화

---

## Phase 3: 통합 테스트 및 검증

### 3.1 End-to-End 테스트
- [ ] 전체 플로우 테스트 (Story 변경 → Impact → Dirty → Context Builder → 생성 → 업서트)
- [ ] Dirty 노드만 재생성되는지 확인
- [ ] Trace 링크가 올바르게 생성되는지 확인
- [ ] Dirty 플래그가 올바르게 해제되는지 확인

### 3.2 Phase 분기 테스트
- [ ] Aggregate/Field dirty → Phase A만 실행
- [ ] Command/Event dirty → Phase B만 실행
- [ ] 둘 다 dirty → Phase A → Phase B 순서 실행

### 3.3 에러 처리
- [ ] Neo4j 연결 실패 시 처리
- [ ] Context Builder 실패 시 처리
- [ ] 생성기 실패 시 처리
- [ ] 업서트 실패 시 처리 (롤백 또는 재시도)

---

## 📚 참고 문서

- `spec/jobqueue-neo4j-sot-spec.md`: Firebase Queue + Neo4j SoT 운영 스펙
- `spec/spec-v1.2.md`: Phase 분리 + UI 통합 스펙
- `python/example/regeneration_context_builder.py`: Context Builder 구현
- `python/example/aggregate_draft_generator_adapter.py`: Adapter 구현 예시

---

## ✅ 완료 기준 (DoD)

v1.2가 완료되었다고 판단하는 기준:

1. ✅ Context Builder가 안정적으로 동작
2. ✅ Adapter가 레거시 생성기와 정상 연결
3. ✅ Firebase Payload가 참조 키 기반으로 변경됨
4. ✅ Consumer가 Neo4j 조회 + Context Builder 사용
5. ✅ 결과가 Neo4j에 올바르게 업서트됨
6. ✅ Dirty 플래그가 올바르게 해제됨
7. ✅ Phase 분기가 올바르게 작동함
8. ✅ (권장) Run/Job 메타 노드가 생성됨

