# Zero-base SDD v1 — Graph Schema Spec (Draft Data Schema)

본 문서는 **제로베이스 SDD(Spec-Driven Development)** 를 시작하기 위한 **초안 데이터 스키마(그래프 스키마: 노드/관계 설계)** v1입니다.  
목표는 **요구사항(Epic/Story/AC) ↔ 도메인 설계(BC/Aggregate/Field) ↔ 이벤트스토밍(Command/Event/Policy)** 을 단일 그래프에서 연결하여, **역추적 기반 부분 수정(Selective Regeneration)** 이 가능하도록 하는 것입니다.

---

## 0. 설계 원칙 (v1)

- **Traceability First**: 모든 핵심 산출물은 “어떤 요구사항에서 왔는지”를 그래프 관계로 추적 가능해야 함.
- **Deterministic Upsert**: `id` 기반으로 중복 없이 MERGE/UPSERT 가능해야 함.
- **Progressive Elaboration**: 처음에는 거칠게(BC/Aggregate/Command/Event) 만들고 점차 세밀하게(Entity/VO/Field/AC/Policy) 확장 가능해야 함.
- **부분 수정**: 요구사항 일부가 바뀌면 해당 요구사항을 기준으로 영향 노드를 찾고(impact) 해당 서브그래프만 재생성(regen)할 수 있어야 함.

> 참고: 운영 메타(Project/Workspace/Run 등)는 v1에서 **선택(옵션)** 으로 두되, 추후 통합/감사/디버깅을 위해 추가를 권장합니다.

---

## 1. 노드(Label) 설계

### 1.1 Bounded Context

**:BoundedContext**
- `id: string` (내부용 유니크 ID)
- `name: string`
- `description: string`
- `domain: string` (예: `"Order"`, `"Billing"`)
- `kind: string` (예: `"core"`, `"supporting"`, `"generic"`)
- *(권장)* `status: string` (`"draft" | "confirmed" | "deprecated"`)
- *(권장)* `version: int`
- *(권장)* `source_hash: string` (입력/근거 해시)

---

### 1.2 Aggregate / Entity / ValueObject / Field

**:Aggregate**
- `id: string`
- `name: string`
- `description: string`
- `kind: string` (`"root"` 고정으로 두거나 root 여부만 표현)
- `version: int`
- `status: string` (`"draft"`, `"confirmed"`, `"deprecated"` …)
- *(권장)* `source_hash: string`

**:Entity**
- `id: string`
- `name: string`
- `description: string`
- *(권장)* `status: string`, `version: int`

**:ValueObject**
- `id: string`
- `name: string`
- `description: string`
- *(권장)* `status: string`, `version: int`

**:Field**
- `id: string`
- `name: string`
- `type: string` (예: `"String"`, `"Money"`, `"UUID"`, `"DateTime"`)
- `isKey: boolean` (Aggregate ID 여부)
- `isNullable: boolean`
- `isForeignKey: boolean`
- `description: string`
- *(권장)* `source_hash: string`

---

### 1.3 Behavior: Command / Event / Policy

**:Command**
- `id: string`
- `name: string`
- `description: string`
- `syncMode: string` (`"sync" | "async"`)
- `source: string` (`"API"`, `"Scheduler"`, `"Policy"` 등)
- *(권장)* `template_key: string` (예: `api.command.http`)

**:Event**
- `id: string`
- `name: string`
- `description: string`
- `category: string` (`"DomainEvent" | "IntegrationEvent"`)
- `reliability: string` (예: `"at-least-once"`)
- *(권장)* `payload_schema_ref: string`

**:Policy**
- `id: string`
- `name: string`
- `description: string`
- `kind: string` (`"saga" | "process-manager" | "rule"`)
- `conditionExpr: string` (간단한 표현식 or DMN key)

---

### 1.4 Requirements: Epic / UserStory / AcceptanceCriterion

**:Epic**
- `id: string`
- `title: string`
- `description: string`
- `priority: string`
- `status: string`

**:UserStory**
- `id: string`
- `title: string`
- `asIs: string` *(optional)*
- `toBe: string` *(optional)*
- `storyText: string` (`"As a ... I want ... so that ..."`)
- `priority: string`
- `status: string`
- *(권장)* `semantic_text: string` (LLM 의미 보강 결과)
- *(권장)* `keywords: list[string]`

**:AcceptanceCriterion**
- `id: string`
- `title: string`
- `criterionText: string`
- `testType: string` (`"example" | "scenario" | "rule"`)
- `status: string`
- *(권장)* `semantic_text: string`, `keywords: list[string]`

---

## 2. 관계(Relationship) 설계

### 2.1 Bounded Context ↔ Aggregate

- `(:BoundedContext)-[:HAS_AGGREGATE]->(:Aggregate)`

> 한 BC 안에 여러 Aggregate. Aggregate는 어떤 BC에 속하는지 역방향 탐색으로 확인 가능.

---

### 2.2 Aggregate 내부 구조 (Entity / ValueObject / Field)

- `(:Aggregate)-[:HAS_ENTITY]->(:Entity)`
- `(:Aggregate)-[:HAS_VALUE_OBJECT]->(:ValueObject)`

- `(:Aggregate)-[:HAS_FIELD]->(:Field)` (Aggregate Root 필드)
- `(:Entity)-[:HAS_FIELD]->(:Field)`
- `(:ValueObject)-[:HAS_FIELD]->(:Field)`

> ERD 자동 생성 시 루트/엔터티/VO 기준으로 Field를 묶어 추출 가능.

---

### 2.3 Aggregate 간 “정적 참조” 관계 (ID 기반 참조)

- `(:Aggregate)-[:REFERS_TO_AGGREGATE { viaField: string }]->(:Aggregate)`
- `(:Field)-[:REFERS_TO_FIELD]->(:Field)`

예: `Order.customerId` → `Customer.customerId`

---

### 2.4 Event Storming 핵심 흐름: Command – Event – Policy – Aggregate

- `(:Aggregate)-[:HANDLES_COMMAND]->(:Command)`

- `(:Command)-[:EMITS_EVENT]->(:Event)`
- *(선택)* `(:Aggregate)-[:EMITS_EVENT]->(:Event)` (발신 주체를 명시적으로 함께 기록할 때)

- `(:Policy)-[:LISTENS_EVENT]->(:Event)`
- `(:Policy)-[:TRIGGERS_COMMAND]->(:Command)`

- `(:Event)-[:AFFECTS_AGGREGATE]->(:Aggregate)`

> 이 체인으로 아래 질문이 즉시 가능:
> - 이 이벤트는 어디서 나왔나?
> - 누가 듣고(Policy) 무엇을 트리거하나(Command)?
> - 결과적으로 어떤 Aggregate가 영향 받나?

---

### 2.5 Requirements ↔ 설계요소 Traceability (부분 수정의 기반)

- `(:Epic)-[:HAS_STORY]->(:UserStory)`
- `(:UserStory)-[:HAS_CRITERION]->(:AcceptanceCriterion)`

- `(:UserStory)-[:IMPACTS_AGGREGATE]->(:Aggregate)`
- `(:AcceptanceCriterion)-[:IMPACTS_FIELD]->(:Field)`

- `(:AcceptanceCriterion)-[:COVERS_COMMAND]->(:Command)`
- `(:AcceptanceCriterion)-[:COVERS_EVENT]->(:Event)`

> 이 링크들이 있으면:
> - “이 Story가 반영된 Aggregate는?”
> - “이 Field의 근거가 된 AC는?”
> - “이 Command/Event를 검증하는 AC는?”
> 를 그래프 질의로 바로 도출.

---

## 3. (권장) Trace 링크 속성 규칙

Trace 관계(예: `IMPACTS_AGGREGATE`, `IMPACTS_FIELD`, `COVERS_*`)에는 아래 속성 중 일부를 부여하는 것을 권장합니다.

- `confidence: float` (0~1)
- `rationale: string` (짧은 근거)
- `evidence_ref: string` (로그/파일 키)
- `created_at: datetime`

> v1에서는 `confidence`, `rationale`만 있어도 충분합니다.

---

## 4. (권장) ID 규칙 (Deterministic)

- Epic: `EP_{번호}` (예: `EP_001`)
- UserStory: `US_{번호}` (예: `US_001`)
- AcceptanceCriterion: `AC_{번호}` (예: `AC_001`)
- BoundedContext: `BC_{슬러그}` (예: `BC_ORDER`)
- Aggregate: `AGG_{슬러그}` (예: `AGG_ORDER`)
- Field: `F_{AGG}_{슬러그}` (예: `F_ORDER_AMOUNT`)
- Command/Event/Policy: `CMD_{슬러그}`, `EVT_{슬러그}`, `POL_{슬러그}`

---

## 5. (권장) 제약/인덱스 (Unique)

Neo4j 제약(예시):

```cypher
CREATE CONSTRAINT bc_id_unique IF NOT EXISTS
FOR (n:BoundedContext) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT agg_id_unique IF NOT EXISTS
FOR (n:Aggregate) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT field_id_unique IF NOT EXISTS
FOR (n:Field) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT cmd_id_unique IF NOT EXISTS
FOR (n:Command) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT evt_id_unique IF NOT EXISTS
FOR (n:Event) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT pol_id_unique IF NOT EXISTS
FOR (n:Policy) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT epic_id_unique IF NOT EXISTS
FOR (n:Epic) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT us_id_unique IF NOT EXISTS
FOR (n:UserStory) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT ac_id_unique IF NOT EXISTS
FOR (n:AcceptanceCriterion) REQUIRE n.id IS UNIQUE;
```

---

## 6. v1 운영 범위 정의 (MVP)

### 포함
- 위 노드/관계 전체
- `UserStory/AC` 기반 Trace 링크 생성
- ES 흐름(핵심 체인) 연결

### 제외(후순위)
- UI(Page/Component) 스키마
- 모든 중간 I/O(프롬프트/중간 추론) 완전 노드화
- 고급 버전 브랜치/머지(Workspace/Run은 v2에 정식 포함 가능)

---

## 7. v1 완성 정의(DoD)

- [ ] 위 Label/Relationship 목록이 문서로 확정되어 있으며, 그래프에 적용 가능한 상태
- [ ] `id` 유일 제약이 적용되어 중복 없이 MERGE 가능
- [ ] 최소 Trace 링크(`IMPACTS_AGGREGATE`, `IMPACTS_FIELD`, `COVERS_*`)가 생성되어 역추적 가능
- [ ] ES 체인(Command→Event→Policy→Command→Aggregate 영향)이 탐색 가능
