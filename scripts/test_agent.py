#!/usr/bin/env python3
"""
Test script for Event Storming Agent

This script demonstrates the LangGraph workflow without requiring
interactive input by auto-approving at each checkpoint.
"""

import os
import sys

# Ensure the agent package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from agent.graph import EventStormingRunner
from agent.state import WorkflowPhase

console = Console()


def main():
    console.print("\n")
    console.print(Panel.fit(
        "[bold blue]🧪 Event Storming Agent Test[/bold blue]\n"
        "[dim]Auto-approving at each checkpoint for demonstration[/dim]",
        border_style="blue"
    ))

    runner = EventStormingRunner(thread_id="test-session")

    try:
        # Start the workflow
        console.print("\n[bold cyan]Starting workflow...[/bold cyan]")
        state = runner.start()

        step = 0
        max_steps = 20  # Safety limit

        while not runner.is_complete() and step < max_steps:
            state = runner.get_state()
            step += 1

            if state is None:
                console.print("[red]State is None, breaking[/red]")
                break

            console.print(f"\n[bold]Step {step}: Phase = {state.phase.value}[/bold]")

            # Display the last message
            if state.messages:
                last_msg = state.messages[-1]
                content = last_msg.content
                if len(content) > 500:
                    content = content[:500] + "..."
                console.print(Panel(content, title="🤖 Agent", border_style="green"))

            # Check for errors
            if state.error:
                console.print(f"[bold red]Error: {state.error}[/bold red]")
                break

            # Auto-approve if waiting for human input
            if state.awaiting_human_approval:
                console.print("[yellow]✋ Auto-approving...[/yellow]")
                state = runner.provide_feedback("APPROVED")
            else:
                # No more steps needed
                break

        # Final result
        state = runner.get_state()
        if state and state.phase == WorkflowPhase.COMPLETE:
            console.print("\n")
            console.print(Panel.fit(
                "[bold green]✅ Test Complete![/bold green]",
                border_style="green"
            ))

            # Show summary
            console.print(f"\n[cyan]BCs identified:[/cyan] {len(state.approved_bcs)}")
            for bc in state.approved_bcs:
                console.print(f"  • {bc.name}: {bc.description[:50]}...")

            console.print(f"\n[cyan]Aggregates:[/cyan]")
            for bc_id, aggs in state.approved_aggregates.items():
                console.print(f"  {bc_id}:")
                for agg in aggs:
                    console.print(f"    • {agg.name}")

            console.print(f"\n[cyan]Commands:[/cyan] {sum(len(cmds) for cmds in state.command_candidates.values())}")
            console.print(f"[cyan]Events:[/cyan] {sum(len(evts) for evts in state.event_candidates.values())}")
            console.print(f"[cyan]Policies:[/cyan] {len(state.approved_policies)}")

        else:
            console.print(f"\n[yellow]Workflow ended at phase: {state.phase if state else 'Unknown'}[/yellow]")

    except Exception as e:
        console.print(f"\n[bold red]Error: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

