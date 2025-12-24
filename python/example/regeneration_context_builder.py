"""
Zero-base SDD v1.2 — Regeneration Context Builder (v0)

역할:
- Neo4j(SoT)에서 root Story/AC, dirty 집합, 인접 컨텍스트(BC 힌트, 기존 Aggregate 스냅샷 등)를 조회
- 기존(레거시) 생성기가 사용할 수 있는 "입력 계약(JSON)"을 구성

주의:
- 이 모듈은 "생성(LLM 호출/초안 생성)"을 하지 않습니다.
- 오직 "컨텍스트 구성"만 수행합니다.

입력(권장):
- project_id (optional)
- root_story_id (required)
- dirty_node_ids OR dirty dict (Aggregate/Field/Command/Event/Policy 등)
- phase ('A'|'B'), mode ('full'|'dirty')

출력(Phase A Aggregate Draft 용 최소 계약):
{
  "project_id": "...",
  "root_story_id": "US_001",
  "phase": "A",
  "mode": "dirty",
  "dirty": {
    "Aggregate": ["AGG_ORDER"],
    "Field": ["F_ORDER_AMOUNT"]
  },
  "requirements": {
    "story": {...},
    "criteria": [...]
  },
  "context": {
    "bounded_context_hint": "Order",
    "existing_aggregate_snapshot": {...},
    "related_aggregates_in_bc": [...]
  },
  "explain": {...}   # optional (debug)
}
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set
from neo4j import GraphDatabase


def _unique(seq: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _group_dirty_ids(dirty_node_ids: List[str]) -> Dict[str, List[str]]:
    """id prefix 기반으로 dirty를 라벨 그룹으로 나눔."""
    groups: Dict[str, List[str]] = {
        "Aggregate": [],
        "Field": [],
        "Command": [],
        "Event": [],
        "Policy": [],
        "BoundedContext": [],
        "Entity": [],
        "ValueObject": [],
        "Unknown": [],
    }
    for nid in dirty_node_ids or []:
        if nid.startswith("AGG_"):
            groups["Aggregate"].append(nid)
        elif nid.startswith("F_"):
            groups["Field"].append(nid)
        elif nid.startswith("CMD_"):
            groups["Command"].append(nid)
        elif nid.startswith("EVT_"):
            groups["Event"].append(nid)
        elif nid.startswith("POL_"):
            groups["Policy"].append(nid)
        elif nid.startswith("BC_"):
            groups["BoundedContext"].append(nid)
        elif nid.startswith("ENT_"):
            groups["Entity"].append(nid)
        elif nid.startswith("VO_"):
            groups["ValueObject"].append(nid)
        else:
            groups["Unknown"].append(nid)
    for k in list(groups.keys()):
        groups[k] = _unique(groups[k])
    return groups


@dataclass
class BuilderConfig:
    max_neighbor_hops: int = 1
    include_explain: bool = True


class RegenerationContextBuilder:
    def __init__(self, uri: str, user: str, password: str, config: Optional[BuilderConfig] = None):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.config = config or BuilderConfig()

    def close(self) -> None:
        self.driver.close()

    def build_phase_a_aggregate_context(
        self,
        root_story_id: str,
        dirty_node_ids: Optional[List[str]] = None,
        dirty: Optional[Dict[str, List[str]]] = None,
        project_id: Optional[str] = None,
        mode: str = "dirty",
    ) -> Dict[str, Any]:
        if not root_story_id:
            raise ValueError("root_story_id is required")

        dirty_groups = dirty if dirty is not None else _group_dirty_ids(dirty_node_ids or [])
        target_agg_ids = dirty_groups.get("Aggregate", [])
        target_field_ids = dirty_groups.get("Field", [])

        story = self._get_story(root_story_id)
        criteria = self._get_criteria(root_story_id)

        if not target_agg_ids:
            target_agg_ids = self._get_impacted_aggregates_by_story(root_story_id)

        existing_snapshots = [self._get_aggregate_snapshot(agg_id) for agg_id in target_agg_ids]
        bc_hint = self._infer_bc_hint_from_aggregates(target_agg_ids) or self._infer_bc_hint_from_story(root_story_id)

        related_in_bc = self._get_aggregates_in_bc(bc_hint) if bc_hint else []

        explain: Dict[str, Any] = {}
        if self.config.include_explain:
            explain = {
                "dirty_groups": dirty_groups,
                "fallback_used": {
                    "aggregates_from_story_impact": (dirty_groups.get("Aggregate") in (None, [],) and bool(target_agg_ids)),
                },
                "bc_hint_source": "from_aggregates_or_story",
                "story_id": root_story_id,
            }

        return {
            "project_id": project_id,
            "root_story_id": root_story_id,
            "phase": "A",
            "mode": mode,
            "dirty": {
                "Aggregate": _unique(target_agg_ids),
                "Field": _unique(target_field_ids),
            },
            "requirements": {
                "story": story,
                "criteria": criteria,
            },
            "context": {
                "bounded_context_hint": bc_hint,
                "existing_aggregate_snapshot": existing_snapshots[0] if len(existing_snapshots) == 1 else existing_snapshots,
                "related_aggregates_in_bc": related_in_bc,
            },
            "explain": explain,
        }

    # -----------------------------
    # Queries
    # -----------------------------
    def _get_story(self, story_id: str) -> Dict[str, Any]:
        with self.driver.session() as session:
            rec = session.run(
                """
                MATCH (us:UserStory {id:$id})
                RETURN us.id AS id,
                       us.title AS title,
                       us.storyText AS storyText,
                       us.priority AS priority,
                       us.status AS status,
                       us.source_hash AS source_hash,
                       us.semantic_text AS semantic_text,
                       us.keywords AS keywords
                """,
                id=story_id,
            ).single()
            if not rec:
                raise ValueError(f"UserStory not found: {story_id}")
            data = dict(rec.data())
            if data.get("keywords") is None:
                data["keywords"] = []
            return data

    def _get_criteria(self, story_id: str) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            res = session.run(
                """
                MATCH (us:UserStory {id:$id})-[:HAS_CRITERION]->(ac:AcceptanceCriterion)
                RETURN ac.id AS id,
                       ac.title AS title,
                       ac.criterionText AS criterionText,
                       ac.testType AS testType,
                       ac.status AS status,
                       ac.source_hash AS source_hash,
                       ac.semantic_text AS semantic_text,
                       ac.keywords AS keywords
                ORDER BY ac.id
                """,
                id=story_id,
            )
            out: List[Dict[str, Any]] = []
            for r in res:
                d = r.data()
                if d.get("keywords") is None:
                    d["keywords"] = []
                out.append(d)
            return out

    def _get_impacted_aggregates_by_story(self, story_id: str) -> List[str]:
        with self.driver.session() as session:
            res = session.run(
                """
                MATCH (us:UserStory {id:$id})-[:IMPACTS_AGGREGATE]->(a:Aggregate)
                RETURN DISTINCT a.id AS id
                ORDER BY id
                """,
                id=story_id,
            )
            return [r["id"] for r in res if r.get("id")]

    def _get_aggregate_snapshot(self, agg_id: str) -> Dict[str, Any]:
        with self.driver.session() as session:
            rec = session.run(
                """
                MATCH (a:Aggregate {id:$id})
                OPTIONAL MATCH (a)-[:HAS_FIELD]->(f:Field)
                RETURN a.id AS id,
                       a.name AS name,
                       a.description AS description,
                       a.kind AS kind,
                       a.version AS version,
                       a.status AS status,
                       a.source_hash AS source_hash,
                       collect(DISTINCT {
                         id: f.id,
                         name: f.name,
                         type: f.type,
                         isKey: f.isKey,
                         isNullable: f.isNullable,
                         isForeignKey: f.isForeignKey,
                         description: f.description
                       }) AS fields
                """,
                id=agg_id,
            ).single()
            if not rec:
                return {"id": agg_id, "name": None, "description": None, "fields": []}

            data = rec.data()
            fields = []
            for f in data.get("fields") or []:
                if f and f.get("id"):
                    fields.append(f)
            data["fields"] = fields
            return data

    def _infer_bc_hint_from_aggregates(self, agg_ids: List[str]) -> Optional[str]:
        if not agg_ids:
            return None
        with self.driver.session() as session:
            rec = session.run(
                """
                UNWIND $agg_ids AS aid
                MATCH (bc:BoundedContext)-[:HAS_AGGREGATE]->(a:Aggregate {id: aid})
                RETURN bc.id AS bc_id, bc.name AS bc_name, count(*) AS cnt
                ORDER BY cnt DESC
                LIMIT 1
                """,
                agg_ids=agg_ids,
            ).single()
            if not rec:
                return None
            return rec["bc_name"] or rec["bc_id"]

    def _infer_bc_hint_from_story(self, story_id: str) -> Optional[str]:
        with self.driver.session() as session:
            rec = session.run(
                """
                MATCH (us:UserStory {id:$id})-[:IMPACTS_AGGREGATE]->(a:Aggregate)<-[:HAS_AGGREGATE]-(bc:BoundedContext)
                RETURN bc.id AS bc_id, bc.name AS bc_name, count(*) AS cnt
                ORDER BY cnt DESC
                LIMIT 1
                """,
                id=story_id,
            ).single()
            if not rec:
                return None
            return rec["bc_name"] or rec["bc_id"]

    def _get_aggregates_in_bc(self, bc_hint: str) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            res = session.run(
                """
                MATCH (bc:BoundedContext)
                WHERE bc.id = $h OR bc.name = $h
                MATCH (bc)-[:HAS_AGGREGATE]->(a:Aggregate)
                RETURN a.id AS id, a.name AS name, a.status AS status, a.version AS version
                ORDER BY a.id
                """,
                h=bc_hint,
            )
            return [r.data() for r in res]


def main():
    import argparse, json, os
    p = argparse.ArgumentParser(description="Regeneration Context Builder v0")
    p.add_argument("--uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    p.add_argument("--user", default=os.getenv("NEO4J_USER", "neo4j"))
    p.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", ""))
    p.add_argument("--story", required=True, help="root UserStory id (e.g., US_001)")
    p.add_argument("--dirty", nargs="*", default=[], help="dirty node ids (e.g., AGG_ORDER F_ORDER_AMOUNT)")
    p.add_argument("--project", default=None)
    args = p.parse_args()

    b = RegenerationContextBuilder(args.uri, args.user, args.password)
    try:
        ctx = b.build_phase_a_aggregate_context(
            root_story_id=args.story,
            dirty_node_ids=args.dirty,
            project_id=args.project,
            mode="dirty",
        )
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
    finally:
        b.close()

if __name__ == "__main__":
    main()
