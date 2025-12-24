# Zero-base SDD v1.2 — Next Phase Spec & Schema (Phase 분리 + UI 통합)

본 문서는 **현재 v1.1(impact→dirty→scope 결정 엔진) 통과** 이후,  
다음 단계에서 **Phase A(초안) / Phase B(Event Storming)** 를 **분리 유지**하면서도 **UI에서 하나처럼 상호작용**하도록 만들기 위한 **v1.2 스펙 + 스키마 확장**입니다.

---

## 0. 목표 요약

### 목표
- **Phase는 분리 유지**  
  - **Phase A**: 요구사항 기반 구조/설계 초안(BC/Sitemap/Aggregate)
  - **Phase B**: Phase A 결과 기반 이벤트스토밍(Command/Event/Policy Chain) 생성
- **UI는 통합처럼 보이게**: 사용자는 “한 프로젝트에서 연속 흐름”으로 느끼되, 내부는 단계별로 실행/재실행 가능
- **Selective Regeneration 준비**: 변경 발생 시 dirty를 기준으로 **Phase별로 재실행 분기** 가능

### v1.2에서 하지 않는 것
- 기존 생성기(레거시) 로직의 완전 이식(=실제 생성 엔진 통합)
- UI 화면 구현(스펙/계약만 정의)

---

## 1. Phase 정의

### Phase A — Drafting (초안 생성)
**입력**: Requirements(Story/AC) + semantic enrichment(선택)  
**출력(그래프 반영)**:
- BoundedContext(BC)
- Sitemap(Command 후보, ReadModel 후보) *(v1.2에서는 최소 Command 후보만 필수)*
- Aggregate/Field 초안(+ Trace links)

**DoD(Phase A 완료 조건)**  
- (필수) `UserStory/AC → Aggregate/Field` trace 링크 존재  
- (필수) `Aggregate(:Element)` 최소 1개 이상 존재  
- (필수) `Command(:Element)` 후보 최소 1개 이상 존재 *(Sitemap 결과)*

---

### Phase B — Event Storming (행위/흐름 생성)
**입력**: Phase A 산출물(특히 Command 후보, Aggregate, Trace)  
**출력(그래프 반영)**:
- Command↔Event↔Policy 체인
- Event → AFFECTS_AGGREGATE 확정
- Trace links(AC → COVERS_COMMAND/EVENT 등) 보강

**DoD(Phase B 완료 조건)**
- (필수) `Aggregate -[:HANDLES_COMMAND]-> Command` 존재
- (필수) `Command -[:EMITS_EVENT]-> Event` 존재
- (선택) `Policy` 포함, `AFFECTS_AGGREGATE` 포함

---

## 2. UI 통합 원칙 (상호작용 스펙)

UI는 “한 흐름”처럼 보이되, 내부는 Phase로 분리 실행/재실행한다.

### 2.1 통합 뷰(단일 프로젝트 캔버스)
- 요구사항(Story/AC) / 도메인(BC/Aggregate/Field) / ES(Command/Event/Policy)을 **한 그래프 뷰**에서 탐색
- 각 요소는 `:Element` 라벨로 타입별 색상/필터 적용

### 2.2 버튼/액션 모델(권장)
- `Phase A 실행(초안 생성)`
- `Phase B 실행(이벤트스토밍 생성)`
- `Re-run (dirty only)` : dirty만 재실행 (Phase 자동 분기)

### 2.3 상호작용 규칙
- 노드(Story/AC/Aggregate/Command) 선택 시: 근거(Trace) 및 영향 범위(Impact) 조회
- “변경 저장” 시: Change detection → Impact → dirty 표시 → “재생성 필요” 배지 노출

---

## 3. 오케스트레이션(Backend) 스펙 — Phase 분리 + 단일 컨트롤러

### 3.1 단일 Orchestrator(권장)
- 내부적으로 Phase A / Phase B를 호출하지만, 외부에는 “한 프로젝트 작업 흐름”으로 제공

### 3.2 실행 분기 규칙(Selective Regen)
- dirty set에 `Aggregate/Field` 포함 → **Phase A 먼저**
- dirty set에 `Command/Event/Policy`만 포함 → **Phase B만**
- 둘 다 포함 → **Phase A → Phase B 순서**

---

## 4. 스키마 확장 (v1.2)

> 기존 v1.1 도메인 스키마는 유지합니다.  
> v1.2는 “실행/통합/재생성”을 위한 운영 메타를 추가합니다.

### 4.1 신규(권장) 노드
#### :Project
- `id`, `name`, `domain`, `created_at`

#### :Workspace *(선택: v1.2 포함 권장)*
- `id`, `type(draft|generated|released)`, `name`, `baseline_ws_id`

#### :Run *(필수 권장)*
- `id`
- `phase(A|B)`
- `agent`(generator 이름)
- `model`, `prompt_version`
- `input_hash`
- `started_at`, `ended_at`
- `status(running|completed|failed)`

#### :Change *(Change detection)*
- `id`, `at`, `reason`
- `label`, `node_id`
- `before_hash`, `after_hash`

#### :Job *(UI 실행/재실행 큐, 선택)*
- `id`, `project_id`, `phase`, `mode(full|dirty)`
- `status`, `created_at`, `started_at`, `ended_at`
- `request_payload_ref`(optional)

---

### 4.2 신규/권장 관계
#### Project/Workspace
- `(Project)-[:HAS_WORKSPACE]->(Workspace)`
- `(Workspace)-[:CONTAINS]->(n)` *(요소/요구사항/메타를 workspace에 귀속)*

#### Run 추적
- `(Run)-[:TOUCHED]->(n)`
- `(Run)-[:DIRTIED]->(n)` *(선택)*

#### Change 추적
- `(Change)-[:CHANGED]->(n)`

#### Job 실행
- `(Job)-[:STARTED_RUN]->(Run)`

---

### 4.3 :Element 라벨 정책(확정)
- `:Element`는 도메인/행위 요소에만 부여  
  `BoundedContext, Aggregate, Entity, ValueObject, Field, Command, Event, Policy`
- Requirements/Meta에는 부여하지 않음  
  `Epic, UserStory, AcceptanceCriterion, Run, Change, Job` 제외

---

## 5. Phase A → Phase B 계약(Contract)

### 5.1 Phase A 최소 산출물(Phase B 입력)
- `(:Aggregate:Element)` 목록(필드 포함)
- `(:Command:Element)` 후보 목록(Sitemap 결과)
- Trace:
  - `(UserStory)-[:IMPACTS_AGGREGATE]->(Aggregate)`
  - `(AC)-[:COVERS_COMMAND]->(Command)` *(없으면 Phase B가 보강 가능)*

### 5.2 Phase B 최소 산출물(그래프 반영)
- `Aggregate -[:HANDLES_COMMAND]-> Command`
- `Command -[:EMITS_EVENT]-> Event`
- (선택) `Policy -[:LISTENS_EVENT]-> Event`, `Policy -[:TRIGGERS_COMMAND]-> Command`
- (선택) `Event -[:AFFECTS_AGGREGATE]-> Aggregate`

---

## 6. Regeneration Context Builder v0 (레거시 생성기 연결용)

v1.2에서는 기존 생성기(레거시)를 바로 이식하지 않고, **입력/출력 계약만 고정**한 채로 임시/기본값을 채워 실행할 수 있게 한다.

### 6.1 입력(Phase A: Aggregate 초안 생성기용) 최소 계약
```json
{
  "root_story_id": "US_001",
  "dirty": {
    "Aggregate": ["AGG_ORDER"],
    "Field": ["F_ORDER_AMOUNT"]
  },
  "requirements": {
    "story": {"id": "US_001", "title": "...", "storyText": "..."},
    "criteria": [{"id":"AC_001","criterionText":"amount > 0"}]
  },
  "context": {
    "bounded_context_hint": "Order",
    "existing_aggregate_snapshot": {"id":"AGG_ORDER","name":"Order","fields":[...]}
  }
}
```

### 6.2 출력(Phase A 결과 업서트용) 최소 계약
```json
{
  "aggregates": [
    {"id":"AGG_ORDER","name":"Order","description":"...", "fields":[...]}
  ],
  "trace": {
    "story_to_aggregate": [{"story_id":"US_001","agg_id":"AGG_ORDER","confidence":0.9}],
    "ac_to_field": [{"ac_id":"AC_001","field_id":"F_ORDER_AMOUNT","confidence":1.0}]
  }
}
```

> ✅ 결론: **기존 Aggregate 초안 생성기 관련 백엔드 코드는 “바이브 코딩 단계에서 Adapter로 붙이면 됩니다.”**  
> v1.2에서는 “입력/출력 계약 + 업서트/trace 갱신”까지만 고정하면 충분합니다.

---

## 7. v1.2 테스트 게이트(권장)

- [ ] Phase A 실행 시 dirty(Aggregate/Field)만 대상으로 입력 컨텍스트가 구성된다
- [ ] Phase A 결과 업서트 후 해당 Aggregate/Field만 dirty가 해제된다
- [ ] Phase B는 Phase A 산출물(특히 Command 후보)을 읽어서 ES를 생성한다
- [ ] UI에서 하나의 프로젝트 그래프 뷰로 탐색/필터가 가능하다(`:Element` 기반)

---

## 8. 산출물 목록

- `spec/phase-v1_2.md` *(본 문서 저장본)*
- `cypher/reset-and-load-test-data.cypher` *(이미 생성됨)*
- `cypher/migrate-element-labels.cypher` *(이미 생성됨)*
- (선택) `cypher/create-ops-meta.cypher` *(Project/Workspace/Run/Change/Job 제약 추가)*
