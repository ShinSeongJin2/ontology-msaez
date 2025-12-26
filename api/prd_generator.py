"""
PRD Generator API

Generates PRD (Product Requirements Document) and AI-friendly project context files
from the current Event Storming model stored in Neo4j.
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from datetime import datetime
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.requests import Request

from api.platform.neo4j import get_session
from api.smart_logger import SmartLogger
from api.request_logging import http_context, summarize_for_log, sha256_bytes

router = APIRouter(prefix="/api/prd", tags=["PRD Generator"])


# =============================================================================
# Enums and Models
# =============================================================================


class Language(str, Enum):
    JAVA = "java"
    KOTLIN = "kotlin"
    TYPESCRIPT = "typescript"
    PYTHON = "python"
    GO = "go"


class Framework(str, Enum):
    SPRING_BOOT = "spring-boot"
    SPRING_WEBFLUX = "spring-webflux"
    NESTJS = "nestjs"
    EXPRESS = "express"
    FASTAPI = "fastapi"
    GIN = "gin"
    FIBER = "fiber"


class MessagingPlatform(str, Enum):
    KAFKA = "kafka"
    RABBITMQ = "rabbitmq"
    REDIS_STREAMS = "redis-streams"
    PULSAR = "pulsar"
    IN_MEMORY = "in-memory"


class DeploymentStyle(str, Enum):
    MICROSERVICES = "microservices"
    MODULAR_MONOLITH = "modular-monolith"


class Database(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MONGODB = "mongodb"
    H2 = "h2"


class TechStackConfig(BaseModel):
    language: Language = Language.JAVA
    framework: Framework = Framework.SPRING_BOOT
    messaging: MessagingPlatform = MessagingPlatform.KAFKA
    deployment: DeploymentStyle = DeploymentStyle.MICROSERVICES
    database: Database = Database.POSTGRESQL
    project_name: str = Field(default="my-project", description="Project name for the generated code")
    package_name: str = Field(default="com.example", description="Base package name (for Java/Kotlin)")
    include_docker: bool = True
    include_kubernetes: bool = False
    include_tests: bool = True


class PRDGenerationRequest(BaseModel):
    node_ids: list[str] = Field(..., description="List of node IDs from canvas")
    tech_stack: TechStackConfig = Field(default_factory=TechStackConfig)


# =============================================================================
# Data Fetching
# =============================================================================


def _get_framework_languages(framework: Framework) -> list[str]:
    mapping = {
        Framework.SPRING_BOOT: ["java", "kotlin"],
        Framework.SPRING_WEBFLUX: ["java", "kotlin"],
        Framework.NESTJS: ["typescript"],
        Framework.EXPRESS: ["typescript", "javascript"],
        Framework.FASTAPI: ["python"],
        Framework.GIN: ["go"],
        Framework.FIBER: ["go"],
    }
    return mapping.get(framework, [])


def _get_messaging_description(messaging: MessagingPlatform) -> str:
    descriptions = {
        MessagingPlatform.KAFKA: "Distributed event streaming, best for microservices",
        MessagingPlatform.RABBITMQ: "Message broker with flexible routing",
        MessagingPlatform.REDIS_STREAMS: "Lightweight, good for simpler use cases",
        MessagingPlatform.PULSAR: "Multi-tenant, geo-replication support",
        MessagingPlatform.IN_MEMORY: "For modular monolith, uses internal event bus",
    }
    return descriptions.get(messaging, "")


def fetch_bc_data(bc_id: str) -> dict | None:
    t0 = time.perf_counter()
    query = """
    MATCH (bc:BoundedContext {id: $bc_id})
    OPTIONAL MATCH (bc)-[:HAS_AGGREGATE]->(agg:Aggregate)
    OPTIONAL MATCH (agg)-[:HAS_COMMAND]->(cmd:Command)
    OPTIONAL MATCH (cmd)-[:EMITS]->(evt:Event)
    WITH bc, agg,
         collect(DISTINCT {id: cmd.id, name: cmd.name, actor: cmd.actor}) as commands,
         collect(DISTINCT {id: evt.id, name: evt.name, version: evt.version}) as events
    WITH bc, collect(DISTINCT {
        id: agg.id,
        name: agg.name,
        rootEntity: agg.rootEntity,
        commands: commands,
        events: events
    }) as aggregates

    OPTIONAL MATCH (bc)-[:HAS_POLICY]->(pol:Policy)
    OPTIONAL MATCH (triggerEvt:Event)-[:TRIGGERS]->(pol)
    OPTIONAL MATCH (pol)-[:INVOKES]->(invokeCmd:Command)
    WITH bc, aggregates, collect(DISTINCT {
        id: pol.id,
        name: pol.name,
        description: pol.description,
        triggerEventId: triggerEvt.id,
        triggerEventName: triggerEvt.name,
        invokeCommandId: invokeCmd.id,
        invokeCommandName: invokeCmd.name
    }) as policies

    RETURN {
        id: bc.id,
        name: bc.name,
        description: bc.description,
        aggregates: [a IN aggregates WHERE a.id IS NOT NULL],
        policies: [p IN policies WHERE p.id IS NOT NULL]
    } as bc_data
    """

    with get_session() as session:
        result = session.run(query, bc_id=bc_id)
        record = result.single()
        if record:
            bc_data = dict(record["bc_data"])
            SmartLogger.log(
                "INFO",
                "PRD: fetched BC data from Neo4j.",
                category="api.prd.neo4j.fetch_bc",
                params={
                    "bc_id": bc_id,
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "summary": {
                        "aggregates": len(bc_data.get("aggregates") or []),
                        "policies": len(bc_data.get("policies") or []),
                    },
                },
            )
            return bc_data
    SmartLogger.log(
        "WARNING",
        "PRD: BC not found while fetching data.",
        category="api.prd.neo4j.fetch_bc.not_found",
        params={"bc_id": bc_id, "duration_ms": int((time.perf_counter() - t0) * 1000)},
    )
    return None


def get_bcs_from_nodes(node_ids: list[str]) -> list[dict]:
    t0 = time.perf_counter()
    query = """
    // Direct BC nodes
    UNWIND $node_ids as nodeId
    OPTIONAL MATCH (bc:BoundedContext {id: nodeId})
    WITH collect(DISTINCT bc.id) as directBCs

    // BCs containing the nodes
    UNWIND $node_ids as nodeId
    OPTIONAL MATCH (bc:BoundedContext)-[:HAS_AGGREGATE|HAS_POLICY*1..3]->(n {id: nodeId})
    WITH directBCs, collect(DISTINCT bc.id) as containingBCs

    // BCs for Commands (via Aggregate)
    UNWIND $node_ids as nodeId
    OPTIONAL MATCH (bc:BoundedContext)-[:HAS_AGGREGATE]->(agg:Aggregate)-[:HAS_COMMAND]->(cmd:Command {id: nodeId})
    WITH directBCs, containingBCs, collect(DISTINCT bc.id) as cmdBCs

    // BCs for Events (via Command)
    UNWIND $node_ids as nodeId
    OPTIONAL MATCH (bc:BoundedContext)-[:HAS_AGGREGATE]->(agg2:Aggregate)-[:HAS_COMMAND]->(cmd2:Command)-[:EMITS]->(evt:Event {id: nodeId})
    WITH directBCs, containingBCs, cmdBCs, collect(DISTINCT bc.id) as evtBCs

    WITH directBCs + containingBCs + cmdBCs + evtBCs as allBCIds
    UNWIND allBCIds as bcId
    WITH DISTINCT bcId WHERE bcId IS NOT NULL
    RETURN collect(bcId) as bc_ids
    """

    bc_ids: list[str] = []
    with get_session() as session:
        result = session.run(query, node_ids=node_ids)
        record = result.single()
        if record:
            bc_ids = record["bc_ids"] or []

    SmartLogger.log(
        "INFO",
        "PRD: resolved BC IDs from selected node IDs.",
        category="api.prd.neo4j.resolve_bcs",
        params={
            "inputs": {"node_ids": summarize_for_log(node_ids)},
            "resolved_bc_ids": bc_ids,
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        },
    )

    bcs: list[dict] = []
    for bc_id in bc_ids:
        bc_data = fetch_bc_data(bc_id)
        if bc_data:
            bcs.append(bc_data)
    return bcs


# =============================================================================
# Generators
# =============================================================================


def generate_main_prd(bcs: list[dict], config: TechStackConfig) -> str:
    prd = f"""# {config.project_name} - Product Requirements Document

Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Technology Stack

| Component | Choice |
|-----------|--------|
| **Language** | {config.language.value} |
| **Framework** | {config.framework.value} |
| **Messaging** | {config.messaging.value} |
| **Database** | {config.database.value} |
| **Deployment** | {config.deployment.value} |

## Bounded Contexts
"""

    prd += "\n| BC Name | Aggregates | Commands | Events | Policies |\n"
    prd += "|---------|------------|----------|--------|----------|\n"
    for bc in bcs:
        aggs = bc.get("aggregates", []) or []
        cmds = sum(len(a.get("commands", []) or []) for a in aggs)
        evts = sum(len(a.get("events", []) or []) for a in aggs)
        pols = len(bc.get("policies", []) or [])
        prd += f"| {bc.get('name', 'Unknown')} | {len(aggs)} | {cmds} | {evts} | {pols} |\n"

    prd += "\n## Notes\n- This PRD was generated from the Event Storming model stored in Neo4j.\n"
    return prd


def generate_bc_spec(bc: dict, config: TechStackConfig) -> str:
    name = bc.get("name", "Unknown")
    spec = f"""# {name} Bounded Context Specification

## Overview
- **BC ID**: {bc.get("id", "")}
- **Description**: {bc.get("description", "No description")}

## Aggregates
"""
    for agg in bc.get("aggregates", []) or []:
        spec += f"\n### {agg.get('name', 'Unknown')}\n"
        if agg.get("rootEntity"):
            spec += f"- Root Entity: `{agg['rootEntity']}`\n"
        if agg.get("commands"):
            spec += "- Commands:\n"
            for cmd in agg["commands"]:
                if cmd.get("id"):
                    spec += f"  - `{cmd.get('name','')}` (actor: {cmd.get('actor','')})\n"
        if agg.get("events"):
            spec += "- Events:\n"
            for evt in agg["events"]:
                if evt.get("id"):
                    spec += f"  - `{evt.get('name','')}` (v{evt.get('version','1')})\n"

    if bc.get("policies"):
        spec += "\n## Policies\n"
        for pol in bc["policies"]:
            if pol.get("id"):
                spec += f"- `{pol.get('name','')}`: triggers `{pol.get('triggerEventId')}` -> invokes `{pol.get('invokeCommandId')}`\n"

    spec += "\n## Implementation Notes\n"
    spec += f"- Framework: `{config.framework.value}`\n- Messaging: `{config.messaging.value}`\n"
    return spec


def generate_claude_md(bcs: list[dict], config: TechStackConfig) -> str:
    return f"""# CLAUDE.md - AI Assistant Context

## Project
- Name: {config.project_name}
- Deployment: {config.deployment.value}
- Stack: {config.language.value} / {config.framework.value}
- Messaging: {config.messaging.value}
- Database: {config.database.value}

## Bounded Contexts
{chr(10).join([f"- {bc.get('name','Unknown')} ({bc.get('id','')})" for bc in bcs])}
"""


def generate_cursor_rules(config: TechStackConfig) -> str:
    return f"""# Cursor Rules for {config.project_name}

- Follow DDD naming: Commands are verbs, Events are past tense
- Keep BC boundaries clear
- Prefer explicit schemas for events and commands
"""


def generate_agent_config(bc: dict) -> str:
    bc_name = (bc.get("name", "unknown") or "unknown").lower().replace(" ", "_")
    return f"""# Agent Configuration: {bc.get('name','Unknown')}

## Scope
- Only modify files within `{bc_name}/`
- Respect event contracts defined in `specs/{bc_name}_spec.md`
"""


def generate_readme(bcs: list[dict], config: TechStackConfig) -> str:
    return f"""# {config.project_name}

Generated from Event Storming model.

## Bounded Contexts
{chr(10).join([f"- {bc.get('name','Unknown')}: {bc.get('description','')}" for bc in bcs])}
"""


def generate_dockerfile(config: TechStackConfig) -> str:
    if config.framework == Framework.FASTAPI:
        return """FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""
    if config.framework in [Framework.NESTJS, Framework.EXPRESS]:
        return """FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
CMD ["npm","run","start"]
"""
    return """# Dockerfile template (customize per service)
"""


def generate_docker_compose(config: TechStackConfig) -> str:
    # Minimal infra template
    if config.database == Database.POSTGRESQL:
        db_service = """  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: ${DB_NAME:-app}
      POSTGRES_USER: ${DB_USER:-postgres}
      POSTGRES_PASSWORD: ${DB_PASSWORD:-postgres}
    ports:
      - "5432:5432"
"""
    elif config.database == Database.MONGODB:
        db_service = """  mongodb:
    image: mongo:6
    ports:
      - "27017:27017"
"""
    else:
        db_service = ""

    return f"""version: "3.8"
services:
{db_service}
"""


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("/tech-stacks")
async def get_available_tech_stacks(request: Request):
    SmartLogger.log(
        "INFO",
        "PRD: tech stack options requested.",
        category="api.prd.tech_stacks.request",
        params=http_context(request),
    )
    payload = {
        "languages": [{"value": l.value, "label": l.name.title()} for l in Language],
        "frameworks": [
            {
                "value": f.value,
                "label": f.value.replace("-", " ").title(),
                "languages": _get_framework_languages(f),
            }
            for f in Framework
        ],
        "messaging": [
            {
                "value": m.value,
                "label": m.value.replace("-", " ").title(),
                "description": _get_messaging_description(m),
            }
            for m in MessagingPlatform
        ],
        "deployments": [
            {"value": d.value, "label": d.value.replace("-", " ").title()}
            for d in DeploymentStyle
        ],
        "databases": [{"value": d.value, "label": d.value.title()} for d in Database],
    }
    SmartLogger.log(
        "INFO",
        "PRD: tech stack options returned.",
        category="api.prd.tech_stacks.done",
        params={**http_context(request), "summary": {"keys": list(payload.keys())}},
    )
    return payload


@router.post("/generate")
async def generate_prd(request: PRDGenerationRequest, http_request: Request):
    t0 = time.perf_counter()
    if not request.node_ids:
        raise HTTPException(status_code=400, detail="node_ids cannot be empty")

    SmartLogger.log(
        "INFO",
        "PRD: generation plan requested.",
        category="api.prd.generate.request",
        params={
            **http_context(http_request),
            "inputs": {
                "node_ids": summarize_for_log(request.node_ids),
                "tech_stack": request.tech_stack.model_dump(),
            },
        },
    )

    bcs = get_bcs_from_nodes(request.node_ids)
    if not bcs:
        raise HTTPException(status_code=404, detail="No Bounded Contexts found for the given nodes")

    config = request.tech_stack

    files_to_generate = ["CLAUDE.md", "PRD.md", ".cursorrules"]
    for bc in bcs:
        bc_name = (bc.get("name", "unknown") or "unknown").lower().replace(" ", "_")
        files_to_generate.append(f".claude/agents/{bc_name}_agent.md")
        files_to_generate.append(f"specs/{bc_name}_spec.md")

    if config.include_docker:
        files_to_generate.append("docker-compose.yml")
        files_to_generate.append("Dockerfile")

    payload = {
        "success": True,
        "bounded_contexts": [{"id": bc.get("id"), "name": bc.get("name")} for bc in bcs],
        "tech_stack": config.model_dump(),
        "files_to_generate": files_to_generate,
        "download_url": "/api/prd/download",
    }
    SmartLogger.log(
        "INFO",
        "PRD: generation plan created.",
        category="api.prd.generate.done",
        params={
            **http_context(http_request),
            "duration_ms": int((time.perf_counter() - t0) * 1000),
            "summary": {
                "bcs": len(bcs),
                "files_to_generate": len(files_to_generate),
            },
        },
    )
    return payload


@router.post("/download")
async def download_prd_zip(request: PRDGenerationRequest, http_request: Request):
    t0 = time.perf_counter()
    if not request.node_ids:
        raise HTTPException(status_code=400, detail="node_ids cannot be empty")

    SmartLogger.log(
        "INFO",
        "PRD: zip download requested.",
        category="api.prd.download.request",
        params={
            **http_context(http_request),
            "inputs": {
                "node_ids": summarize_for_log(request.node_ids),
                "tech_stack": request.tech_stack.model_dump(),
            },
        },
    )

    bcs = get_bcs_from_nodes(request.node_ids)
    if not bcs:
        raise HTTPException(status_code=404, detail="No Bounded Contexts found for the given nodes")

    config = request.tech_stack
    zip_buffer = io.BytesIO()

    t_zip0 = time.perf_counter()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("CLAUDE.md", generate_claude_md(bcs, config))
        zip_file.writestr("PRD.md", generate_main_prd(bcs, config))
        zip_file.writestr(".cursorrules", generate_cursor_rules(config))

        for bc in bcs:
            bc_name = (bc.get("name", "unknown") or "unknown").lower().replace(" ", "_")
            zip_file.writestr(f"specs/{bc_name}_spec.md", generate_bc_spec(bc, config))
            zip_file.writestr(f".claude/agents/{bc_name}_agent.md", generate_agent_config(bc))

        if config.include_docker:
            zip_file.writestr("docker-compose.yml", generate_docker_compose(config))
            zip_file.writestr("Dockerfile", generate_dockerfile(config))

        zip_file.writestr("README.md", generate_readme(bcs, config))

    zip_buffer.seek(0)
    zip_bytes = zip_buffer.getvalue()
    zip_size = len(zip_bytes)
    zip_sha = sha256_bytes(zip_bytes)
    filename = f"{config.project_name}_prd_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    SmartLogger.log(
        "INFO",
        "PRD: zip built and streaming response returned.",
        category="api.prd.download.done",
        params={
            **http_context(http_request),
            "duration_ms": int((time.perf_counter() - t0) * 1000),
            "zip_build_ms": int((time.perf_counter() - t_zip0) * 1000),
            "summary": {
                "bcs": len(bcs),
                "zip_bytes": zip_size,
                "zip_sha256": zip_sha,
                "filename": filename,
            },
        },
    )

    zip_buffer = io.BytesIO(zip_bytes)
    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


