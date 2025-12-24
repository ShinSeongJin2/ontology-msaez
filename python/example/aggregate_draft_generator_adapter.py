"""
Zero-base SDD v1.2 — Aggregate Draft Generator Adapter (v0)

목적:
- 기존(레거시) Aggregate Draft Generator를 "Neo4j SoT + Context Builder" 구조에 끼워 넣기 위한 얇은 어댑터.
- 생성기 로직은 그대로 두고, 입력/출력만 SDD 계약에 맞춰 변환한다.

주의:
- 본 파일은 "뼈대"이며, 레거시 생성기 import 경로/시그니처에 맞게 연결하세요.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, List
from neo4j import GraphDatabase

from regeneration_context_builder import RegenerationContextBuilder


class AggregateDraftGeneratorAdapter:
    def __init__(self, uri: str, user: str, password: str):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.ctx_builder = RegenerationContextBuilder(uri, user, password)
        # TODO: 레거시 생성기 연결
        # from python.project_generator.workflows.aggregate_draft.aggregate_draft_generator import AggregateDraftGenerator
        # self.legacy = AggregateDraftGenerator(...)

    def close(self):
        self.ctx_builder.close()
        self.driver.close()

    def run(self, root_story_id: str, dirty_node_ids: List[str], project_id: Optional[str] = None) -> Dict[str, Any]:
        ctx = self.ctx_builder.build_phase_a_aggregate_context(
            root_story_id=root_story_id,
            dirty_node_ids=dirty_node_ids,
            project_id=project_id,
            mode="dirty",
        )

        # TODO: 레거시 생성기 실행 (예: self.legacy.generate(ctx))
        # 임시 더미 출력: 기존 스냅샷을 그대로 반환
        snap = ctx["context"]["existing_aggregate_snapshot"]
        aggregates = snap if isinstance(snap, list) else [snap]

        return {
            "input_context": ctx,
            "output": {
                "aggregates": aggregates,
                "trace": {
                    "story_to_aggregate": [
                        {"story_id": root_story_id, "agg_id": a.get("id"), "confidence": 0.9}
                        for a in aggregates
                        if a and a.get("id")
                    ],
                    "ac_to_field": [],
                },
            },
        }
