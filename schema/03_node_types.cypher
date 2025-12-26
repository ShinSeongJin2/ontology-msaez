// ============================================================
// Event Storming Impact Analysis - Node Types Definition
// ============================================================
// 각 노드 타입의 속성 정의 및 생성 패턴을 문서화합니다.
// ============================================================

// ############################################################
// 1. Requirement (요구사항)
// ############################################################
// 설명: 원본 요구사항 문서에서 추출한 요구사항
//
// 필수 속성:
//   - id: String (고유 식별자)
//   - title: String (요구사항 제목)
//
// 선택 속성:
//   - description: String (상세 설명)
//   - source: String (출처 문서명)
//   - createdAt: DateTime
//   - priority: String ("high", "medium", "low")
// ############################################################

CREATE (r:Requirement {
    id: "REQ-001",
    title: "주문 관리 기능",
    description: "고객이 주문을 생성, 조회, 취소할 수 있어야 한다",
    source: "기능요구사항서 v1.0",
    createdAt: datetime(),
    priority: "high"
});


// ############################################################
// 2. UserStory (사용자 스토리)
// ############################################################
// 설명: "As a [role], I want [action], so that [benefit]" 형식
// 관계: IMPLEMENTS → BoundedContext, Aggregate
//
// 필수 속성:
//   - id: String (고유 식별자)
//   - role: String (사용자 역할)
//   - action: String (원하는 행동)
//
// 선택 속성:
//   - benefit: String (기대 효과)
//   - priority: String
//   - status: String ("draft", "approved", "implemented")
//   - acceptanceCriteria: List<String>
// ############################################################

CREATE (us:UserStory {
    id: "US-001",
    role: "customer",
    action: "cancel my order",
    benefit: "I can get a refund if I change my mind",
    priority: "high",
    status: "approved"
});


// ############################################################
// 3. BoundedContext (바운디드 컨텍스트)
// ############################################################
// 설명: 전략적 설계 단위, 도메인의 논리적 경계
// 관계: 
//   - HAS_AGGREGATE → Aggregate
//   - HAS_POLICY → Policy
//   - DEPENDS_ON ↔ BoundedContext
//
// 필수 속성:
//   - id: String (고유 식별자)
//   - name: String (컨텍스트 이름)
//
// 선택 속성:
//   - description: String
//   - owner: String (담당 팀)
// ############################################################

CREATE (bc:BoundedContext {
    id: "BC-ORDER",
    name: "Order",
    description: "주문 생성, 수정, 취소 및 주문 상태 관리",
    owner: "Order Team"
});


// ############################################################
// 4. Aggregate (애그리게이트)
// ############################################################
// 설명: 전술적 설계 핵심, 트랜잭션 일관성 경계
// 관계:
//   - HAS_COMMAND → Command
//
// 필수 속성:
//   - id: String
//   - name: String
//
// 선택 속성:
//   - rootEntity: String
//   - invariants: List<String>
// ############################################################

CREATE (agg:Aggregate {
    id: "AGG-ORDER",
    name: "Order",
    rootEntity: "Order",
    invariants: [
        "주문 총액은 0보다 커야 함",
        "배송 시작 후에는 취소 불가"
    ]
});


// ############################################################
// 5. Command (커맨드)
// ############################################################
// 설명: 사용자의 의도를 표현하는 명령
// 관계:
//   - EMITS → Event
//
// 필수 속성:
//   - id: String
//   - name: String (동사형)
//
// 선택 속성:
//   - actor: String
//   - inputSchema: String (JSON)
// ############################################################

CREATE (cmd:Command {
    id: "CMD-CANCEL-ORDER",
    name: "CancelOrder",
    actor: "customer",
    inputSchema: '{"orderId": "string", "reason": "string"}'
});


// ############################################################
// 6. Event (이벤트)
// ############################################################
// 설명: 도메인에서 발생한 사실 (과거형)
// 관계:
//   - TRIGGERS → Policy (다른 BC의 Policy)
//
// 필수 속성:
//   - id: String
//   - name: String (과거형)
//   - version: String
//
// 선택 속성:
//   - schema: String (JSON)
//   - isBreaking: Boolean
// ############################################################

CREATE (evt:Event {
    id: "EVT-ORDER-CANCELLED",
    name: "OrderCancelled",
    version: "1.0.0",
    schema: '{"orderId": "string", "cancelledAt": "datetime", "reason": "string"}',
    isBreaking: false
});


// ############################################################
// 7. Policy (폴리시)
// ############################################################
// 설명: 다른 BC의 이벤트에 반응하여 자신의 Command를 호출
//       "When [Event] then [Command]" 패턴
// 관계:
//   - INVOKES → Command (자신의 BC에 있는)
//
// 필수 속성:
//   - id: String
//   - name: String
//
// 선택 속성:
//   - condition: String (트리거 조건)
//   - description: String
// ############################################################

CREATE (pol:Policy {
    id: "POL-REFUND-ON-CANCEL",
    name: "RefundOnOrderCancellation",
    condition: "OrderCancelled received",
    description: "주문 취소 이벤트 수신 시 환불 처리"
});
