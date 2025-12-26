"""
LangGraph Node Functions for Event Storming Workflow

Each node function:
1. Takes the current state
2. Performs its specific task (LLM call, Neo4j operation, etc.)
3. Returns updated state

Nodes are designed to work incrementally, processing one user story
at a time to stay within LLM context limits.
"""

from __future__ import annotations

import json
import os
from typing import Any, List, Dict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.neo4j_client import get_neo4j_client
from agent.prompts import (
    BREAKDOWN_USER_STORY_PROMPT,
    EXTRACT_AGGREGATES_PROMPT,
    EXTRACT_COMMANDS_PROMPT,
    EXTRACT_EVENTS_PROMPT,
    IDENTIFY_BC_FROM_STORIES_PROMPT,
    IDENTIFY_POLICIES_PROMPT,
    SYSTEM_PROMPT,
)
from pydantic import BaseModel, Field

from agent.state import (
    AggregateCandidate,
    BoundedContextCandidate,
    CommandCandidate,
    EventCandidate,
    EventStormingState,
    PolicyCandidate,
    UserStoryBreakdown,
    WorkflowPhase,
    format_user_story,
)


# Wrapper models for structured output
class BoundedContextList(BaseModel):
    """List of Bounded Context candidates."""
    bounded_contexts: List[BoundedContextCandidate] = Field(
        description="List of identified bounded contexts"
    )


class AggregateList(BaseModel):
    """List of Aggregate candidates."""
    aggregates: List[AggregateCandidate] = Field(
        description="List of identified aggregates"
    )


class CommandList(BaseModel):
    """List of Command candidates."""
    commands: List[CommandCandidate] = Field(
        description="List of identified commands"
    )


class EventList(BaseModel):
    """List of Event candidates."""
    events: List[EventCandidate] = Field(
        description="List of identified events"
    )


class PolicyList(BaseModel):
    """List of Policy candidates."""
    policies: List[PolicyCandidate] = Field(
        description="List of identified policies"
    )

load_dotenv()


def get_llm():
    """Get the configured LLM instance."""
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=0)
    else:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=0)


# =============================================================================
# Initialization Nodes
# =============================================================================


def init_node(state: EventStormingState) -> Dict[str, Any]:
    """Initialize the workflow."""
    return {
        "phase": WorkflowPhase.LOAD_USER_STORIES,
        "messages": [SystemMessage(content=SYSTEM_PROMPT)],
    }


def load_user_stories_node(state: EventStormingState) -> Dict[str, Any]:
    """Load unprocessed user stories from Neo4j."""
    client = get_neo4j_client()

    # First try to get unprocessed stories
    user_stories = client.get_unprocessed_user_stories()

    # If none, get all stories for demo purposes
    if not user_stories:
        user_stories = client.get_all_user_stories()

    if not user_stories:
        return {
            "phase": WorkflowPhase.COMPLETE,
            "error": "No user stories found in Neo4j. Please load sample data first.",
        }

    # Format stories for display
    stories_text = "\n".join(
        [
            f"- [{us['id']}] {format_user_story(us)}"
            for us in user_stories
        ]
    )

    return {
        "user_stories": user_stories,
        "total_user_stories": len(user_stories),
        "phase": WorkflowPhase.IDENTIFY_BC,
        "messages": [
            HumanMessage(
                content=f"Loaded {len(user_stories)} user stories:\n{stories_text}"
            )
        ],
    }


# =============================================================================
# Bounded Context Identification
# =============================================================================


def identify_bc_node(state: EventStormingState) -> Dict[str, Any]:
    """Identify Bounded Context candidates from user stories."""
    llm = get_llm()

    # Format user stories for the prompt
    stories_text = "\n".join(
        [
            f"[{us['id']}] As a {us.get('role', 'user')}, I want to {us.get('action', '?')}"
            + (f", so that {us.get('benefit', '')}" if us.get("benefit") else "")
            for us in state.user_stories
        ]
    )

    prompt = IDENTIFY_BC_FROM_STORIES_PROMPT.format(user_stories=stories_text)

    # Use structured output for BC candidates
    structured_llm = llm.with_structured_output(BoundedContextList)

    response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])

    # Parse response into BC candidates
    bc_candidates = response.bounded_contexts

    # Format for display
    bc_text = "\n".join(
        [
            f"- {bc.id}: {bc.name}\n  Description: {bc.description}\n  User Stories: {', '.join(bc.user_story_ids)}"
            for bc in bc_candidates
        ]
    )

    return {
        "bc_candidates": bc_candidates,
        "phase": WorkflowPhase.APPROVE_BC,
        "awaiting_human_approval": True,
        "messages": [
            AIMessage(
                content=f"I've identified {len(bc_candidates)} Bounded Context candidates:\n\n{bc_text}\n\nPlease review and approve, or provide feedback for changes."
            )
        ],
    }


def approve_bc_node(state: EventStormingState) -> Dict[str, Any]:
    """Process human approval for Bounded Contexts."""
    feedback = state.human_feedback

    if feedback and feedback.upper() == "APPROVED":
        return {
            "approved_bcs": state.bc_candidates,
            "awaiting_human_approval": False,
            "human_feedback": None,
            "phase": WorkflowPhase.BREAKDOWN_USER_STORY,
            "current_bc_index": 0,
            "messages": [
                HumanMessage(content="APPROVED"),
                AIMessage(content="Bounded Contexts approved! Moving to user story breakdown..."),
            ],
        }
    elif feedback:
        # User requested changes - go back to identification with feedback
        return {
            "awaiting_human_approval": False,
            "human_feedback": None,
            "phase": WorkflowPhase.IDENTIFY_BC,
            "messages": [
                HumanMessage(content=feedback),
                AIMessage(content=f"I'll revise the Bounded Contexts based on your feedback: {feedback}"),
            ],
        }
    else:
        # Still waiting for approval
        return {"awaiting_human_approval": True}


# =============================================================================
# User Story Breakdown
# =============================================================================


def breakdown_user_story_node(state: EventStormingState) -> Dict[str, Any]:
    """Break down user stories within the current Bounded Context."""
    if state.current_bc_index >= len(state.approved_bcs):
        # All BCs processed, move to aggregate extraction
        return {
            "phase": WorkflowPhase.EXTRACT_AGGREGATES,
            "current_bc_index": 0,
        }

    current_bc = state.approved_bcs[state.current_bc_index]
    llm = get_llm()

    # Get user stories for this BC
    bc_stories = [
        us for us in state.user_stories if us["id"] in current_bc.user_story_ids
    ]

    breakdowns = []

    for us in bc_stories:
        user_story_text = format_user_story(us)
        prompt = BREAKDOWN_USER_STORY_PROMPT.format(
            user_story=f"[{us['id']}] {user_story_text}",
            bc_name=current_bc.name,
        )

        structured_llm = llm.with_structured_output(UserStoryBreakdown)

        response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
        # Ensure correct ID
        response.user_story_id = us["id"]
        breakdowns.append(response)

    # Move to next BC
    return {
        "breakdowns": state.breakdowns + breakdowns,
        "current_bc_index": state.current_bc_index + 1,
        "messages": [
            AIMessage(
                content=f"Analyzed {len(breakdowns)} user stories in BC '{current_bc.name}'. Moving to next BC..."
            )
        ],
    }


# =============================================================================
# Aggregate Extraction
# =============================================================================


def extract_aggregates_node(state: EventStormingState) -> Dict[str, Any]:
    """Extract Aggregates for each Bounded Context."""
    if state.current_bc_index >= len(state.approved_bcs):
        return {
            "phase": WorkflowPhase.APPROVE_AGGREGATES,
            "awaiting_human_approval": True,
            "current_bc_index": 0,
        }

    current_bc = state.approved_bcs[state.current_bc_index]
    llm = get_llm()

    # Get breakdowns for this BC
    bc_breakdowns = [
        bd for bd in state.breakdowns if bd.user_story_id in current_bc.user_story_ids
    ]

    breakdowns_text = "\n".join(
        [
            f"User Story: {bd.user_story_id}\n"
            f"  Sub-tasks: {', '.join(bd.sub_tasks)}\n"
            f"  Domain Concepts: {', '.join(bd.domain_concepts)}\n"
            f"  Potential Aggregates: {', '.join(bd.potential_aggregates)}"
            for bd in bc_breakdowns
        ]
    )

    # Extract short BC ID for aggregate naming (e.g., BC-ORDER -> ORDER)
    bc_id_short = current_bc.id.replace("BC-", "")

    prompt = EXTRACT_AGGREGATES_PROMPT.format(
        bc_name=current_bc.name,
        bc_id=current_bc.id,
        bc_id_short=bc_id_short,
        bc_description=current_bc.description,
        breakdowns=breakdowns_text,
    )

    structured_llm = llm.with_structured_output(AggregateList)

    response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
    aggregates = response.aggregates

    # Store aggregates for this BC
    aggregate_candidates = dict(state.aggregate_candidates)
    aggregate_candidates[current_bc.id] = aggregates

    return {
        "aggregate_candidates": aggregate_candidates,
        "current_bc_index": state.current_bc_index + 1,
        "messages": [
            AIMessage(
                content=f"Identified {len(aggregates)} aggregates for BC '{current_bc.name}'."
            )
        ],
    }


def approve_aggregates_node(state: EventStormingState) -> Dict[str, Any]:
    """Process human approval for Aggregates."""
    feedback = state.human_feedback

    if feedback and feedback.upper() == "APPROVED":
        return {
            "approved_aggregates": state.aggregate_candidates,
            "awaiting_human_approval": False,
            "human_feedback": None,
            "phase": WorkflowPhase.EXTRACT_COMMANDS,
            "messages": [
                HumanMessage(content="APPROVED"),
                AIMessage(content="Aggregates approved! Extracting commands..."),
            ],
        }
    elif feedback:
        return {
            "awaiting_human_approval": False,
            "human_feedback": None,
            "phase": WorkflowPhase.EXTRACT_AGGREGATES,
            "current_bc_index": 0,
            "messages": [
                HumanMessage(content=feedback),
                AIMessage(content=f"I'll revise the Aggregates based on your feedback: {feedback}"),
            ],
        }
    else:
        # Display aggregates for review
        agg_text = ""
        for bc_id, aggregates in state.aggregate_candidates.items():
            bc = next((bc for bc in state.approved_bcs if bc.id == bc_id), None)
            bc_name = bc.name if bc else bc_id
            agg_text += f"\n{bc_name}:\n"
            for agg in aggregates:
                agg_text += f"  - {agg.name}: {agg.description}\n"
                agg_text += f"    Invariants: {', '.join(agg.invariants)}\n"

        return {
            "awaiting_human_approval": True,
            "messages": [
                AIMessage(
                    content=f"Please review the proposed Aggregates:\n{agg_text}\n\nType 'APPROVED' or provide feedback."
                )
            ],
        }


# =============================================================================
# Command Extraction
# =============================================================================


def extract_commands_node(state: EventStormingState) -> Dict[str, Any]:
    """Extract Commands for each Aggregate."""
    llm = get_llm()
    command_candidates = dict(state.command_candidates)

    for bc_id, aggregates in state.approved_aggregates.items():
        bc = next((bc for bc in state.approved_bcs if bc.id == bc_id), None)
        if not bc:
            continue

        bc_id_short = bc.id.replace("BC-", "")

        for agg in aggregates:
            # Get user stories that this aggregate implements
            agg_story_ids = agg.user_story_ids if agg.user_story_ids else bc.user_story_ids
            agg_stories = [us for us in state.user_stories if us["id"] in agg_story_ids]
            stories_context = "\n".join([
                f"[{us['id']}] As a {us.get('role', 'user')}, I want to {us.get('action', '?')}"
                for us in agg_stories
            ])

            prompt = EXTRACT_COMMANDS_PROMPT.format(
                aggregate_name=agg.name,
                aggregate_id=agg.id,
                bc_name=bc.name,
                bc_short=bc_id_short,
                user_story_context=stories_context,
            )

            structured_llm = llm.with_structured_output(CommandList)

            response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
            command_candidates[agg.id] = response.commands

    return {
        "command_candidates": command_candidates,
        "phase": WorkflowPhase.EXTRACT_EVENTS,
        "messages": [
            AIMessage(
                content=f"Extracted commands for all aggregates. Moving to event extraction..."
            )
        ],
    }


# =============================================================================
# Event Extraction
# =============================================================================


def extract_events_node(state: EventStormingState) -> Dict[str, Any]:
    """Extract Events for each Command."""
    llm = get_llm()
    event_candidates = dict(state.event_candidates)

    for agg_id, commands in state.command_candidates.items():
        # Include user story IDs in command context
        commands_text = "\n".join([
            f"- {cmd.name} (implements: {cmd.user_story_ids}): {cmd.description}"
            for cmd in commands
        ])

        # Find aggregate name and BC
        agg_name = agg_id
        bc_name = ""
        bc_short = ""
        for bc_id, aggregates in state.approved_aggregates.items():
            for agg in aggregates:
                if agg.id == agg_id:
                    agg_name = agg.name
                    bc = next((b for b in state.approved_bcs if b.id == bc_id), None)
                    if bc:
                        bc_name = bc.name
                        bc_short = bc.id.replace("BC-", "")
                    break

        prompt = EXTRACT_EVENTS_PROMPT.format(
            aggregate_name=agg_name,
            bc_name=bc_name,
            bc_short=bc_short,
            commands=commands_text,
        )

        structured_llm = llm.with_structured_output(EventList)

        response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
        event_candidates[agg_id] = response.events

    return {
        "event_candidates": event_candidates,
        "phase": WorkflowPhase.IDENTIFY_POLICIES,
        "messages": [
            AIMessage(
                content="Extracted events for all commands. Identifying cross-BC policies..."
            )
        ],
    }


# =============================================================================
# Policy Identification
# =============================================================================


def identify_policies_node(state: EventStormingState) -> Dict[str, Any]:
    """Identify Policies for cross-BC communication."""
    llm = get_llm()

    # Collect all events
    all_events = []
    for agg_id, events in state.event_candidates.items():
        # Find which BC this aggregate belongs to
        bc_name = "Unknown"
        for bc_id, aggregates in state.approved_aggregates.items():
            for agg in aggregates:
                if agg.id == agg_id:
                    bc = next((b for b in state.approved_bcs if b.id == bc_id), None)
                    bc_name = bc.name if bc else bc_id
                    break

        for evt in events:
            all_events.append(f"- {evt.name} (from {bc_name}): {evt.description}")

    events_text = "\n".join(all_events)

    # Collect commands by BC
    commands_by_bc = {}
    for bc in state.approved_bcs:
        bc_commands = []
        for agg_id, aggregates in state.approved_aggregates.items():
            if agg_id == bc.id or any(a.id.startswith(bc.id.replace("BC-", "AGG-")) for a in aggregates):
                continue
        # Get commands for aggregates in this BC
        for agg_id, commands in state.command_candidates.items():
            for aggregates in state.approved_aggregates.get(bc.id, []):
                if agg_id == aggregates.id:
                    bc_commands.extend([f"- {cmd.name}: {cmd.description}" for cmd in commands])
        if not bc_commands:
            # Fallback: collect all commands for this BC's aggregates
            for agg in state.approved_aggregates.get(bc.id, []):
                for cmd in state.command_candidates.get(agg.id, []):
                    bc_commands.append(f"- {cmd.name}: {cmd.description}")
        commands_by_bc[bc.name] = "\n".join(bc_commands) if bc_commands else "No commands"

    commands_text = "\n".join(
        [f"{bc_name}:\n{cmds}" for bc_name, cmds in commands_by_bc.items()]
    )

    bc_text = "\n".join(
        [f"- {bc.name}: {bc.description}" for bc in state.approved_bcs]
    )

    prompt = IDENTIFY_POLICIES_PROMPT.format(
        events=events_text,
        commands_by_bc=commands_text,
        bounded_contexts=bc_text,
    )

    structured_llm = llm.with_structured_output(PolicyList)

    response = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
    policies = response.policies

    return {
        "policy_candidates": policies,
        "phase": WorkflowPhase.APPROVE_POLICIES,
        "awaiting_human_approval": True,
        "messages": [
            AIMessage(
                content=f"Identified {len(policies)} cross-BC policies. Please review..."
            )
        ],
    }


def approve_policies_node(state: EventStormingState) -> Dict[str, Any]:
    """Process human approval for Policies."""
    feedback = state.human_feedback

    if feedback and feedback.upper() == "APPROVED":
        return {
            "approved_policies": state.policy_candidates,
            "awaiting_human_approval": False,
            "human_feedback": None,
            "phase": WorkflowPhase.SAVE_TO_GRAPH,
            "messages": [
                HumanMessage(content="APPROVED"),
                AIMessage(content="Policies approved! Saving everything to Neo4j..."),
            ],
        }
    elif feedback:
        return {
            "awaiting_human_approval": False,
            "human_feedback": None,
            "phase": WorkflowPhase.IDENTIFY_POLICIES,
            "messages": [
                HumanMessage(content=feedback),
                AIMessage(content=f"I'll revise the Policies based on your feedback: {feedback}"),
            ],
        }
    else:
        # Display policies for review
        pol_text = "\n".join(
            [
                f"- {pol.name}\n  When: {pol.trigger_event} → Then: {pol.invoke_command} (in {pol.target_bc})"
                for pol in state.policy_candidates
            ]
        )

        return {
            "awaiting_human_approval": True,
            "messages": [
                AIMessage(
                    content=f"Please review the proposed Policies:\n\n{pol_text}\n\nType 'APPROVED' or provide feedback."
                )
            ],
        }


# =============================================================================
# Save to Neo4j
# =============================================================================


def save_to_graph_node(state: EventStormingState) -> Dict[str, Any]:
    """Save all generated artifacts to Neo4j."""
    client = get_neo4j_client()
    saved_items = []

    try:
        # 1. Create Bounded Contexts
        for bc in state.approved_bcs:
            client.create_bounded_context(
                id=bc.id,
                name=bc.name,
                description=bc.description,
            )
            saved_items.append(f"BC: {bc.name}")

            # Link user stories to BC
            for us_id in bc.user_story_ids:
                client.link_user_story_to_bc(us_id, bc.id)

        # 2. Create Aggregates and link to User Stories
        for bc_id, aggregates in state.approved_aggregates.items():
            for agg in aggregates:
                client.create_aggregate(
                    id=agg.id,
                    name=agg.name,
                    bc_id=bc_id,
                    root_entity=agg.root_entity,
                    invariants=agg.invariants,
                )
                saved_items.append(f"  Aggregate: {agg.name}")

                # Link user stories to aggregate (IMPLEMENTS)
                for us_id in agg.user_story_ids:
                    try:
                        client.link_user_story_to_aggregate(us_id, agg.id)
                    except Exception:
                        pass  # User story might not exist

        # 3. Create Commands and link to User Stories
        for agg_id, commands in state.command_candidates.items():
            for cmd in commands:
                client.create_command(
                    id=cmd.id,
                    name=cmd.name,
                    aggregate_id=agg_id,
                    actor=cmd.actor,
                )
                saved_items.append(f"    Command: {cmd.name}")

                # Link user stories to command (IMPLEMENTS)
                for us_id in cmd.user_story_ids:
                    try:
                        client.link_user_story_to_command(us_id, cmd.id)
                    except Exception:
                        pass  # User story might not exist

        # 4. Create Events and link to User Stories
        for agg_id, events in state.event_candidates.items():
            commands = state.command_candidates.get(agg_id, [])
            for i, evt in enumerate(events):
                # Link to corresponding command if available
                cmd_id = commands[i].id if i < len(commands) else commands[0].id if commands else None
                if cmd_id:
                    client.create_event(
                        id=evt.id,
                        name=evt.name,
                        command_id=cmd_id,
                    )
                    saved_items.append(f"      Event: {evt.name}")

                    # Link user stories to event (IMPLEMENTS)
                    for us_id in evt.user_story_ids:
                        try:
                            client.link_user_story_to_event(us_id, evt.id)
                        except Exception:
                            pass  # User story might not exist

        # 5. Create Policies
        for pol in state.approved_policies:
            # Find the event and command IDs
            trigger_event_id = None
            invoke_command_id = None
            target_bc_id = None

            # Find event ID
            for events in state.event_candidates.values():
                for evt in events:
                    if evt.name == pol.trigger_event:
                        trigger_event_id = evt.id
                        break

            # Find command ID and BC ID
            for bc in state.approved_bcs:
                if bc.name == pol.target_bc or bc.id == pol.target_bc:
                    target_bc_id = bc.id
                    for agg in state.approved_aggregates.get(bc.id, []):
                        for cmd in state.command_candidates.get(agg.id, []):
                            if cmd.name == pol.invoke_command:
                                invoke_command_id = cmd.id
                                break

            if trigger_event_id and invoke_command_id and target_bc_id:
                client.create_policy(
                    id=pol.id,
                    name=pol.name,
                    bc_id=target_bc_id,
                    trigger_event_id=trigger_event_id,
                    invoke_command_id=invoke_command_id,
                    description=pol.description,
                )
                saved_items.append(f"  Policy: {pol.name}")

        return {
            "phase": WorkflowPhase.COMPLETE,
            "messages": [
                AIMessage(
                    content=f"✅ Successfully saved to Neo4j!\n\n"
                    f"Created items:\n" + "\n".join(saved_items) +
                    f"\n\nYou can now view the graph in Neo4j Browser at http://localhost:7474"
                )
            ],
        }

    except Exception as e:
        return {
            "phase": WorkflowPhase.COMPLETE,
            "error": str(e),
            "messages": [
                AIMessage(content=f"❌ Error saving to Neo4j: {str(e)}")
            ],
        }


# =============================================================================
# Routing Functions
# =============================================================================


def route_after_approval(state: EventStormingState) -> str:
    """Route based on whether we're waiting for human approval."""
    if state.awaiting_human_approval:
        return "wait_for_human"
    return "continue"


def route_by_phase(state: EventStormingState) -> str:
    """Route to the appropriate node based on current phase."""
    phase_routes = {
        WorkflowPhase.INIT: "init",
        WorkflowPhase.LOAD_USER_STORIES: "load_user_stories",
        WorkflowPhase.SELECT_USER_STORY: "select_user_story",
        WorkflowPhase.IDENTIFY_BC: "identify_bc",
        WorkflowPhase.APPROVE_BC: "approve_bc",
        WorkflowPhase.BREAKDOWN_USER_STORY: "breakdown_user_story",
        WorkflowPhase.EXTRACT_AGGREGATES: "extract_aggregates",
        WorkflowPhase.APPROVE_AGGREGATES: "approve_aggregates",
        WorkflowPhase.EXTRACT_COMMANDS: "extract_commands",
        WorkflowPhase.EXTRACT_EVENTS: "extract_events",
        WorkflowPhase.IDENTIFY_POLICIES: "identify_policies",
        WorkflowPhase.APPROVE_POLICIES: "approve_policies",
        WorkflowPhase.SAVE_TO_GRAPH: "save_to_graph",
        WorkflowPhase.COMPLETE: "complete",
    }
    return phase_routes.get(state.phase, "complete")

