"""
Ingestion Workflow Runner (streaming)

Business capability: convert uploaded requirements text into an Event Storming model in Neo4j,
emitting real-time progress events for the UI (SSE).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage, SystemMessage

from api.features.ingestion.ingestion_ai_audit import (
    AI_AUDIT_LOG_ENABLED,
    AI_AUDIT_LOG_FULL_OUTPUT,
    AI_AUDIT_LOG_FULL_PROMPT,
)
from api.features.ingestion.ingestion_contracts import IngestionPhase, ProgressEvent
from api.features.ingestion.ingestion_llm_runtime import get_llm
from api.features.ingestion.ingestion_sessions import IngestionSession
from api.features.ingestion.requirements_to_user_stories import extract_user_stories_from_text
from api.platform.observability.request_logging import sha256_text, summarize_for_log
from api.platform.observability.smart_logger import SmartLogger


async def run_ingestion_workflow(session: IngestionSession, content: str) -> AsyncGenerator[ProgressEvent, None]:
    """
    Run the full ingestion workflow with streaming progress updates.
    """
    from api.features.ingestion.event_storming.neo4j_client import get_neo4j_client

    client = get_neo4j_client()

    try:
        SmartLogger.log(
            "INFO",
            "Ingestion workflow started",
            category="ingestion.workflow",
            params={"session_id": session.id, "content_length": len(content)},
        )
        # Phase 1: Parsing
        yield ProgressEvent(phase=IngestionPhase.PARSING, message="문서 파싱 중...", progress=5)
        await asyncio.sleep(0.3)  # Small delay for UI feedback

        # Phase 2: Extract User Stories
        yield ProgressEvent(phase=IngestionPhase.EXTRACTING_USER_STORIES, message="User Story 추출 중...", progress=10)

        user_stories = extract_user_stories_from_text(content)
        SmartLogger.log(
            "INFO",
            "User stories extracted",
            category="ingestion.workflow.user_stories",
            params={"session_id": session.id, "count": len(user_stories)},
        )

        # Save user stories to Neo4j and emit events for each
        for i, us in enumerate(user_stories):
            try:
                client.create_user_story(
                    id=us.id,
                    role=us.role,
                    action=us.action,
                    benefit=us.benefit,
                    priority=us.priority,
                    status="draft",
                )

                # Emit event for each User Story created
                yield ProgressEvent(
                    phase=IngestionPhase.EXTRACTING_USER_STORIES,
                    message=f"User Story 생성: {us.id}",
                    progress=10 + (10 * (i + 1) // len(user_stories)),
                    data={
                        "type": "UserStory",
                        "object": {
                            "id": us.id,
                            "name": f"{us.role}: {us.action[:30]}...",
                            "type": "UserStory",
                            "role": us.role,
                            "action": us.action,
                            "benefit": us.benefit,
                            "priority": us.priority,
                        },
                    },
                )
                await asyncio.sleep(0.15)  # Small delay for visual effect

            except Exception as e:
                # Skip if already exists (or any create error) but keep traceability.
                SmartLogger.log(
                    "WARNING",
                    "User story create skipped",
                    category="ingestion.neo4j.user_story",
                    params={"session_id": session.id, "id": us.id, "error": str(e)},
                )

        yield ProgressEvent(
            phase=IngestionPhase.EXTRACTING_USER_STORIES,
            message=f"{len(user_stories)}개 User Story 추출 완료",
            progress=20,
            data={
                "count": len(user_stories),
                "items": [{"id": us.id, "role": us.role, "action": us.action[:50]} for us in user_stories],
            },
        )

        # Phase 3: Identify Bounded Contexts
        yield ProgressEvent(phase=IngestionPhase.IDENTIFYING_BC, message="Bounded Context 식별 중...", progress=25)

        from api.features.ingestion.event_storming.nodes import BoundedContextList
        from api.features.ingestion.event_storming.prompts import IDENTIFY_BC_FROM_STORIES_PROMPT, SYSTEM_PROMPT

        llm = get_llm()

        stories_text = "\n".join(
            [f"[{us.id}] As a {us.role}, I want to {us.action}, so that {us.benefit}" for us in user_stories]
        )

        structured_llm = llm.with_structured_output(BoundedContextList)
        prompt = IDENTIFY_BC_FROM_STORIES_PROMPT.format(user_stories=stories_text)

        provider = os.getenv("LLM_PROVIDER", "openai")
        model = os.getenv("LLM_MODEL", "gpt-4o")
        if AI_AUDIT_LOG_ENABLED:
            SmartLogger.log(
                "INFO",
                "Ingestion: identify BCs - LLM invoke starting.",
                category="ingestion.llm.identify_bc.start",
                params={
                    "session_id": session.id,
                    "llm": {"provider": provider, "model": model},
                    "user_stories_count": len(user_stories),
                    "prompt_len": len(prompt),
                    "prompt_sha256": sha256_text(prompt),
                    "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                    "system_sha256": sha256_text(SYSTEM_PROMPT),
                },
                max_inline_chars=1800,
            )

        t_llm0 = time.perf_counter()
        bc_response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
        llm_ms = int((time.perf_counter() - t_llm0) * 1000)

        if AI_AUDIT_LOG_ENABLED:
            try:
                resp_dump = bc_response.model_dump() if hasattr(bc_response, "model_dump") else bc_response.dict()
            except Exception:
                resp_dump = {"__type__": type(bc_response).__name__, "__repr__": repr(bc_response)[:1000]}
            bcs = getattr(bc_response, "bounded_contexts", []) or []
            SmartLogger.log(
                "INFO",
                "Ingestion: identify BCs - LLM invoke completed.",
                category="ingestion.llm.identify_bc.done",
                params={
                    "session_id": session.id,
                    "llm": {"provider": provider, "model": model},
                    "llm_ms": llm_ms,
                    "result": {
                        "bounded_contexts_count": len(bcs),
                        "bounded_context_ids": summarize_for_log([getattr(bc, "id", None) for bc in bcs]),
                        "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                    },
                },
                max_inline_chars=1800,
            )

        bc_candidates = bc_response.bounded_contexts
        SmartLogger.log(
            "INFO",
            "Bounded contexts identified",
            category="ingestion.workflow.bc",
            params={"session_id": session.id, "count": len(bc_candidates), "ids": [bc.id for bc in bc_candidates][:10]},
        )

        # Create BCs in Neo4j
        for bc_idx, bc in enumerate(bc_candidates):
            client.create_bounded_context(id=bc.id, name=bc.name, description=bc.description)

            # Emit BC creation event
            yield ProgressEvent(
                phase=IngestionPhase.IDENTIFYING_BC,
                message=f"Bounded Context 생성: {bc.name}",
                progress=30 + (10 * bc_idx // max(len(bc_candidates), 1)),
                data={
                    "type": "BoundedContext",
                    "object": {
                        "id": bc.id,
                        "name": bc.name,
                        "type": "BoundedContext",
                        "description": bc.description,
                        "userStoryIds": bc.user_story_ids,
                    },
                },
            )
            await asyncio.sleep(0.2)

            # Link user stories to BC and emit move events
            for us_id in bc.user_story_ids:
                try:
                    client.link_user_story_to_bc(us_id, bc.id)

                    yield ProgressEvent(
                        phase=IngestionPhase.IDENTIFYING_BC,
                        message=f"User Story {us_id} → {bc.name}",
                        progress=30 + (10 * bc_idx // max(len(bc_candidates), 1)),
                        data={
                            "type": "UserStoryAssigned",
                            "object": {"id": us_id, "type": "UserStory", "targetBcId": bc.id, "targetBcName": bc.name},
                        },
                    )
                    await asyncio.sleep(0.1)
                except Exception as e:
                    SmartLogger.log(
                        "WARNING",
                        "User story to BC link skipped",
                        category="ingestion.neo4j.us_to_bc",
                        params={"session_id": session.id, "user_story_id": us_id, "bc_id": bc.id, "error": str(e)},
                    )

        # Phase 4: Extract Aggregates
        yield ProgressEvent(phase=IngestionPhase.EXTRACTING_AGGREGATES, message="Aggregate 추출 중...", progress=45)

        from api.features.ingestion.event_storming.nodes import AggregateList
        from api.features.ingestion.event_storming.prompts import EXTRACT_AGGREGATES_PROMPT

        all_aggregates: dict[str, Any] = {}
        progress_per_bc = 10 // max(len(bc_candidates), 1)

        for bc_idx, bc in enumerate(bc_candidates):
            bc_id_short = bc.id.replace("BC-", "")

            breakdowns_text = f"User Stories: {', '.join(bc.user_story_ids)}"

            prompt = EXTRACT_AGGREGATES_PROMPT.format(
                bc_name=bc.name,
                bc_id=bc.id,
                bc_id_short=bc_id_short,
                bc_description=bc.description,
                breakdowns=breakdowns_text,
            )

            structured_llm = llm.with_structured_output(AggregateList)

            provider = os.getenv("LLM_PROVIDER", "openai")
            model = os.getenv("LLM_MODEL", "gpt-4o")
            if AI_AUDIT_LOG_ENABLED:
                SmartLogger.log(
                    "INFO",
                    "Ingestion: extract aggregates - LLM invoke starting.",
                    category="ingestion.llm.extract_aggregates.start",
                    params={
                        "session_id": session.id,
                        "llm": {"provider": provider, "model": model},
                        "bc": {"id": bc.id, "name": bc.name},
                        "prompt_len": len(prompt),
                        "prompt_sha256": sha256_text(prompt),
                        "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                        "system_sha256": sha256_text(SYSTEM_PROMPT),
                    },
                    max_inline_chars=1800,
                )

            t_llm0 = time.perf_counter()
            agg_response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
            llm_ms = int((time.perf_counter() - t_llm0) * 1000)

            if AI_AUDIT_LOG_ENABLED:
                try:
                    resp_dump = agg_response.model_dump() if hasattr(agg_response, "model_dump") else agg_response.dict()
                except Exception:
                    resp_dump = {"__type__": type(agg_response).__name__, "__repr__": repr(agg_response)[:1000]}
                aggs = getattr(agg_response, "aggregates", []) or []
                SmartLogger.log(
                    "INFO",
                    "Ingestion: extract aggregates - LLM invoke completed.",
                    category="ingestion.llm.extract_aggregates.done",
                    params={
                        "session_id": session.id,
                        "llm": {"provider": provider, "model": model},
                        "bc": {"id": bc.id, "name": bc.name},
                        "llm_ms": llm_ms,
                        "result": {
                            "aggregates_count": len(aggs),
                            "aggregate_ids": summarize_for_log([getattr(a, "id", None) for a in aggs]),
                            "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                        },
                    },
                    max_inline_chars=1800,
                )

            aggregates = agg_response.aggregates
            all_aggregates[bc.id] = aggregates
            SmartLogger.log(
                "INFO",
                "Aggregates extracted",
                category="ingestion.workflow.aggregates",
                params={"session_id": session.id, "bc_id": bc.id, "bc_name": bc.name, "count": len(aggregates)},
            )

            for agg in aggregates:
                client.create_aggregate(
                    id=agg.id,
                    name=agg.name,
                    bc_id=bc.id,
                    root_entity=agg.root_entity,
                    invariants=agg.invariants,
                )

                yield ProgressEvent(
                    phase=IngestionPhase.EXTRACTING_AGGREGATES,
                    message=f"Aggregate 생성: {agg.name}",
                    progress=45 + progress_per_bc * bc_idx,
                    data={"type": "Aggregate", "object": {"id": agg.id, "name": agg.name, "type": "Aggregate", "parentId": bc.id}},
                )
                await asyncio.sleep(0.15)

        # Phase 5: Extract Commands
        yield ProgressEvent(phase=IngestionPhase.EXTRACTING_COMMANDS, message="Command 추출 중...", progress=60)

        from api.features.ingestion.event_storming.nodes import CommandList
        from api.features.ingestion.event_storming.prompts import EXTRACT_COMMANDS_PROMPT

        all_commands: dict[str, Any] = {}

        for bc in bc_candidates:
            bc_id_short = bc.id.replace("BC-", "")
            bc_aggregates = all_aggregates.get(bc.id, [])

            for agg in bc_aggregates:
                stories_context = "\n".join(
                    [f"[{us.id}] As a {us.role}, I want to {us.action}" for us in user_stories if us.id in bc.user_story_ids]
                )

                prompt = EXTRACT_COMMANDS_PROMPT.format(
                    aggregate_name=agg.name,
                    aggregate_id=agg.id,
                    bc_name=bc.name,
                    bc_short=bc_id_short,
                    user_story_context=stories_context[:2000],
                )

                structured_llm = llm.with_structured_output(CommandList)

                try:
                    provider = os.getenv("LLM_PROVIDER", "openai")
                    model = os.getenv("LLM_MODEL", "gpt-4o")
                    if AI_AUDIT_LOG_ENABLED:
                        SmartLogger.log(
                            "INFO",
                            "Ingestion: extract commands - LLM invoke starting.",
                            category="ingestion.llm.extract_commands.start",
                            params={
                                "session_id": session.id,
                                "llm": {"provider": provider, "model": model},
                                "bc": {"id": bc.id, "name": bc.name},
                                "aggregate": {"id": agg.id, "name": agg.name},
                                "prompt_len": len(prompt),
                                "prompt_sha256": sha256_text(prompt),
                                "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                                "system_sha256": sha256_text(SYSTEM_PROMPT),
                            },
                            max_inline_chars=1800,
                        )

                    t_llm0 = time.perf_counter()
                    cmd_response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
                    llm_ms = int((time.perf_counter() - t_llm0) * 1000)
                    commands = cmd_response.commands

                    if AI_AUDIT_LOG_ENABLED:
                        try:
                            resp_dump = cmd_response.model_dump() if hasattr(cmd_response, "model_dump") else cmd_response.dict()
                        except Exception:
                            resp_dump = {"__type__": type(cmd_response).__name__, "__repr__": repr(cmd_response)[:1000]}
                        SmartLogger.log(
                            "INFO",
                            "Ingestion: extract commands - LLM invoke completed.",
                            category="ingestion.llm.extract_commands.done",
                            params={
                                "session_id": session.id,
                                "llm": {"provider": provider, "model": model},
                                "bc": {"id": bc.id, "name": bc.name},
                                "aggregate": {"id": agg.id, "name": agg.name},
                                "llm_ms": llm_ms,
                                "result": {
                                    "commands_count": len(commands),
                                    "command_ids": summarize_for_log([getattr(c, "id", None) for c in commands]),
                                    "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                                },
                            },
                            max_inline_chars=1800,
                        )
                except Exception as e:
                    SmartLogger.log(
                        "WARNING",
                        "Command extraction failed (LLM)",
                        category="ingestion.workflow.commands",
                        params={"session_id": session.id, "bc_id": bc.id, "agg_id": agg.id, "error": str(e)},
                    )
                    commands = []

                all_commands[agg.id] = commands
                if commands:
                    SmartLogger.log(
                        "INFO",
                        "Commands extracted",
                        category="ingestion.workflow.commands",
                        params={"session_id": session.id, "agg_id": agg.id, "count": len(commands)},
                    )

                for cmd in commands:
                    client.create_command(id=cmd.id, name=cmd.name, aggregate_id=agg.id, actor=cmd.actor)

                    yield ProgressEvent(
                        phase=IngestionPhase.EXTRACTING_COMMANDS,
                        message=f"Command 생성: {cmd.name}",
                        progress=65,
                        data={"type": "Command", "object": {"id": cmd.id, "name": cmd.name, "type": "Command", "parentId": agg.id}},
                    )
                    await asyncio.sleep(0.1)

        # Phase 6: Extract Events
        yield ProgressEvent(phase=IngestionPhase.EXTRACTING_EVENTS, message="Event 추출 중...", progress=75)

        from api.features.ingestion.event_storming.nodes import EventList
        from api.features.ingestion.event_storming.prompts import EXTRACT_EVENTS_PROMPT

        all_events: dict[str, Any] = {}

        for bc in bc_candidates:
            bc_id_short = bc.id.replace("BC-", "")
            bc_aggregates = all_aggregates.get(bc.id, [])

            for agg in bc_aggregates:
                commands = all_commands.get(agg.id, [])
                if not commands:
                    continue

                commands_text = "\n".join(
                    [
                        f"- {cmd.name}: {cmd.description}" if hasattr(cmd, "description") else f"- {cmd.name}"
                        for cmd in commands
                    ]
                )

                prompt = EXTRACT_EVENTS_PROMPT.format(
                    aggregate_name=agg.name,
                    bc_name=bc.name,
                    bc_short=bc_id_short,
                    commands=commands_text,
                )

                structured_llm = llm.with_structured_output(EventList)

                try:
                    provider = os.getenv("LLM_PROVIDER", "openai")
                    model = os.getenv("LLM_MODEL", "gpt-4o")
                    if AI_AUDIT_LOG_ENABLED:
                        SmartLogger.log(
                            "INFO",
                            "Ingestion: extract events - LLM invoke starting.",
                            category="ingestion.llm.extract_events.start",
                            params={
                                "session_id": session.id,
                                "llm": {"provider": provider, "model": model},
                                "bc": {"id": bc.id, "name": bc.name},
                                "aggregate": {"id": agg.id, "name": agg.name},
                                "prompt_len": len(prompt),
                                "prompt_sha256": sha256_text(prompt),
                                "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                                "system_sha256": sha256_text(SYSTEM_PROMPT),
                            },
                            max_inline_chars=1800,
                        )

                    t_llm0 = time.perf_counter()
                    evt_response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
                    llm_ms = int((time.perf_counter() - t_llm0) * 1000)
                    events = evt_response.events

                    if AI_AUDIT_LOG_ENABLED:
                        try:
                            resp_dump = evt_response.model_dump() if hasattr(evt_response, "model_dump") else evt_response.dict()
                        except Exception:
                            resp_dump = {"__type__": type(evt_response).__name__, "__repr__": repr(evt_response)[:1000]}
                        SmartLogger.log(
                            "INFO",
                            "Ingestion: extract events - LLM invoke completed.",
                            category="ingestion.llm.extract_events.done",
                            params={
                                "session_id": session.id,
                                "llm": {"provider": provider, "model": model},
                                "bc": {"id": bc.id, "name": bc.name},
                                "aggregate": {"id": agg.id, "name": agg.name},
                                "llm_ms": llm_ms,
                                "result": {
                                    "events_count": len(events),
                                    "event_ids": summarize_for_log([getattr(e, "id", None) for e in events]),
                                    "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                                },
                            },
                            max_inline_chars=1800,
                        )
                except Exception as e:
                    SmartLogger.log(
                        "WARNING",
                        "Event extraction failed (LLM)",
                        category="ingestion.workflow.events",
                        params={"session_id": session.id, "bc_id": bc.id, "agg_id": agg.id, "error": str(e)},
                    )
                    events = []

                all_events[agg.id] = events
                if events:
                    SmartLogger.log(
                        "INFO",
                        "Events extracted",
                        category="ingestion.workflow.events",
                        params={"session_id": session.id, "agg_id": agg.id, "count": len(events)},
                    )

                for i, evt in enumerate(events):
                    cmd_id = commands[i].id if i < len(commands) else commands[0].id if commands else None

                    if cmd_id:
                        client.create_event(id=evt.id, name=evt.name, command_id=cmd_id)

                        yield ProgressEvent(
                            phase=IngestionPhase.EXTRACTING_EVENTS,
                            message=f"Event 생성: {evt.name}",
                            progress=80,
                            data={"type": "Event", "object": {"id": evt.id, "name": evt.name, "type": "Event", "parentId": cmd_id}},
                        )
                        await asyncio.sleep(0.1)

        # Phase 7: Identify Policies
        yield ProgressEvent(phase=IngestionPhase.IDENTIFYING_POLICIES, message="Policy 식별 중...", progress=90)

        from api.features.ingestion.event_storming.nodes import PolicyList
        from api.features.ingestion.event_storming.prompts import IDENTIFY_POLICIES_PROMPT

        all_events_list: list[str] = []
        for events in all_events.values():
            for evt in events:
                all_events_list.append(f"- {evt.name}")

        events_text = "\n".join(all_events_list)

        commands_by_bc: dict[str, str] = {}
        for bc in bc_candidates:
            bc_cmds: list[str] = []
            for agg in all_aggregates.get(bc.id, []):
                for cmd in all_commands.get(agg.id, []):
                    bc_cmds.append(f"- {cmd.name}")
            commands_by_bc[bc.name] = "\n".join(bc_cmds) if bc_cmds else "No commands"

        commands_text = "\n".join([f"{bc_name}:\n{cmds}" for bc_name, cmds in commands_by_bc.items()])

        bc_text = "\n".join([f"- {bc.name}: {bc.description}" for bc in bc_candidates])

        prompt = IDENTIFY_POLICIES_PROMPT.format(events=events_text, commands_by_bc=commands_text, bounded_contexts=bc_text)

        structured_llm = llm.with_structured_output(PolicyList)

        try:
            provider = os.getenv("LLM_PROVIDER", "openai")
            model = os.getenv("LLM_MODEL", "gpt-4o")
            if AI_AUDIT_LOG_ENABLED:
                SmartLogger.log(
                    "INFO",
                    "Ingestion: identify policies - LLM invoke starting.",
                    category="ingestion.llm.identify_policies.start",
                    params={
                        "session_id": session.id,
                        "llm": {"provider": provider, "model": model},
                        "bounded_contexts_count": len(bc_candidates),
                        "events_count": len(all_events_list),
                        "prompt_len": len(prompt),
                        "prompt_sha256": sha256_text(prompt),
                        "prompt": prompt if AI_AUDIT_LOG_FULL_PROMPT else summarize_for_log(prompt),
                        "system_sha256": sha256_text(SYSTEM_PROMPT),
                    },
                    max_inline_chars=1800,
                )

            t_llm0 = time.perf_counter()
            pol_response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
            llm_ms = int((time.perf_counter() - t_llm0) * 1000)
            policies = pol_response.policies

            if AI_AUDIT_LOG_ENABLED:
                try:
                    resp_dump = pol_response.model_dump() if hasattr(pol_response, "model_dump") else pol_response.dict()
                except Exception:
                    resp_dump = {"__type__": type(pol_response).__name__, "__repr__": repr(pol_response)[:1000]}
                SmartLogger.log(
                    "INFO",
                    "Ingestion: identify policies - LLM invoke completed.",
                    category="ingestion.llm.identify_policies.done",
                    params={
                        "session_id": session.id,
                        "llm": {"provider": provider, "model": model},
                        "llm_ms": llm_ms,
                        "result": {
                            "policies_count": len(policies),
                            "policy_ids": summarize_for_log([getattr(p, "id", None) for p in policies]),
                            "response": resp_dump if AI_AUDIT_LOG_FULL_OUTPUT else summarize_for_log(resp_dump),
                        },
                    },
                    max_inline_chars=1800,
                )
        except Exception as e:
            SmartLogger.log(
                "WARNING",
                "Policy identification failed (LLM)",
                category="ingestion.workflow.policies",
                params={"session_id": session.id, "error": str(e)},
            )
            policies = []

        for pol in policies:
            trigger_event_id = None
            invoke_command_id = None
            target_bc_id = None

            for events in all_events.values():
                for evt in events:
                    if evt.name == pol.trigger_event:
                        trigger_event_id = evt.id
                        break

            for bc in bc_candidates:
                if bc.name == pol.target_bc or bc.id == pol.target_bc:
                    target_bc_id = bc.id
                    for agg in all_aggregates.get(bc.id, []):
                        for cmd in all_commands.get(agg.id, []):
                            if cmd.name == pol.invoke_command:
                                invoke_command_id = cmd.id
                                break

            if trigger_event_id and invoke_command_id and target_bc_id:
                try:
                    client.create_policy(
                        id=pol.id,
                        name=pol.name,
                        bc_id=target_bc_id,
                        trigger_event_id=trigger_event_id,
                        invoke_command_id=invoke_command_id,
                        description=pol.description,
                    )

                    yield ProgressEvent(
                        phase=IngestionPhase.IDENTIFYING_POLICIES,
                        message=f"Policy 생성: {pol.name}",
                        progress=95,
                        data={"type": "Policy", "object": {"id": pol.id, "name": pol.name, "type": "Policy", "parentId": target_bc_id}},
                    )
                except Exception as e:
                    SmartLogger.log(
                        "WARNING",
                        "Policy create skipped",
                        category="ingestion.neo4j.policy",
                        params={"session_id": session.id, "policy_id": pol.id, "error": str(e)},
                    )

        # Complete
        yield ProgressEvent(
            phase=IngestionPhase.COMPLETE,
            message="✅ 모델 생성 완료!",
            progress=100,
            data={
                "summary": {
                    "user_stories": len(user_stories),
                    "bounded_contexts": len(bc_candidates),
                    "aggregates": sum(len(aggs) for aggs in all_aggregates.values()),
                    "commands": sum(len(cmds) for cmds in all_commands.values()),
                    "events": sum(len(evts) for evts in all_events.values()),
                    "policies": len(policies),
                }
            },
        )
        SmartLogger.log(
            "INFO",
            "Ingestion workflow complete",
            category="ingestion.workflow",
            params={
                "session_id": session.id,
                "user_stories": len(user_stories),
                "bounded_contexts": len(bc_candidates),
                "aggregates": sum(len(aggs) for aggs in all_aggregates.values()),
                "commands": sum(len(cmds) for cmds in all_commands.values()),
                "events": sum(len(evts) for evts in all_events.values()),
                "policies": len(policies),
            },
        )

    except Exception as e:
        SmartLogger.log(
            "ERROR",
            "Ingestion workflow failed",
            category="ingestion.workflow",
            params={"session_id": session.id, "error": str(e)},
        )
        yield ProgressEvent(phase=IngestionPhase.ERROR, message=f"❌ 오류 발생: {str(e)}", progress=0, data={"error": str(e)})


