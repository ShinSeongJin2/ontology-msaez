"""
Event Storming Node Runtime (LLM + audit logging)

Business capability: provide consistent LLM runtime + audit toggles for the Event Storming agent nodes.
Kept local to the `event_storming` feature implementation (not a global "service" layer).
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv


load_dotenv()


def _env_flag(key: str, default: bool = False) -> bool:
    val = (os.getenv(key) or "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


AI_AUDIT_LOG_ENABLED = _env_flag("AI_AUDIT_LOG_ENABLED", True)
AI_AUDIT_LOG_FULL_PROMPT = _env_flag("AI_AUDIT_LOG_FULL_PROMPT", False)
AI_AUDIT_LOG_FULL_OUTPUT = _env_flag("AI_AUDIT_LOG_FULL_OUTPUT", False)


def dump_model(obj: Any) -> Any:
    """Safely dump a pydantic model (v1/v2) for logging."""
    try:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
    except Exception:
        pass
    try:
        if hasattr(obj, "dict"):
            return obj.dict()
    except Exception:
        pass
    return {"__type__": type(obj).__name__, "__repr__": repr(obj)[:1000]}


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


