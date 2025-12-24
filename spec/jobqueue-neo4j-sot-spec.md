# Zero-base SDD — Neo4j SoT + Firebase Job Queue 병행 스펙 (v1)

본 문서는 **Neo4j(그래프 DB)를 Source of Truth(SoT)** 로 전환하면서도,  
기존에 사용하던 **Firebase 기반 Job Queue(비동기 실행)** 방식은 유지하는 운영/구현 스펙이다.

---

## 1. 결론(원칙)

- ✅ **Firebase Job Queue는 유지**한다.  
- ✅ **Neo4j는 SoT(상태/지식의 단일 진실원)** 로 사용한다.  
- ✅ Firebase에는 “전체 요구사항/전체 산출물 JSON”을 넣지 않고, **Neo4j를 참조하는 키 중심 payload**를 넣는다.

---

## 2. 역할 분리

### 2.1 Neo4j(SoT)가 담당
- Requirements: `Epic`, `UserStory`, `AcceptanceCriterion`
- Draft/Design: `BoundedContext`, `Aggregate`, `Field`, `Entity`, `ValueObject`
- ES/Behavior: `Command`, `Event`, `Policy`
- Traceability: `IMPACTS_*`, `COVERS_*`
- Impact/Dirty: 영향 범위, `dirty/dirty_reason/dirty_at`
- 운영 메타(권장): `Run`, `Change`, `Job` (아래 참고)

### 2.2 Firebase Job Queue가 담당
- 비동기 작업 실행(queued/running/failed/done)
- 재시도/타임아웃/워커 확장/병렬 처리
- 실행 로그 및 중간 산출물 보관(선택)
- UI에 보여줄 “작업 진행 상태”의 실시간 업데이트(선택: Neo4j Run/Job과 동기화)

---

## 3. 왜 Firebase Job Queue를 유지하는가

- LLM 작업은 지연/실패가 잦아 **큐/워커 모델**이 안정적
- Phase A/B를 **분리 실행 + 순서 제어**하기 쉬움
- UI에서 “진행/실패/재시도” 상태 관리가 쉬움
- 향후 다른 큐로 교체해도 **실행 계층만 교체** 가능

---

## 4. 변경되는 핵심 설계(필수)

### 4.1 Job payload는 “전체 입력”이 아니라 “그래프 참조” 중심

Firebase Job에는 원문 요구사항 전체를 넣지 않는다.  
대신, 워커가 Neo4j에서 조회해 입력 컨텍스트를 만들 수 있도록 **참조 키**만 넣는다.

#### 표준 payload 예시
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

- `jobType`: 실행할 생성기 종류
- `projectId`: 프로젝트 식별자
- `rootStoryId`: 변경의 루트(Selective Regen 기준)
- `dirtyNodeIds`: 재생성 대상 집합(결정 엔진 결과)
- `phase`: A(초안) / B(ES)
- `mode`: full / dirty
- `runId`: 실행 추적용(권장)

---

### 4.2 결과 반영(업서트)은 Neo4j가 기준

- Firebase에 결과 JSON을 저장해도 되지만(로그/캐시),  
  **정식 반영(노드/관계/trace/dirty clear)은 Neo4j**에 수행한다.

#### 규칙
- 워커 실행 결과는 **Neo4j UpsertManager/TraceabilityManager**로 반영
- 반영 성공 후에만 `dirty=false`(또는 속성 제거) 처리
- Firebase는 “작업 완료/실패” 상태만 기록해도 됨(선택적으로 Neo4j에도 동기화)

---

### 4.3 실행 상태는 Neo4j Run/Job과 동기화(권장)

Firebase job 상태가 바뀔 때마다 Neo4j에도 기록하면, UI는 Neo4j만 봐도 전체 흐름을 알 수 있다.

- Firebase: `queued → running → done|failed`
- Neo4j: `:Run.status` 또는 `:Job.status`에 동일 상태 반영

---

## 5. 권장 운영 메타 스키마(최소)

### 5.1 :Run (권장 필수)
- `id`
- `phase` (`A|B`)
- `agent` (generator 이름)
- `model`, `prompt_version`
- `input_hash`
- `status` (`queued|running|completed|failed`)
- `started_at`, `ended_at`

관계(권장):
- `(Run)-[:TOUCHED]->(n)` : 실행이 만든/갱신한 노드
- `(Run)-[:DIRTIED]->(n)` : 실행이 dirty 마킹한 노드(선택)

---

### 5.2 :Change (변경 감지)
- `id`
- `label`, `node_id`
- `before_hash`, `after_hash`
- `at`, `reason`

관계:
- `(Change)-[:CHANGED]->(n)`

---

### 5.3 :Job (UI/큐 통합용, 선택)
- `id`
- `project_id`
- `phase` (`A|B`)
- `mode` (`full|dirty`)
- `status`
- `created_at`, `started_at`, `ended_at`
- `payload_ref` (Firebase doc path 등)

관계:
- `(Job)-[:STARTED_RUN]->(Run)`

---

## 6. Phase A/B 실행 규칙(Selective Regeneration)

- dirty set에 `Aggregate/Field` 포함 → **Phase A 먼저**
- dirty set에 `Command/Event/Policy`만 포함 → **Phase B만**
- 둘 다 포함 → **Phase A → Phase B 순서**

> 오케스트레이터는 1개(단일 컨트롤러)로 두되, 내부 실행은 Phase 단위로 분리한다.

---

## 7. 구현 가이드(코드 수정 방향)

### 7.1 “코드에선 원래 쓰던 원본을 보여주면서 스펙에 맞게 수정” 전략
가능하다. 권장 방식은 다음과 같다.

- 기존 Firebase 큐 생산자(Producer) 코드는 유지
- payload를 “전체 JSON” → “Neo4j 참조 키”로 변경
- 워커(Consumer)는:
  1) payload 수신
  2) Neo4j에서 컨텍스트 조회(Story/AC/dirty/인접 요소)
  3) 생성기 실행(Phase A 또는 B)
  4) 결과를 Neo4j에 업서트
  5) 성공 시 dirty clear + Run/Job status 업데이트

> 즉, “원본 코드”에서 수정 포인트는 대부분 **payload 구조와 Neo4j 업서트 반영 로직**이다.

### 7.2 변경 포인트 체크리스트
- [ ] Firebase payload에서 요구사항 원문/전체 JSON 제거
- [ ] payload에 `projectId/rootStoryId/dirtyNodeIds/runId/phase/mode` 추가
- [ ] 워커에서 Neo4j 조회 기반으로 입력 구성(Context Builder)
- [ ] 결과 업서트는 Neo4j(Trace/dirty 포함) 기준으로 처리
- [ ] 상태 동기화: Firebase job status ↔ Neo4j Run/Job status

---

## 8. v1 완료 정의(DoD)

- [ ] Firebase Job은 “참조 기반 payload”만 전달
- [ ] 워커는 Neo4j에서 입력 컨텍스트를 구성한다
- [ ] 생성 결과는 Neo4j에 업서트된다(SoT)
- [ ] dirty는 Neo4j에서만 최종 결정/해제된다
- [ ] (권장) Run/Change/Job 메타가 남아 UI에서 추적 가능하다
