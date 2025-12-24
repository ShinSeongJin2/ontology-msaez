// Zero-base SDD v1 — 예제 데이터 삽입
// 스펙 문서에 정의된 스키마를 사용한 샘플 데이터

// ==========================================
// 1. Requirements 계층 구조 예제
// ==========================================

// Epic
MERGE (ep1:Epic {
  id: 'EP_001',
  title: '주문 관리 시스템',
  description: '고객이 상품을 주문하고 결제할 수 있는 시스템',
  priority: 'high',
  status: 'confirmed'
});

// UserStory
MERGE (us1:UserStory {
  id: 'US_001',
  title: '주문 생성',
  storyText: 'As a customer, I want to create an order so that I can purchase products',
  priority: 'high',
  status: 'confirmed'
});

// AcceptanceCriterion
MERGE (ac1:AcceptanceCriterion {
  id: 'AC_001',
  title: '주문 생성 시 총액 계산',
  criterionText: '주문 생성 시 모든 상품의 가격 합계가 정확히 계산되어야 함',
  testType: 'scenario',
  status: 'confirmed'
});

// Requirements 관계
MERGE (ep1)-[:HAS_STORY]->(us1);
MERGE (us1)-[:HAS_CRITERION]->(ac1);

// ==========================================
// 2. Bounded Context 및 Aggregate 예제
// ==========================================

MERGE (bc1:BoundedContext {
  id: 'BC_ORDER',
  name: 'Order',
  description: '주문 도메인 컨텍스트',
  domain: 'Order',
  kind: 'core',
  status: 'confirmed',
  version: 1
});

MERGE (agg1:Aggregate {
  id: 'AGG_ORDER',
  name: 'Order',
  description: '주문 집합체',
  kind: 'root',
  version: 1,
  status: 'confirmed'
});

MERGE (bc1)-[:HAS_AGGREGATE]->(agg1);

// ==========================================
// 3. Field 예제
// ==========================================

MERGE (f1:Field {
  id: 'F_ORDER_AMOUNT',
  name: 'amount',
  type: 'Money',
  isKey: false,
  isNullable: false,
  isForeignKey: false,
  description: '주문 총액'
});

MERGE (f2:Field {
  id: 'F_ORDER_CUSTOMER_ID',
  name: 'customerId',
  type: 'UUID',
  isKey: false,
  isNullable: false,
  isForeignKey: true,
  description: '고객 ID (외래키)'
});

MERGE (agg1)-[:HAS_FIELD]->(f1);
MERGE (agg1)-[:HAS_FIELD]->(f2);

// ==========================================
// 4. Command 및 Event 예제
// ==========================================

MERGE (cmd1:Command {
  id: 'CMD_CREATE_ORDER',
  name: 'CreateOrder',
  description: '주문 생성 명령',
  syncMode: 'sync',
  source: 'API'
});

MERGE (evt1:Event {
  id: 'EVT_ORDER_CREATED',
  name: 'OrderCreated',
  description: '주문이 생성되었음을 알리는 이벤트',
  category: 'DomainEvent',
  reliability: 'at-least-once'
});

MERGE (agg1)-[:HANDLES_COMMAND]->(cmd1);
MERGE (cmd1)-[:EMITS_EVENT]->(evt1);
MERGE (evt1)-[:AFFECTS_AGGREGATE]->(agg1);

// ==========================================
// 5. Traceability 링크 예제
// ==========================================

MERGE (us1)-[r1:IMPACTS_AGGREGATE {
  confidence: 0.9,
  rationale: '주문 생성 스토리는 Order Aggregate에 직접 영향'
}]->(agg1);

MERGE (ac1)-[r2:IMPACTS_FIELD {
  confidence: 1.0,
  rationale: '총액 계산 기준은 amount 필드와 직접 연관'
}]->(f1);

MERGE (ac1)-[r3:COVERS_COMMAND {
  confidence: 0.95,
  rationale: '주문 생성 기준은 CreateOrder 명령을 검증'
}]->(cmd1);

MERGE (ac1)-[r4:COVERS_EVENT {
  confidence: 0.9,
  rationale: '주문 생성 기준은 OrderCreated 이벤트를 검증'
}]->(evt1);

