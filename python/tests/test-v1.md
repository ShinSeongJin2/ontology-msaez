# Zero-base SDD v1.1 — 테스트 순서 (데이터 기반)

아래 순서대로 진행하면 v1.1 개선사항(impact 확장/dirty 표준화/change 감지 준비)을 빠르게 검증할 수 있습니다.

## 0) 준비
1. `schema-init-v1_1.cypher` 실행 (제약/인덱스 생성)
2. `example-data-v1_1-test.cypher` 실행 (테스트 데이터 삽입)

---

## 1) Dirty 표준화 테스트 (가장 먼저)
**목적:** `dirty/dirty_reason/dirty_at`만 사용되는지 확인

1. `AGG_ORDER`를 dirty로 만듦
   - `MATCH (a:Aggregate {id:'AGG_ORDER'}) SET a.dirty=true, a.dirty_reason='test', a.dirty_at=datetime()`
2. dirty 조회
   - `MATCH (n) WHERE n.dirty=true RETURN labels(n)[0] AS label, n.id AS id, n.dirty_reason AS reason, n.dirty_at AS at ORDER BY at DESC`
3. clear dirty
   - `MATCH (n) WHERE n.dirty=true REMOVE n.dirty, n.dirty_reason, n.dirty_at`

✅ 기대 결과  
- `dirty`로 조회/해제가 되고, `is_dirty` 같은 속성은 생기지 않음

---

## 2) Impact 확장(ES chain 포함) 테스트
**목적:** US_001 변경 시 Policy/추가 Command/affected aggregate까지 잡히는지 확인

1. 영향 탐색 실행 (UserStory 기준)
   - `queries-v1_1.cypher`의 impact 쿼리 실행(또는 ImpactAnalyzer 표준 함수)
2. 결과 확인

✅ 기대 결과(최소)
- Aggregate: `AGG_ORDER` 포함
- Field: `F_ORDER_AMOUNT` 포함
- Command: `CMD_PLACE_ORDER` 포함
- Event: `EVT_ORDER_PLACED` 포함
- Policy: `POL_RESERVE_STOCK` 포함
- AFFECTS_AGGREGATE로 연결된 aggregate: `AGG_STOCK` 포함

---

## 3) Selective Regeneration scope 산출 + Dirty 마킹
**목적:** “영향 범위 → dirty 마킹”이 정확히 동작하는지 확인

1. `calculate_regeneration_scope('US_001')` 실행
2. dirty 노드 목록 조회

✅ 기대 결과  
- 영향을 받은 노드들만 dirty로 표시됨  
- 무관한 노드는 dirty가 되지 않음

---

## 4) Change 감지(준비 테스트)
**목적:** (Change 노드 방식 또는 hash 비교 방식 도입 시) before/after 비교가 되는지 확인

1. `US_001.source_hash`를 `H1 -> H2`로 변경(upsert)
2. Change logger 또는 비교 로직을 통해 “변경 있음” 판정
3. 변경이 있을 때만 2)~3) 수행하도록 분기

✅ 기대 결과  
- 동일 해시 재업서트 시 “변경 없음”  
- 해시 변경 시 “변경 있음” + impact/dirty 실행

---

## 참고: 데이터셋의 핵심 체인
`US_001 -> AC_001 -> CMD_PLACE_ORDER -> EVT_ORDER_PLACED -> POL_RESERVE_STOCK -> CMD_RESERVE_STOCK`  
그리고 `EVT_ORDER_PLACED -> AFFECTS_AGGREGATE -> AGG_STOCK`
