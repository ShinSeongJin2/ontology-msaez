#!/usr/bin/env python3
"""
요구사항 텍스트에서 Event Storming 모델 생성

Usage:
    uv run python scripts/generate_from_requirements.py
    
또는 파일에서 읽기:
    uv run python scripts/generate_from_requirements.py --file requirements.txt
"""

import os
import sys
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

load_dotenv()

console = Console()


# =============================================================================
# Pydantic Models for LLM Output
# =============================================================================

class GeneratedUserStory(BaseModel):
    """Generated User Story from requirements."""
    id: str = Field(description="Unique ID like US-001")
    role: str = Field(description="User role (e.g., customer, seller, admin)")
    action: str = Field(description="What the user wants to do")
    benefit: str = Field(description="Why they want to do it")
    priority: str = Field(default="medium", description="Priority: high, medium, low")


class UserStoryList(BaseModel):
    """List of generated user stories."""
    user_stories: List[GeneratedUserStory] = Field(
        description="List of user stories extracted from requirements"
    )


# =============================================================================
# Requirements Parser
# =============================================================================

EXTRACT_USER_STORIES_PROMPT = """다음 요구사항 텍스트를 분석하여 User Story 목록을 추출하세요.

요구사항 텍스트:
{requirements}

지침:
1. 각 기능/요구사항을 독립적인 User Story로 변환
2. "As a [role], I want to [action], so that [benefit]" 형식 사용
3. 역할(role)은 구체적으로 (customer, seller, admin, system 등)
4. 액션(action)은 명확한 동사로 시작
5. 이점(benefit)은 비즈니스 가치 설명
6. 우선순위는 핵심 기능은 high, 부가 기능은 medium, 선택 기능은 low

User Story ID는 US-001, US-002 형식으로 순차적으로 부여하세요.
"""


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


def extract_user_stories(requirements_text: str) -> List[GeneratedUserStory]:
    """Extract user stories from requirements text using LLM."""
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = get_llm()
    structured_llm = llm.with_structured_output(UserStoryList)

    system_prompt = """당신은 도메인 주도 설계(DDD) 전문가입니다. 
요구사항을 User Story로 변환하는 작업을 수행합니다.
User Story는 명확하고 테스트 가능해야 합니다."""

    prompt = EXTRACT_USER_STORIES_PROMPT.format(requirements=requirements_text)

    response = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt)
    ])

    return response.user_stories


def save_user_stories_to_neo4j(user_stories: List[GeneratedUserStory]):
    """Save user stories to Neo4j."""
    from agent.neo4j_client import get_neo4j_client

    client = get_neo4j_client()
    saved = []

    for us in user_stories:
        result = client.create_user_story(
            id=us.id,
            role=us.role,
            action=us.action,
            benefit=us.benefit,
            priority=us.priority,
            status="draft"
        )
        saved.append(result)

    return saved


def run_event_storming_workflow():
    """Run the Event Storming workflow with auto-approval."""
    from agent.graph import EventStormingRunner
    from agent.state import WorkflowPhase

    runner = EventStormingRunner(thread_id="requirements-session")

    console.print("\n[bold cyan]🚀 Event Storming 워크플로우 시작...[/bold cyan]\n")

    state = runner.start()
    step = 0
    max_steps = 30

    while not runner.is_complete() and step < max_steps:
        state = runner.get_state()
        step += 1

        if state is None:
            break

        # Display progress
        if state.messages:
            last_msg = state.messages[-1]
            content = last_msg.content
            if len(content) > 200:
                content = content[:200] + "..."
            console.print(f"[dim]Step {step}:[/dim] {content}")

        if state.error:
            console.print(f"[bold red]Error: {state.error}[/bold red]")
            break

        # Auto-approve at each checkpoint
        if state.awaiting_human_approval:
            console.print(f"  [yellow]→ Phase: {state.phase.value} 자동 승인[/yellow]")
            state = runner.provide_feedback("APPROVED")
        else:
            break

    return runner.get_state()


def main():
    console.print("\n")
    console.print(Panel.fit(
        "[bold blue]📋 요구사항 기반 Event Storming 생성기[/bold blue]\n"
        "[dim]요구사항 텍스트에서 User Story를 추출하고 Event Storming 모델을 생성합니다[/dim]",
        border_style="blue"
    ))

    # Check for file argument
    requirements_text = None
    if len(sys.argv) > 2 and sys.argv[1] == "--file":
        filepath = sys.argv[2]
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                requirements_text = f.read()
            console.print(f"\n[green]✓ 파일에서 요구사항 로드: {filepath}[/green]")
        else:
            console.print(f"[red]파일을 찾을 수 없음: {filepath}[/red]")
            return 1

    # If no file, use sample or prompt
    if not requirements_text:
        console.print("\n[bold]샘플 요구사항을 사용하시겠습니까? (y/n)[/bold]")
        use_sample = Prompt.ask("선택", default="y")

        if use_sample.lower() == "y":
            requirements_text = """
# 온라인 쇼핑몰 요구사항

## 1. 주문 관리
- 고객은 상품을 장바구니에 담고 주문할 수 있어야 한다
- 고객은 주문을 취소할 수 있어야 한다 (배송 전까지)
- 고객은 주문 상태를 조회할 수 있어야 한다

## 2. 상품 관리
- 판매자는 상품을 등록할 수 있어야 한다
- 판매자는 상품 정보를 수정할 수 있어야 한다
- 판매자는 상품 재고를 관리할 수 있어야 한다

## 3. 결제 처리
- 시스템은 주문 시 결제를 처리해야 한다
- 주문 취소 시 자동으로 환불이 처리되어야 한다

## 4. 재고 관리
- 주문 시 재고가 자동으로 차감되어야 한다
- 주문 취소 시 재고가 복원되어야 한다

## 5. 알림
- 주문 완료 시 고객에게 이메일 알림을 보내야 한다
- 배송 시작 시 고객에게 알림을 보내야 한다
"""
        else:
            console.print("\n[bold]요구사항을 입력하세요 (빈 줄 두 번으로 종료):[/bold]")
            lines = []
            empty_count = 0
            while empty_count < 2:
                line = input()
                if line == "":
                    empty_count += 1
                else:
                    empty_count = 0
                lines.append(line)
            requirements_text = "\n".join(lines[:-2])  # Remove trailing empty lines

    console.print("\n[bold]요구사항 텍스트:[/bold]")
    console.print(Panel(requirements_text[:500] + "..." if len(requirements_text) > 500 else requirements_text))

    # Step 1: Extract User Stories
    console.print("\n[bold cyan]📝 Step 1: User Story 추출 중...[/bold cyan]")
    try:
        user_stories = extract_user_stories(requirements_text)
        console.print(f"[green]✓ {len(user_stories)}개의 User Story 추출 완료[/green]\n")

        # Display extracted stories
        table = Table(title="추출된 User Stories", show_header=True)
        table.add_column("ID", style="cyan")
        table.add_column("Role", style="green")
        table.add_column("Action")
        table.add_column("Priority", style="yellow")

        for us in user_stories:
            table.add_row(us.id, us.role, us.action[:40] + "..." if len(us.action) > 40 else us.action, us.priority)

        console.print(table)

    except Exception as e:
        console.print(f"[bold red]User Story 추출 실패: {e}[/bold red]")
        return 1

    # Step 2: Save to Neo4j
    console.print("\n[bold cyan]💾 Step 2: Neo4j에 저장 중...[/bold cyan]")
    try:
        saved = save_user_stories_to_neo4j(user_stories)
        console.print(f"[green]✓ {len(saved)}개의 User Story 저장 완료[/green]")
    except Exception as e:
        console.print(f"[bold red]Neo4j 저장 실패: {e}[/bold red]")
        return 1

    # Step 3: Run Event Storming workflow
    console.print("\n[bold cyan]🎯 Step 3: Event Storming 워크플로우 실행...[/bold cyan]")
    try:
        final_state = run_event_storming_workflow()

        if final_state:
            console.print("\n")
            console.print(Panel.fit(
                "[bold green]🎉 Event Storming 완료![/bold green]",
                border_style="green"
            ))

            # Summary
            console.print("\n[bold]결과 요약:[/bold]")
            console.print(f"  • Bounded Contexts: {len(final_state.approved_bcs)}")
            for bc in final_state.approved_bcs:
                console.print(f"    - {bc.name}: {bc.description[:50]}...")

            agg_count = sum(len(aggs) for aggs in final_state.approved_aggregates.values())
            console.print(f"  • Aggregates: {agg_count}")

            cmd_count = sum(len(cmds) for cmds in final_state.command_candidates.values())
            console.print(f"  • Commands: {cmd_count}")

            evt_count = sum(len(evts) for evts in final_state.event_candidates.values())
            console.print(f"  • Events: {evt_count}")

            console.print(f"  • Policies: {len(final_state.approved_policies)}")

            console.print("\n[dim]Neo4j Browser에서 확인: http://localhost:7474[/dim]")
            console.print("[dim]쿼리: MATCH (n) RETURN n LIMIT 100[/dim]")

    except Exception as e:
        console.print(f"[bold red]워크플로우 실행 실패: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

