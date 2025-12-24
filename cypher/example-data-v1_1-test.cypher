// Zero-base SDD v1.1 — minimal test dataset for impact/dirty/change workflows
// Safe to re-run (uses MERGE). Adjust IDs if needed.

// ------------------------------
// Requirements
// ------------------------------
MERGE (ep:Epic {id:'EP_001'})
SET ep.title='주문 처리 자동화', ep.description='테스트용 에픽', ep.priority='P1', ep.status='draft';

MERGE (us:UserStory {id:'US_001'})
SET us.title='고객이 주문을 생성한다',
    us.storyText='As a customer, I want to create an order so that I can purchase items.',
    us.priority='high',
    us.status='draft',
    us.source_hash='H1';

MERGE (ac:AcceptanceCriterion {id:'AC_001'})
SET ac.title='주문 금액은 0보다 커야 한다',
    ac.criterionText='amount > 0',
    ac.testType='rule',
    ac.status='draft',
    ac.source_hash='H1';

MATCH (ep:Epic {id:'EP_001'}), (us:UserStory {id:'US_001'})
MERGE (ep)-[:HAS_STORY]->(us);

MATCH (us:UserStory {id:'US_001'}), (ac:AcceptanceCriterion {id:'AC_001'})
MERGE (us)-[:HAS_CRITERION]->(ac);

// ------------------------------
// Domain: BC / Aggregates / Fields
// ------------------------------
MERGE (bc:BoundedContext {id:'BC_ORDER'})
SET bc.name='Order', bc.description='주문 BC', bc.domain='Order', bc.kind='core', bc.status='draft', bc.version=1;

MERGE (aggOrder:Aggregate {id:'AGG_ORDER'})
SET aggOrder.name='Order', aggOrder.description='주문 Aggregate', aggOrder.kind='root', aggOrder.version=1, aggOrder.status='draft';

MERGE (aggStock:Aggregate {id:'AGG_STOCK'})
SET aggStock.name='Stock', aggStock.description='재고 Aggregate', aggStock.kind='root', aggStock.version=1, aggStock.status='draft';

MATCH (bc:BoundedContext {id:'BC_ORDER'}), (aggOrder:Aggregate {id:'AGG_ORDER'})
MERGE (bc)-[:HAS_AGGREGATE]->(aggOrder);

MATCH (bc:BoundedContext {id:'BC_ORDER'}), (aggStock:Aggregate {id:'AGG_STOCK'})
MERGE (bc)-[:HAS_AGGREGATE]->(aggStock);

// Fields
MERGE (fAmount:Field {id:'F_ORDER_AMOUNT'})
SET fAmount.name='amount', fAmount.type='Money', fAmount.isKey=false, fAmount.isNullable=false, fAmount.isForeignKey=false, fAmount.description='주문 금액';

MERGE (fOrderId:Field {id:'F_ORDER_ID'})
SET fOrderId.name='orderId', fOrderId.type='UUID', fOrderId.isKey=true, fOrderId.isNullable=false, fOrderId.isForeignKey=false, fOrderId.description='주문 ID';

MATCH (aggOrder:Aggregate {id:'AGG_ORDER'}), (fOrderId:Field {id:'F_ORDER_ID'})
MERGE (aggOrder)-[:HAS_FIELD]->(fOrderId);

MATCH (aggOrder:Aggregate {id:'AGG_ORDER'}), (fAmount:Field {id:'F_ORDER_AMOUNT'})
MERGE (aggOrder)-[:HAS_FIELD]->(fAmount);

// ------------------------------
// Traceability (requirements -> design/behavior)
// ------------------------------
MATCH (us:UserStory {id:'US_001'}), (aggOrder:Aggregate {id:'AGG_ORDER'})
MERGE (us)-[r1:IMPACTS_AGGREGATE]->(aggOrder)
ON CREATE SET r1.confidence=0.9, r1.rationale='스토리가 주문 생성/관리 책임을 요구', r1.created_at=datetime()
ON MATCH SET r1.confidence=0.9, r1.rationale='스토리가 주문 생성/관리 책임을 요구', r1.created_at=datetime();

MATCH (ac:AcceptanceCriterion {id:'AC_001'}), (fAmount:Field {id:'F_ORDER_AMOUNT'})
MERGE (ac)-[r2:IMPACTS_FIELD]->(fAmount)
ON CREATE SET r2.confidence=1.0, r2.rationale='인수조건이 amount 검증 요구', r2.created_at=datetime()
ON MATCH SET r2.confidence=1.0, r2.rationale='인수조건이 amount 검증 요구', r2.created_at=datetime();

// ------------------------------
// Event Storming: Command / Event / Policy chain
// ------------------------------
MERGE (cmdPlace:Command {id:'CMD_PLACE_ORDER'})
SET cmdPlace.name='PlaceOrder', cmdPlace.description='주문 생성', cmdPlace.syncMode='sync', cmdPlace.source='API';

MERGE (evtPlaced:Event {id:'EVT_ORDER_PLACED'})
SET evtPlaced.name='OrderPlaced', evtPlaced.description='주문 생성됨', evtPlaced.category='DomainEvent', evtPlaced.reliability='at-least-once';

MERGE (polReserve:Policy {id:'POL_RESERVE_STOCK'})
SET polReserve.name='ReserveStockOnOrderPlaced', polReserve.description='주문 생성 시 재고 예약', polReserve.kind='saga', polReserve.conditionExpr='on EVT_ORDER_PLACED';

MERGE (cmdReserve:Command {id:'CMD_RESERVE_STOCK'})
SET cmdReserve.name='ReserveStock', cmdReserve.description='재고 예약', cmdReserve.syncMode='async', cmdReserve.source='Policy';

// ES structural links
MATCH (aggOrder:Aggregate {id:'AGG_ORDER'}), (cmdPlace:Command {id:'CMD_PLACE_ORDER'})
MERGE (aggOrder)-[:HANDLES_COMMAND]->(cmdPlace);

MATCH (cmdPlace:Command {id:'CMD_PLACE_ORDER'}), (evtPlaced:Event {id:'EVT_ORDER_PLACED'})
MERGE (cmdPlace)-[:EMITS_EVENT]->(evtPlaced);

MATCH (polReserve:Policy {id:'POL_RESERVE_STOCK'}), (evtPlaced:Event {id:'EVT_ORDER_PLACED'})
MERGE (polReserve)-[:LISTENS_EVENT]->(evtPlaced);

MATCH (polReserve:Policy {id:'POL_RESERVE_STOCK'}), (cmdReserve:Command {id:'CMD_RESERVE_STOCK'})
MERGE (polReserve)-[:TRIGGERS_COMMAND]->(cmdReserve);

MATCH (evtPlaced:Event {id:'EVT_ORDER_PLACED'}), (aggStock:Aggregate {id:'AGG_STOCK'})
MERGE (evtPlaced)-[:AFFECTS_AGGREGATE]->(aggStock);

// Traceability to behavior (AC covers)
MATCH (ac:AcceptanceCriterion {id:'AC_001'}), (cmdPlace:Command {id:'CMD_PLACE_ORDER'})
MERGE (ac)-[r3:COVERS_COMMAND]->(cmdPlace)
ON CREATE SET r3.confidence=0.95, r3.rationale='인수조건은 주문 생성 커맨드 검증에 연결', r3.created_at=datetime()
ON MATCH SET r3.confidence=0.95, r3.rationale='인수조건은 주문 생성 커맨드 검증에 연결', r3.created_at=datetime();

MATCH (ac:AcceptanceCriterion {id:'AC_001'}), (evtPlaced:Event {id:'EVT_ORDER_PLACED'})
MERGE (ac)-[r4:COVERS_EVENT]->(evtPlaced)
ON CREATE SET r4.confidence=0.9, r4.rationale='인수조건은 OrderPlaced 이벤트 시나리오에 포함', r4.created_at=datetime()
ON MATCH SET r4.confidence=0.9, r4.rationale='인수조건은 OrderPlaced 이벤트 시나리오에 포함', r4.created_at=datetime();

// ------------------------------
// Optional: clean dirty flags for a fresh start
// ------------------------------
MATCH (n)
WHERE n.dirty = true
REMOVE n.dirty, n.dirty_reason, n.dirty_at;
