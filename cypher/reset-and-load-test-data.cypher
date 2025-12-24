// Zero-base SDD v1.1 — reset + load test data + apply Element label
// Purpose: start from a clean slate (for test prefixes) and insert the minimal dataset.
// Safe to re-run.

// ------------------------------
// A) Cleanup (test prefixes only)
// ------------------------------
MATCH (n)
WHERE n.id STARTS WITH 'EP_' OR n.id STARTS WITH 'US_' OR n.id STARTS WITH 'AC_'
   OR n.id STARTS WITH 'BC_' OR n.id STARTS WITH 'AGG_' OR n.id STARTS WITH 'ENT_' OR n.id STARTS WITH 'VO_' OR n.id STARTS WITH 'F_'
   OR n.id STARTS WITH 'CMD_' OR n.id STARTS WITH 'EVT_' OR n.id STARTS WITH 'POL_'
DETACH DELETE n;

// Optional: clean Change/Run produced by tests (uncomment if you want a fully clean test DB)
// MATCH (n:Change) DETACH DELETE n;
// MATCH (n:Run) DETACH DELETE n;

// ------------------------------
// B) Insert minimal test dataset
// ------------------------------

// Requirements
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

MERGE (ep)-[:HAS_STORY]->(us);
MERGE (us)-[:HAS_CRITERION]->(ac);

// Domain: BC / Aggregates / Fields
MERGE (bc:BoundedContext {id:'BC_ORDER'})
SET bc.name='Order', bc.description='주문 BC', bc.domain='Order', bc.kind='core', bc.status='draft', bc.version=1;

MERGE (aggOrder:Aggregate {id:'AGG_ORDER'})
SET aggOrder.name='Order', aggOrder.description='주문 Aggregate', aggOrder.kind='root', aggOrder.version=1, aggOrder.status='draft';

MERGE (aggStock:Aggregate {id:'AGG_STOCK'})
SET aggStock.name='Stock', aggStock.description='재고 Aggregate', aggStock.kind='root', aggStock.version=1, aggStock.status='draft';

MERGE (bc)-[:HAS_AGGREGATE]->(aggOrder);
MERGE (bc)-[:HAS_AGGREGATE]->(aggStock);

// Fields
MERGE (fAmount:Field {id:'F_ORDER_AMOUNT'})
SET fAmount.name='amount', fAmount.type='Money', fAmount.isKey=false, fAmount.isNullable=false, fAmount.isForeignKey=false, fAmount.description='주문 금액';

MERGE (fOrderId:Field {id:'F_ORDER_ID'})
SET fOrderId.name='orderId', fOrderId.type='UUID', fOrderId.isKey=true, fOrderId.isNullable=false, fOrderId.isForeignKey=false, fOrderId.description='주문 ID';

MERGE (aggOrder)-[:HAS_FIELD]->(fOrderId);
MERGE (aggOrder)-[:HAS_FIELD]->(fAmount);

// Traceability (requirements -> design/behavior)
MERGE (us)-[r1:IMPACTS_AGGREGATE]->(aggOrder)
SET r1.confidence=0.9, r1.rationale='스토리가 주문 생성/관리 책임을 요구', r1.created_at=datetime();

MERGE (ac)-[r2:IMPACTS_FIELD]->(fAmount)
SET r2.confidence=1.0, r2.rationale='인수조건이 amount 검증 요구', r2.created_at=datetime();

// Event Storming: Command / Event / Policy chain
MERGE (cmdPlace:Command {id:'CMD_PLACE_ORDER'})
SET cmdPlace.name='PlaceOrder', cmdPlace.description='주문 생성', cmdPlace.syncMode='sync', cmdPlace.source='API';

MERGE (evtPlaced:Event {id:'EVT_ORDER_PLACED'})
SET evtPlaced.name='OrderPlaced', evtPlaced.description='주문 생성됨', evtPlaced.category='DomainEvent', evtPlaced.reliability='at-least-once';

MERGE (polReserve:Policy {id:'POL_RESERVE_STOCK'})
SET polReserve.name='ReserveStockOnOrderPlaced', polReserve.description='주문 생성 시 재고 예약', polReserve.kind='saga', polReserve.conditionExpr='on EVT_ORDER_PLACED';

MERGE (cmdReserve:Command {id:'CMD_RESERVE_STOCK'})
SET cmdReserve.name='ReserveStock', cmdReserve.description='재고 예약', cmdReserve.syncMode='async', cmdReserve.source='Policy';

// ES structural links
MERGE (aggOrder)-[:HANDLES_COMMAND]->(cmdPlace);
MERGE (cmdPlace)-[:EMITS_EVENT]->(evtPlaced);
MERGE (polReserve)-[:LISTENS_EVENT]->(evtPlaced);
MERGE (polReserve)-[:TRIGGERS_COMMAND]->(cmdReserve);
MERGE (evtPlaced)-[:AFFECTS_AGGREGATE]->(aggStock);

// Traceability to behavior (AC covers)
MERGE (ac)-[r3:COVERS_COMMAND]->(cmdPlace)
SET r3.confidence=0.95, r3.rationale='인수조건은 주문 생성 커맨드 검증에 연결', r3.created_at=datetime();

MERGE (ac)-[r4:COVERS_EVENT]->(evtPlaced)
SET r4.confidence=0.9, r4.rationale='인수조건은 OrderPlaced 이벤트 시나리오에 포함', r4.created_at=datetime();

// ------------------------------
// C) Apply :Element label for Neo4j Browser colorization
// ------------------------------
MATCH (n:BoundedContext) SET n:Element;
MATCH (n:Aggregate)      SET n:Element;
MATCH (n:Entity)         SET n:Element;
MATCH (n:ValueObject)    SET n:Element;
MATCH (n:Field)          SET n:Element;
MATCH (n:Command)        SET n:Element;
MATCH (n:Event)          SET n:Element;
MATCH (n:Policy)         SET n:Element;

// ------------------------------
// D) Ensure dirty flags are clean at start
// ------------------------------
MATCH (n)
WHERE n.dirty = true
REMOVE n.dirty, n.dirty_reason, n.dirty_at;
