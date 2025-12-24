// Zero-base SDD v1 — Neo4j Graph Schema Initialization
// 이 스크립트는 스펙 문서(spec/spec-v1.md)에 정의된 모든 노드 레이블과 관계에 대한
// 제약 조건(Unique Constraints)을 생성합니다.

// ==========================================
// 1. BoundedContext 제약 조건
// ==========================================
CREATE CONSTRAINT bc_id_unique IF NOT EXISTS
FOR (n:BoundedContext) REQUIRE n.id IS UNIQUE;

// ==========================================
// 2. Aggregate 제약 조건
// ==========================================
CREATE CONSTRAINT agg_id_unique IF NOT EXISTS
FOR (n:Aggregate) REQUIRE n.id IS UNIQUE;

// ==========================================
// 3. Entity 제약 조건
// ==========================================
CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
FOR (n:Entity) REQUIRE n.id IS UNIQUE;

// ==========================================
// 4. ValueObject 제약 조건
// ==========================================
CREATE CONSTRAINT vo_id_unique IF NOT EXISTS
FOR (n:ValueObject) REQUIRE n.id IS UNIQUE;

// ==========================================
// 5. Field 제약 조건
// ==========================================
CREATE CONSTRAINT field_id_unique IF NOT EXISTS
FOR (n:Field) REQUIRE n.id IS UNIQUE;

// ==========================================
// 6. Command 제약 조건
// ==========================================
CREATE CONSTRAINT cmd_id_unique IF NOT EXISTS
FOR (n:Command) REQUIRE n.id IS UNIQUE;

// ==========================================
// 7. Event 제약 조건
// ==========================================
CREATE CONSTRAINT evt_id_unique IF NOT EXISTS
FOR (n:Event) REQUIRE n.id IS UNIQUE;

// ==========================================
// 8. Policy 제약 조건
// ==========================================
CREATE CONSTRAINT pol_id_unique IF NOT EXISTS
FOR (n:Policy) REQUIRE n.id IS UNIQUE;

// ==========================================
// 9. Epic 제약 조건
// ==========================================
CREATE CONSTRAINT epic_id_unique IF NOT EXISTS
FOR (n:Epic) REQUIRE n.id IS UNIQUE;

// ==========================================
// 10. UserStory 제약 조건
// ==========================================
CREATE CONSTRAINT us_id_unique IF NOT EXISTS
FOR (n:UserStory) REQUIRE n.id IS UNIQUE;

// ==========================================
// 11. AcceptanceCriterion 제약 조건
// ==========================================
CREATE CONSTRAINT ac_id_unique IF NOT EXISTS
FOR (n:AcceptanceCriterion) REQUIRE n.id IS UNIQUE;

// ==========================================
// 12. Run (운영 메타) 제약 조건
// ==========================================
CREATE CONSTRAINT run_id_unique IF NOT EXISTS
FOR (n:Run) REQUIRE n.id IS UNIQUE;

// ==========================================
// 13. Change (변경 감지) 제약 조건 (v1.1)
// ==========================================
CREATE CONSTRAINT change_id_unique IF NOT EXISTS
FOR (n:Change) REQUIRE n.id IS UNIQUE;

// ==========================================
// 인덱스 생성 (선택 사항, 성능 향상을 위해)
// ==========================================
CREATE INDEX bc_name_index IF NOT EXISTS FOR (n:BoundedContext) ON (n.name);
CREATE INDEX agg_name_index IF NOT EXISTS FOR (n:Aggregate) ON (n.name);
CREATE INDEX cmd_name_index IF NOT EXISTS FOR (n:Command) ON (n.name);
CREATE INDEX evt_name_index IF NOT EXISTS FOR (n:Event) ON (n.name);
CREATE INDEX epic_title_index IF NOT EXISTS FOR (n:Epic) ON (n.title);
CREATE INDEX us_title_index IF NOT EXISTS FOR (n:UserStory) ON (n.title);

