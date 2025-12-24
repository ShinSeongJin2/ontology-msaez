# Zero-base SDD v1.1 — 개선 스펙 (Implementation Improvement Spec)

본 문서는 현재까지 구현된 구성요소(스키마/업서트/트레이스/임팩트/더티 마킹)를 기준으로,  
**Selective Regeneration(부분 수정)** 을 “운영 가능 수준”으로 끌어올리기 위해 v1.1에서 반드시 보완해야 할 개선 항목을 정의한다.

> 범위: v1.1은 **정합성/운영성 보강**이 목표이며, 실제 LLM 재생성 실행은 v1.2+에서 연결한다.  
> 목표: “변경 감지 → 영향 탐색(ES 체인 포함) → dirty 마킹 → 재생성 범위 산출”이 일관되게 동작.

---

## 1. 현재 구현 상태 요약

### 1.1 구현되어 있는 것
- 스키마 초기화 및 검증(제약/인덱스 중심) 유틸리티
- id 기반 노드 업서트(Requirements/Domain/Behavior)
- 구조 관계 업서트(HAS_*, HANDLES/EMITS/LISTENS/TRIGGERS/AFFECTS 등)
- Traceability 링크 업서트(IMPACTS_*, COVERS_*) + 관계 속성(confidence, rationale, evidence_ref, created_at)
- Impact 탐색(Story/AC 기준, 부분 범위)
- Dirty Marking / Clear / 조회

### 1.2 운영 관점에서 부족한 것(핵심)
- dirty 속성명 혼재(`dirty` vs `is_dirty`)
- impact 탐색이 ES 체인 확장(Policy/추가 Command/AFFECTS_AGGREGATE)까지 포함하지 않음
- source_hash 기반 “변경 감지”가 placeholder 수준(실 비교/Change 로그/Workspace baseline 없음)
- id 전역 유니크 정책 미고정(라벨 생략 조회 위험)
- 배치/트랜잭션 경계 부재(대량 처리 성능/일관성 리스크)
- 스키마 검증이 제약 “이름”에 종속될 가능성(구조 기반 검증 권장)

---

## 2. v1.1 개선 목표(DoD)

v1.1에서 아래 항목을 충족하면 “부분 수정 준비 완료”로 간주한다.

- [ ] **Dirty 표준화**: 모든 컴포넌트가 동일한 속성명 `dirty`, `dirty_reason`, `dirty_at`를 사용한다.
- [ ] **Impact 표준화(ES 확장 포함)**: UserStory/AC 변경 시, 최소한 다음 레이블을 포함해 영향 범위를 산출한다.  
  `Aggregate`, `Field`, `Command`, `Event`, `Policy`, `AFFECTS_AGGREGATE(영향받는 Aggregate)`
- [ ] **Change Detection 최소 구현**: 변경 감지는 반드시 “이전 값과 비교” 가능한 방식(아래 옵션 중 1개)을 채택한다.
- [ ] **id 정책 확정**: `id` 전역 유니크 또는 “라벨 필수 조회” 중 하나를 시스템 정책으로 확정한다.
- [ ] **Batch/Transaction 경계 도입**: upsert/link 작업은 최소 Run 단위로 트랜잭션 경계를 가진다.
- [ ] **Schema verify 안정화**: 스키마 검증은 제약/인덱스를 **(Label, property)** 기준으로 판정한다.

---

## 3. 개선 항목 상세 스펙

### 3.1 Dirty Marking 표준화

#### 3.1.1 표준 속성명
- 노드 공통:
  - `dirty: boolean`
  - `dirty_reason: string`
  - `dirty_at: datetime`

> 금지: `is_dirty` 사용 금지(레거시가 있다면 마이그레이션 처리)

#### 3.1.2 Dirty 마킹 규칙
- Impact 탐색 결과로 나온 “대상 노드”는 모두 `dirty=true`로 설정한다.
- `dirty_reason`는 “트리거(US/AC 변경)”를 포함한다.
- `dirty_at`는 `datetime()`으로 기록한다.

#### 3.1.3 Dirty 해제 규칙
- 재생성 완료 시(또는 수동 승인 시) 해당 노드의 `dirty`를 제거한다.
- 해제 시점/근거 기록이 필요하면 v1.2에서 `Change` 로그로 확장한다.

---

### 3.2 Impact 탐색 표준화 (ES 체인 확장 포함)

#### 3.2.1 표준 입력
- `UserStory.id` 또는 `AcceptanceCriterion.id`

#### 3.2.2 표준 출력(JSON 형태)
```json
{
  "root": {"label": "UserStory", "id": "US_001"},
  "impacted": {
    "Aggregate": ["AGG_ORDER"],
    "Field": ["F_ORDER_AMOUNT"],
    "Command": ["CMD_PLACE_ORDER"],
    "Event": ["EVT_ORDER_PLACED"],
    "Policy": ["POL_RESERVE_STOCK"]
  },
  "affected_aggregates": ["AGG_STOCK"]
}
```

#### 3.2.3 ES 체인 확장 규칙
Impact 확장 시 최소한 아래를 포함한다.

- `AC -[:COVERS_COMMAND]-> Command`
- `Command -[:EMITS_EVENT]-> Event`
- `Policy -[:LISTENS_EVENT]-> Event`
- `Policy -[:TRIGGERS_COMMAND]-> Command`
- `Event -[:AFFECTS_AGGREGATE]-> Aggregate`

> NOTE: ES 체인 탐색은 순환 가능성이 있으므로 v1.1에서는 hop 제한(예: 2~3 step)을 둔다.

---

### 3.3 Change Detection(변경 감지) 최소 구현

v1.1에서는 아래 옵션 중 1개를 선택하여 “이전 값 대비 변경”을 판정해야 한다.

#### 옵션 A: Change 노드(권장, 가장 단순/확장성 좋음)
- 노드: `:Change {id, at, reason, label, node_id, before_hash, after_hash}`
- 관계: `(Change)-[:CHANGED]->(n)`
- 판정: 새 입력의 `source_hash`와 직전 `Change.after_hash` 비교

#### 옵션 B: Workspace baseline 비교
- `Workspace(type=generated)`가 `baseline_ws_id`를 통해 이전 상태를 참조
- 동일 `id` 노드의 `source_hash`를 비교하여 변경 판정

#### 옵션 C: 외부 저장소 last_hash 테이블
- RDB/Redis에 `(label,id)->last_source_hash` 저장
- upsert 전에 비교 후 변경 판정

> v1.1에서는 “변경 감지”가 최소 구현 수준이어도 되지만, 반드시 “비교 가능한 상태”여야 한다(placeholder 금지).

---

### 3.4 id 정책 확정

현재 구현에는 `MATCH (n {id:$id})` 형태가 존재한다. 이는 아래 중 하나를 확정해야 안전하다.

#### 정책 1: id 전역 유니크(강추)
- 모든 노드 라벨에서 `id`가 전역으로 유일하도록 규칙화한다.
- 장점: 라벨 없이도 안전한 조회/마킹 가능
- 단점: ID 규칙 관리가 엄격해야 함

#### 정책 2: 라벨 필수 조회
- 모든 조회/업데이트는 반드시 라벨을 명시한다.
- `mark_dirty(node_ids, node_label)`에서 `node_label`을 필수로 강제한다.

> v1.1 결정사항: (택1)  
> - [ ] 정책 1: id 전역 유니크  
> - [ ] 정책 2: 라벨 필수 조회

---

### 3.5 Batch / Transaction 경계 도입

대량 요구사항 처리 시 성능/정합성을 위해, v1.1에서는 최소 Run 단위로 트랜잭션 경계를 도입한다.

#### 3.5.1 권장 패턴
- `execute_write()` 기반의 배치 함수 제공
- 예) `upsert_bundle(run_id, nodes, relationships)` 한 번 호출로 처리

#### 3.5.2 최소 DoD
- “Story 1건 + AC N건 + Aggregate/Field/Command/Event/Policy” 업서트가
  - 단일 트랜잭션으로 수행 가능하거나
  - 실패 시 부분 적용이 최소화되도록 경계가 정의되어야 한다.

---

### 3.6 Schema 검증 안정화

#### 3.6.1 검증 기준
- 제약/인덱스의 “이름”이 아니라 “구조”로 검증한다.
  - 예) `(:Aggregate) id UNIQUE` 존재 여부
  - 예) `(:Field) name INDEX` 존재 여부

#### 3.6.2 DoD
- `verify_constraints()` 결과에서 `missing == []`
- `verify_indexes()` 결과에서 `missing == []`

---

## 4. v1.1 구현 산출물 목록(권장)

- `schema-init-v1_1.cypher` (constraints + indexes)
- `impact-v1_1.cypher` (표준 impact query 1개)
- `dirty-v1_1.cypher` (mark/clear 쿼리)
- `SchemaManager v1.1` (제약/인덱스 검증)
- `ImpactAnalyzer v1.1` (표준 impact + hop 제한)
- `TraceabilityManager v1.1` (관계 속성 표준 유지)
- (선택) `ChangeLogger v1.1` (옵션 A 채택 시)

---

## 5. v1.1 완료 후 다음 단계(v1.2) 체크포인트

v1.1이 완료되면, v1.2에서는 아래를 수행한다.
- OntologyWriter(업서트+링크)를 “Generator 실행 단위(Run)”로 통합
- dirty 대상만 재생성하는 regen 플로우 연결
- released export JSON 계약 확정(바이브 코딩 입력)

---

## 6. 결정 필요 항목(Owner Action)

- [ ] id 정책 선택(전역 유니크 vs 라벨 필수)
- [ ] Change Detection 옵션 선택(A/B/C)
- [ ] ES chain hop 제한 값 결정(기본 2~3 권장)
