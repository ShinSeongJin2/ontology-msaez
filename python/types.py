"""
Zero-base SDD v1 — 타입 정의
스펙 문서(spec/spec-v1.md)에 정의된 노드와 관계의 타입 정의
"""

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ==========================================
# Enums
# ==========================================

class BoundedContextKind(str, Enum):
    CORE = "core"
    SUPPORTING = "supporting"
    GENERIC = "generic"


class Status(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    DEPRECATED = "deprecated"


class CommandSyncMode(str, Enum):
    SYNC = "sync"
    ASYNC = "async"


class EventCategory(str, Enum):
    DOMAIN_EVENT = "DomainEvent"
    INTEGRATION_EVENT = "IntegrationEvent"


class PolicyKind(str, Enum):
    SAGA = "saga"
    PROCESS_MANAGER = "process-manager"
    RULE = "rule"


class TestType(str, Enum):
    EXAMPLE = "example"
    SCENARIO = "scenario"
    RULE = "rule"


# ==========================================
# Node Types
# ==========================================

@dataclass
class BoundedContext:
    id: str
    name: str
    description: str
    domain: str
    kind: str
    status: str = "draft"
    version: int = 1
    source_hash: Optional[str] = None


@dataclass
class Aggregate:
    id: str
    name: str
    description: str
    kind: str = "root"
    version: int = 1
    status: str = "draft"
    source_hash: Optional[str] = None


@dataclass
class Entity:
    id: str
    name: str
    description: str
    status: str = "draft"
    version: int = 1


@dataclass
class ValueObject:
    id: str
    name: str
    description: str
    status: str = "draft"
    version: int = 1


@dataclass
class Field:
    id: str
    name: str
    type: str
    isKey: bool = False
    isNullable: bool = False
    isForeignKey: bool = False
    description: str = ""
    source_hash: Optional[str] = None


@dataclass
class Command:
    id: str
    name: str
    description: str
    syncMode: str = "sync"
    source: str = "API"
    template_key: Optional[str] = None


@dataclass
class Event:
    id: str
    name: str
    description: str
    category: str = "DomainEvent"
    reliability: str = "at-least-once"
    payload_schema_ref: Optional[str] = None


@dataclass
class Policy:
    id: str
    name: str
    description: str
    kind: str = "rule"
    conditionExpr: str = ""


@dataclass
class Epic:
    id: str
    title: str
    description: str
    priority: str = "medium"
    status: str = "draft"


@dataclass
class UserStory:
    id: str
    title: str
    storyText: str
    priority: str = "medium"
    status: str = "draft"
    asIs: Optional[str] = None
    toBe: Optional[str] = None
    semantic_text: Optional[str] = None
    keywords: List[str] = field(default_factory=list)


@dataclass
class AcceptanceCriterion:
    id: str
    title: str
    criterionText: str
    testType: str = "scenario"
    status: str = "draft"
    semantic_text: Optional[str] = None
    keywords: List[str] = field(default_factory=list)


# ==========================================
# Relationship Types
# ==========================================

@dataclass
class TraceRelationship:
    """Trace 관계의 공통 속성"""
    confidence: float = 1.0
    rationale: str = ""
    evidence_ref: Optional[str] = None
    created_at: Optional[datetime] = None


# ==========================================
# Run Metadata (운영 메타)
# ==========================================

@dataclass
class Run:
    """실행 메타데이터 노드 (어떤 에이전트 실행이 무엇을 만들었는지 추적)"""
    id: str
    run_type: str  # "schema_init", "upsert", "trace_link", "regen" 등
    prompt_version: Optional[str] = None
    model: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    status: str = "running"  # "running", "completed", "failed"
    metadata: dict = field(default_factory=dict)  # 추가 메타데이터


@dataclass
class Change:
    """변경 감지 노드 (v1.1)"""
    id: str
    at: datetime
    reason: str
    label: str
    node_id: str
    before_hash: Optional[str] = None
    after_hash: str = ""


# 관계 타입은 Neo4j에서 직접 사용되므로 별도 클래스로 정의하지 않음
# 대신 관계 속성은 dict로 관리
RELATIONSHIPS = {
    # BC ↔ Aggregate
    "HAS_AGGREGATE": {},
    
    # Aggregate 내부 구조
    "HAS_ENTITY": {},
    "HAS_VALUE_OBJECT": {},
    "HAS_FIELD": {},
    
    # Aggregate 간 참조
    "REFERS_TO_AGGREGATE": {"viaField": str},
    "REFERS_TO_FIELD": {},
    
    # Event Storming
    "HANDLES_COMMAND": {},
    "EMITS_EVENT": {},
    "LISTENS_EVENT": {},
    "TRIGGERS_COMMAND": {},
    "AFFECTS_AGGREGATE": {},
    
    # Requirements 구조
    "HAS_STORY": {},
    "HAS_CRITERION": {},
    
    # Traceability (속성 있음)
    "IMPACTS_AGGREGATE": {"confidence": float, "rationale": str},
    "IMPACTS_FIELD": {"confidence": float, "rationale": str},
    "COVERS_COMMAND": {"confidence": float, "rationale": str},
    "COVERS_EVENT": {"confidence": float, "rationale": str},
}

